import concurrent.futures
import dataclasses
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil
import requests

from log import get_logger


ToolHandler = Callable[[Dict[str, Any]], Any]
LLMCompletionFn = Callable[..., Any]
InterimMessageCallback = Callable[..., Any]


DEFAULT_BOCHA_API_KEY = "sk-28078debe6d74828a57e63164fe0f23e"
logger = get_logger(__name__)


@dataclasses.dataclass
class RegisteredTool:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: ToolHandler

    def as_openai_schema(self) -> Dict[str, Any]:
        """将注册的工具转换为 OpenAI 要求的 functions/tools JSON schema 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclasses.dataclass
class ToolCallRequest:
    tool_call_id: str
    name: str
    arguments: Dict[str, Any]


@dataclasses.dataclass
class AgentRunResult:
    final_content: str
    messages: List[Dict[str, Any]]
    tool_rounds: int
    tool_errors: int
    tool_execution_records: List[Dict[str, Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, RegisteredTool] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: ToolHandler,
    ) -> None:
        """注册一个新的工具到工具注册表中。"""
        self._tools[name] = RegisteredTool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
        )

    def has_tool(self, name: str) -> bool:
        """检查是否已注册指定名称的工具。"""
        return name in self._tools

    def build_tools_schema(self) -> List[Dict[str, Any]]:
        """构建所有已注册工具的 OpenAPI schema 列表，供 LLM API 调用时传入。"""
        return [tool.as_openai_schema() for tool in self._tools.values()]

    def execute(self, request: ToolCallRequest) -> Tuple[bool, str]:
        """执行指定的工具调用请求，返回执行是否成功以及执行结果的字符串或JSON内容。"""
        tool = self._tools.get(request.name)
        if tool is None:
            return False, json.dumps(
                {
                    "ok": False,
                    "error": "tool_not_found",
                    "tool": request.name,
                },
                ensure_ascii=False,
            )

        try:
            result = tool.handler(request.arguments)
            if isinstance(result, str):
                content = result
            else:
                content = json.dumps(result, ensure_ascii=False)
            return True, content
        except Exception as exc:
            error_payload = {
                "ok": False,
                "error": "tool_execution_failed",
                "tool": request.name,
                "message": str(exc),
                "traceback": traceback.format_exc(limit=3),
            }
            return False, json.dumps(error_payload, ensure_ascii=False)

#-------------------------------  工具调用主循环的实现--------------------------------
class ToolCallingAgentRuntime:
    def __init__(
        self,
        llm_completion: LLMCompletionFn,
        tool_registry: Optional[ToolRegistry] = None,
        max_tool_rounds: int = 15,
        max_tool_errors: int = 3,
        debug: bool = True,
    ) -> None:
        self.llm_completion = llm_completion
        self.tool_registry = tool_registry or build_default_tool_registry()
        self.max_tool_rounds = max_tool_rounds
        self.max_tool_errors = max_tool_errors
        self.debug = debug
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    def _log(self, message: str) -> None:
        """内部日志打印方法，仅在调试模式开启时输出。"""
        if self.debug:
            logger.debug("[ToolCallingRuntime] %s", message)

    @staticmethod
    def _safe_json_dumps(data: Any) -> str:
        """安全地将数据序列化为 JSON 字符串，如果失败则返回其字符串表示。"""
        try:
            return json.dumps(data, ensure_ascii=False, indent=2)
        except TypeError:
            return str(data)

    def _serialize_response(self, response: Any) -> str:
        """统一序列化 LLM 的各种响应对象（如 LiteLLM ModelResponse 或字典）为 JSON 字符串。"""
        # 统一把 LiteLLM 响应转成字符串落盘，避免调试时看不到完整返回内容。
        if isinstance(response, dict):
            return self._safe_json_dumps(response)
        if hasattr(response, "model_dump"):
            try:
                return self._safe_json_dumps(response.model_dump())
            except Exception:
                pass
        if hasattr(response, "dict"):
            try:
                return self._safe_json_dumps(response.dict())
            except Exception:
                pass
        if hasattr(response, "json"):
            try:
                return str(response.json())
            except Exception:
                pass
        return str(response)

    def run(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        llm_kwargs: Optional[Dict[str, Any]] = None,
        on_interim_message: Optional[InterimMessageCallback] = None,
    ) -> AgentRunResult:
        """
        执行 Agent 工具调用主循环（简化版的 ReAct 模式）。

        不断向大模型请求回复，若返回需要调用工具，则本地执行工具逻辑，
        并将结果拼接入历史上下文中进行下一轮请求；
        直到大模型直接回复最终文本结果或是达到最大调用次数为止。
        """
        # history 会在工具循环中不断追加 assistant/tool 消息，形成完整闭环上下文。
        # 这是一个 Agent 工具调用主循环（ReAct 模式的简化），不断询问 LLM，直到 LLM 不再输出 tool_calls 为止，或达到最大循环次数。
        history: List[Dict[str, Any]] = list(messages)
        tool_rounds = 0
        tool_errors = 0
        tool_execution_records: List[Dict[str, Any]] = []
        pending_interim_future: Optional[concurrent.futures.Future] = None

        while True:
            if tool_rounds >= self.max_tool_rounds:
                break
            if tool_errors >= self.max_tool_errors:
                break

            response = self._call_llm(
                model=model,
                messages=history,
                llm_kwargs=llm_kwargs or {},
            )
            #print("LLM原始响应内容：\n" + self._serialize_response(response) + "\n--- 以上是完整响应内容 ---")
            parsed = self._parse_llm_response(response)
            # 如果之前的“过渡台词（期间文本）”语音仍在播放，而下一轮模型响应已经到达，
            # 则在这里阻塞等待，避免打断当前的 TTS 语音播放。
            # 这是为了让角色在调用工具等待时间可以先说一句话（比如“稍等，我查一下”）。
            if pending_interim_future is not None:
                pending_interim_future.result()
                pending_interim_future = None

            tool_calls = parsed.get("tool_calls", [])
            interim_text = parsed.get("interim_message")
            interim_is_placeholder = False
            if tool_calls and not interim_text:
                interim_text = "..."
                interim_is_placeholder = True
            if interim_text and on_interim_message is not None:
                # 异步执行回调（这通常会把文本塞进队列，触发 TTS 和前端更新），并保存句柄
                def _invoke_callback():
                    try:
                        return on_interim_message(interim_text, tool_calls, interim_is_placeholder)
                    except TypeError:
                        try:
                            return on_interim_message(interim_text, tool_calls)
                        except TypeError:
                            return on_interim_message(interim_text)

                pending_interim_future = self._executor.submit(_invoke_callback)

            # 如果模型没有输出需要调用的工具，说明本轮为最终回复，直接结束循环退回 final_content
            if not tool_calls:
                final_content = parsed.get("final_content", "")
                if pending_interim_future is not None:
                    pending_interim_future.result()
                return AgentRunResult(
                    final_content=final_content,
                    messages=history,
                    tool_rounds=tool_rounds,
                    tool_errors=tool_errors,
                    tool_execution_records=tool_execution_records,
                )

            assistant_message = {
                "role": "assistant",
                "content": parsed.get("assistant_content", ""),
                "tool_calls": [
                    {
                        "id": call.tool_call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in tool_calls
                ],
            }

            # DeepSeek V4 在启用思考模式进行工具调用时要求必须返回 reasoning_content
            reasoning_content = parsed.get("reasoning_content")
            if (
                reasoning_content
                and self._should_return_reasoning_content(parsed.get("model"))
            ):
                # 按照 DeepSeek V4 文档的要求，在 assistant 消息中附加 reasoning_content 字段。
                assistant_message["reasoning_content"] = reasoning_content

            history.append(assistant_message)

            # 遍历并执行所有工具调用，将结果转化为 tool 消息压入历史中供下一轮循环读取
            for call in tool_calls:
                # 抽签窗口属于强交互型 UI，优先等待过渡文本播报完成后再弹出，避免体验割裂。
                if call.name == "start_lottery" and pending_interim_future is not None:
                    pending_interim_future.result()
                    pending_interim_future = None

                started_at = time.time()
                started_perf = time.perf_counter()
                ok, tool_output = self.tool_registry.execute(call)
                duration_sec = max(0.0, time.perf_counter() - started_perf)
                if not ok:
                    tool_errors += 1

                tool_execution_records.append(
                    {
                        "tool_call_id": call.tool_call_id,
                        "tool_name": call.name,
                        "arguments": call.arguments,
                        "ok": ok,
                        "duration_sec": round(duration_sec, 3),
                        "started_at": int(started_at),
                        "result_content": self._format_tool_output_for_display(call.name, tool_output),
                    }
                )

                self._log(
                    "工具调用结果 raw json:\n"
                    + self._safe_json_dumps(
                        {
                            "name": call.name,
                            "arguments": call.arguments,
                            "ok": ok,
                            "content": tool_output,
                        }
                    )
                )

                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.tool_call_id,
                        "name": call.name,
                        "content": tool_output,
                    }
                )

            tool_rounds += 1

        if pending_interim_future is not None:
            pending_interim_future.result()

        return AgentRunResult(
            final_content=json.dumps(
                {
                    "error": "tool_loop_terminated",
                    "tool_rounds": tool_rounds,
                    "tool_errors": tool_errors,
                },
                ensure_ascii=False,
            ),
            messages=history,
            tool_rounds=tool_rounds,
            tool_errors=tool_errors,
            tool_execution_records=tool_execution_records,
        )

    def _call_llm(self, model: str, messages: List[Dict[str, Any]], llm_kwargs: Dict[str, Any]) -> Any:
        """封装底层LiteLLM的接口请求，带上注册好的可用工具 Schema 以及当前聊天历史发送请求。"""
        kwargs = {
            "model": model,
            "messages": messages,
            "tools": self.tool_registry.build_tools_schema(),
            "tool_choice": "auto",
        }
        kwargs.update(llm_kwargs)
        # 只输出本轮请求摘要，不打印完整聊天历史，避免刷屏。
        messages_tail = messages[-2:] if len(messages) > 2 else messages
        request_preview = {
            "model": kwargs.get("model"),
            "tool_choice": kwargs.get("tool_choice"),
            "tools": [
                ((one.get("function") or {}).get("name") if isinstance(one, dict) else str(one))
                for one in (kwargs.get("tools") or [])
            ],
            "messages_tail": messages_tail,
        }
        self._log("发送给LLM的请求 raw json(截断版):\n" + self._safe_json_dumps(request_preview))
        return self.llm_completion(**kwargs)

    @staticmethod
    def _format_tool_output_for_display(tool_name: str, tool_output: str) -> str:
        """格式化工具结果的输出文本，展示在UI窗口框中，具体可见qtUI.ToolCallInfoDialog。
        避免在 UI 或日志呈现时显示长串丑陋混乱（或带大段无用字段）的 JSON 字符串。"""
        def _truncate(text: str, limit: int = 1800) -> str:
            text = text.strip()
            if len(text) <= limit:
                return text
            return text[:limit] + "\n...(已截断)"

        try:
            payload = json.loads(tool_output)
        except Exception:
            return _truncate(str(tool_output))

        if isinstance(payload, dict):
            if tool_name == "web_search":
                items = payload.get("items") or payload.get("results") or []
                if isinstance(items, list) and items:
                    lines: List[str] = []
                    for idx, item in enumerate(items[:5], start=1):
                        if not isinstance(item, dict):
                            continue
                        title = str(item.get("title") or item.get("name") or "(无标题)")
                        url = str(item.get("url") or item.get("link") or "")
                        snippet = str(item.get("snippet") or item.get("description") or "")
                        line = f"{idx}. {title}"
                        if url:
                            line += f"\n   {url}"
                        if snippet:
                            line += f"\n   {snippet}"
                        lines.append(line)
                    if lines:
                        return _truncate("\n".join(lines))

            if tool_name == "get_weather":
                city = payload.get("city") or payload.get("location") or ""
                temp = payload.get("temperature") or payload.get("temp_c") or payload.get("temp")
                desc = payload.get("weather") or payload.get("description") or payload.get("text")
                parts: List[str] = []
                if city:
                    parts.append(f"城市: {city}")
                if temp is not None:
                    parts.append(f"温度: {temp}")
                if desc:
                    parts.append(f"天气: {desc}")
                if parts:
                    return "\n".join(parts)

            if tool_name == "get_current_datetime":
                formatted = payload.get("formatted") or payload.get("iso")
                timezone = payload.get("timezone")
                if formatted:
                    if timezone:
                        return f"当前时间: {formatted}\n时区: {timezone}"
                    return f"当前时间: {formatted}"

            if tool_name == "get_system_hardware_status":
                cpu = payload.get("cpu_percent")
                memory = payload.get("memory")
                gpu = payload.get("gpu")
                lines: List[str] = []
                if cpu is not None:
                    lines.append(f"CPU占用: {cpu}%")
                if isinstance(memory, dict):
                    used = memory.get("used_percent")
                    if used is not None:
                        lines.append(f"内存占用: {used}%")
                if isinstance(gpu, dict):
                    temp = gpu.get("temperature")
                    if temp is not None:
                        lines.append(f"GPU温度: {temp}")
                if lines:
                    return "\n".join(lines)

            if tool_name == "list_directory":
                dir_path = payload.get("dir_path") or ""
                entries = payload.get("entries") or []
                if isinstance(entries, list):
                    lines: List[str] = [f"{dir_path}  ({len(entries)} 项)"]
                    for entry in entries[:30]:
                        if not isinstance(entry, dict):
                            continue
                        name = entry.get("name", "")
                        size = entry.get("size_display", "")
                        line = f"{name}"
                        if size:
                            line += f"  ({size})"
                        lines.append(line)
                    if len(entries) > 30:
                        lines.append(f"... 还有 {len(entries) - 30} 项")
                    return _truncate("\n".join(lines))

            if tool_name == "read_file_content":
                file_path = payload.get("file_path") or ""
                total = payload.get("total_lines", "?")
                s = payload.get("start_line", "?")
                e = payload.get("end_line", "?")
                content = payload.get("content") or ""
                header = f"{os.path.basename(file_path)}  (行 {s}-{e} / 共 {total} 行)"
                hint = payload.get("hint") or ""
                text = f"{header}\n{'─' * 40}\n{content}"
                if hint:
                    text += f"\n{'─' * 40}\n{hint}"
                return _truncate(text, limit=2000)

            if tool_name == "grep_search_in_file":
                file_path = payload.get("file_path") or ""
                keyword = payload.get("keyword") or ""
                match_count = payload.get("match_count", 0)
                matches_list = payload.get("matches") or []
                lines: List[str] = [f"在 {os.path.basename(file_path)} 中搜索「{keyword}」— 共 {match_count} 处匹配"]
                for m in matches_list[:10]:
                    if not isinstance(m, dict):
                        continue
                    if m.get("truncated"):
                        lines.append(f"\n{m.get('message', '结果已截断')}")
                        break
                    ml = m.get("match_line", "?")
                    cs = m.get("context_start", "?")
                    ce = m.get("context_end", "?")
                    snippet = m.get("snippet", "")
                    lines.append(f"\n--- 第 {ml} 行 (上下文 {cs}-{ce}) ---")
                    lines.append(snippet.rstrip())
                return _truncate("\n".join(lines), limit=2000)

            if tool_name == "export_document":
                file_path = payload.get("file_path") or ""
                message = payload.get("message") or ""
                return f"成功导出文档\n  文件路径: {file_path}\n  {message}"

        return _truncate(json.dumps(payload, ensure_ascii=False, indent=2))

    @staticmethod
    def _should_return_reasoning_content(model: Any) -> bool:
        """
        DeepSeek V4 在启用思考模式进行工具调用时要求必须返回 reasoning_content.
        此函数检查当前请求的模型是否为 deepseek 系列。
        """
        model_text = str(model or "").lower()
        return "deepseek" in model_text

    @staticmethod
    def _parse_llm_response(response: Any) -> Dict[str, Any]:
        """
        统一解析支持多后端返回内容格式差异（字典/对象），
        提取助理回复内容（assistant_content）、期间文本（interim_message）及工具调用请求（tool_calls）供架构处理。
        """
        # 兼容 dict / LiteLLM ModelResponse 两种返回结构。
        message: Any = None
        finish_reason: Optional[str] = None
        model: Optional[str] = None

        if isinstance(response, dict):
            model = response.get("model")
            choices = response.get("choices") or []
            if choices:
                first_choice = choices[0]
                if isinstance(first_choice, dict):
                    message = first_choice.get("message")
                    finish_reason = first_choice.get("finish_reason")
        else:
            model = getattr(response, "model", None)
            choices = getattr(response, "choices", None)
            if choices:
                first_choice = choices[0]
                message = getattr(first_choice, "message", None)
                finish_reason = getattr(first_choice, "finish_reason", None)

        content = ""
        reasoning_content = ""
        raw_tool_calls: List[Any] = []

        if isinstance(message, dict):
            content = message.get("content") or ""
            reasoning_content = message.get("reasoning_content") or ""
            raw_tool_calls = message.get("tool_calls") or []
        elif message is not None:
            content = getattr(message, "content", "") or ""
            reasoning_content = getattr(message, "reasoning_content", "") or ""
            raw_tool_calls = getattr(message, "tool_calls", None) or []

        parsed_from_content = ToolCallingAgentRuntime._parse_content_json(content)

        tool_calls: List[ToolCallRequest] = []
        if raw_tool_calls:
            tool_calls.extend(ToolCallingAgentRuntime._normalize_tool_calls(raw_tool_calls))
        elif parsed_from_content.get("tool_calls"):
            # 兼容 message + tool_call 的 JSON 文本协议。
            tool_calls.extend(parsed_from_content["tool_calls"])

        is_tool_finish = finish_reason == "tool_calls"

        interim_message = parsed_from_content.get("interim_message", "")
        # 有些模型会在 tool_calls 同时把“期间文本”直接放在 content 中，而不是 message 字段。
        # 这里做兜底，确保期间文本能被前端与语音链路消费。
        if tool_calls and not interim_message and content.strip():
            interim_message = content.strip()

        return {
            "assistant_content": parsed_from_content.get("assistant_content", content),
            "reasoning_content": reasoning_content,
            "interim_message": interim_message,
            "tool_calls": tool_calls if (tool_calls or is_tool_finish) else [],
            "final_content": parsed_from_content.get("final_content", content),
            "finish_reason": finish_reason,
            "model": model,
        }

    @staticmethod
    def _parse_content_json(content: str) -> Dict[str, Any]:
        """将模型文本回复内容进行解析，提取潜在包裹在 JSON 结构（如 [{text, translation, emotion...}]）的数据或是中间过渡文本等信息。"""
        content = (content or "").strip()
        if not content:
            return {
                "assistant_content": "",
                "interim_message": "",
                "tool_calls": [],
                "final_content": "",
            }

        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return {
                "assistant_content": content,
                "interim_message": "",
                "tool_calls": [],
                "final_content": content,
            }

        if isinstance(payload, list):
            # 兼容“普通回复格式”作为期间文本：[{text, translation, emotion}, ...]
            normalized = ToolCallingAgentRuntime._normalize_structured_text_payload(payload)
            return {
                "assistant_content": normalized,
                "interim_message": "",
                "tool_calls": [],
                "final_content": normalized,
            }

        if not isinstance(payload, dict):
            return {
                "assistant_content": content,
                "interim_message": "",
                "tool_calls": [],
                "final_content": content,
            }

        # 若返回的是单对象普通回复格式，转换为数组格式以统一后续处理。
        if ToolCallingAgentRuntime._looks_like_single_segment_payload(payload):
            normalized = ToolCallingAgentRuntime._normalize_structured_text_payload([payload])
            return {
                "assistant_content": normalized,
                "interim_message": "",
                "tool_calls": [],
                "final_content": normalized,
            }

        interim_message = str(payload.get("message") or payload.get("interim_message") or "")
        final_content: str
        if "final" in payload:
            final_content = str(payload["final"])
        elif "response" in payload:
            final_content = str(payload["response"])
        else:
            final_content = content

        tool_calls = ToolCallingAgentRuntime._normalize_tool_calls_from_payload(payload)

        return {
            "assistant_content": interim_message,
            "interim_message": interim_message,
            "tool_calls": tool_calls,
            "final_content": final_content,
        }

    @staticmethod
    def _looks_like_single_segment_payload(payload: Dict[str, Any]) -> bool:
        """检查当前 JSON 字典是不是包含单独的对话切片所需的结构字典（有 text，伴随 translation 或 emotion）。"""
        return "text" in payload and ("emotion" in payload or "translation" in payload)

    @staticmethod
    def _normalize_structured_text_payload(items: List[Any]) -> str:
        """把解析切片出的对话回复信息再次转化成严格标准的基于数组格式的 JSON 字符串供主链路使用。"""
        normalized: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            one: Dict[str, Any] = {"text": text}
            if "translation" in item:
                one["translation"] = str(item.get("translation") or "")
            if "emotion" in item:
                one["emotion"] = str(item.get("emotion") or "neutral")
            normalized.append(one)

        if not normalized:
            return "[]"
        return json.dumps(normalized, ensure_ascii=False)

    @staticmethod
    def _normalize_tool_calls(raw_tool_calls: List[Any]) -> List[ToolCallRequest]:
        """将不同 LLM 返回的结构不一的 tool_calls 原生列表标准化重构成预定义的 ToolCallRequest。"""
        normalized: List[ToolCallRequest] = []

        for item in raw_tool_calls:
            if isinstance(item, dict):
                call_id = item.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                function_payload = item.get("function") or {}
                name = function_payload.get("name") or ""
                arguments_raw = function_payload.get("arguments") or "{}"
            else:
                call_id = getattr(item, "id", None) or f"call_{uuid.uuid4().hex[:8]}"
                function_payload = getattr(item, "function", None)
                name = getattr(function_payload, "name", "") if function_payload is not None else ""
                arguments_raw = getattr(function_payload, "arguments", "{}") if function_payload is not None else "{}"

            arguments = ToolCallingAgentRuntime._parse_arguments(arguments_raw)
            normalized.append(ToolCallRequest(tool_call_id=call_id, name=name, arguments=arguments))

        return normalized

    @staticmethod
    def _normalize_tool_calls_from_payload(payload: Dict[str, Any]) -> List[ToolCallRequest]:
        """专门从包含特定 `tool_call` 或者 `tool_calls` 字典结构的字段内重配为内部一致对象的转化函数（应对各种错误返回格式）。"""
        calls: List[Any] = []
        if "tool_call" in payload and isinstance(payload["tool_call"], dict):
            calls.append(payload["tool_call"])
        if "tool_calls" in payload and isinstance(payload["tool_calls"], list):
            calls.extend(payload["tool_calls"])

        normalized: List[ToolCallRequest] = []
        for item in calls:
            name = str(item.get("name") or item.get("function") or "")
            arguments_any = item.get("arguments") or {}
            arguments = ToolCallingAgentRuntime._parse_arguments(arguments_any)
            tool_call_id = str(item.get("id") or f"call_{uuid.uuid4().hex[:8]}")
            normalized.append(ToolCallRequest(tool_call_id=tool_call_id, name=name, arguments=arguments))
        return normalized

    @staticmethod
    def _parse_arguments(arguments_raw: Any) -> Dict[str, Any]:
        """将大模型传来的工具调用参数列表（可能会由于格式问题变为普通字符串或 JSON 字符串）反序列化为字典字典。"""
        if isinstance(arguments_raw, dict):
            return arguments_raw
        if isinstance(arguments_raw, str):
            text = arguments_raw.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return {}
        return {}

#-------------------------------- 注册所有工具函数--------------------------------
def build_default_tool_registry() -> ToolRegistry:
    """
    新建并返回带默认工具配置功能的工具注册表类（含网络天气、本地时间和系统配置等工具）。
    这些自带的基础工具保证了基础的系统检测体验扩展等特性。
    新增工具只需要在这个函数内注册即可。
    """
    registry = ToolRegistry()

    registry.register_tool(
        name="get_current_datetime",
        description="获取当前本地时间。当你想根据早中晚不同时段主动向用户发起不同话题，或者在设置提醒(set_reminder)前核对接下来的时间计划时，主动调用它来“看一眼表”。",
        parameters={
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "description": "Optional datetime strftime format.",
                }
            },
            "required": [],
        },
        handler=DateTimeTool.execute,
    )

    registry.register_tool(
        name="get_system_hardware_status",
        description="获取用户电脑的硬件占用（CPU、内存、GPU）。例如当用户提到电脑卡、玩游戏掉帧等话题时，可直接调用。",
        parameters={
            "type": "object",
            "properties": {
                "include_gpu": {
                    "type": "boolean",
                    "description": "Whether to query NVIDIA GPU metrics via nvidia-smi when available.",
                }
            },
            "required": [],
        },
        handler=SystemHardwareTool.execute,
    )

    registry.register_tool(
        name="web_search",
        description="【主动求知】使用Web搜索引擎查询信息。当你遇到不懂的网络热词、冷门动漫知识，或者用户提到你不知道的新闻时，**绝对不要说自己不知道，也不要盲猜**，你要立即主动调用搜索！甚至你可以只是为了找点有趣的新闻当话题，主动搜完分享给用户。",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query text.",
                },
                "provider": {
                    "type": "string",
                    "description": "Provider name: bocha, bing, tavily, brave, google.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max number of returned search results.",
                },
            },
            "required": ["query"],
        },
        handler=WebSearchTool.execute,
    )

    registry.register_tool(
        name="get_weather",
        description="获取用户所在地当前天气。当用户提到要出门、买东西、身体不舒服等时，可主动调用此工具，通过结果来组织你的回复。",
        parameters={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Optional city name. If omitted, detect from public IP.",
                }
            },
            "required": [],
        },
        handler=WeatherTool.execute,
    )

    # 文件阅读工具集（目录浏览 / 分页读取 / 关键字搜索）
    register_file_reading_tools(registry)

    return registry


def register_reminder_tool(
    registry: ToolRegistry,
    add_reminder_func: Callable[[str, float], bool]
) -> None:
    """注册全局的定时提醒工具（带持久化）。"""
    def _set_reminder_handler(arguments: Dict[str, Any]) -> Dict[str, Any]:
        target_datetime_str = arguments.get('target_datetime', '')
        content = arguments.get('content', '')
        if not target_datetime_str or not content:
            return {'ok': False, 'error': 'target_datetime 和 content 不能为空'}

        try:
            dt = datetime.datetime.strptime(target_datetime_str, "%Y-%m-%d %H:%M:%S")
            # 转为 Unix 时间戳落盘，方便后台管理线程换算或做跨时区迁移
            target_ts = dt.timestamp()
        except Exception as e:
            return {'ok': False, 'error': f'时间格式解析失败，请确保格式为 YYYY-MM-DD HH:MM:SS。详细错误: {e}'}

        import time
        if target_ts <= time.time():
            return {'ok': False, 'error': '设定的目标唤醒时间不能早于当前系统时间'}

        success = add_reminder_func(content, target_ts)
        if success:
            return {'ok': True, 'message': f'成功将事件：[{content}] 设定在 {target_datetime_str} 触发。'}
        else:
            return {'ok': False, 'error': '因系统原因添加失败。'}

    registry.register_tool(
        name='set_reminder',
        description='设定定时器，到期后系统会通知你。**不要只做定闹钟的工具人。**当用户说“我突然有点事，一小时后再聊”或者“我半小时后要出门”，你完全可以定个闹钟到时主动去找用户！再例如聊到午睡，你也可以主动说“那我帮你定个40分钟后的闹钟叫你吧”并调用此工具。必须先调取当前时间计算目标时间！',
        parameters={
            'type': 'object',
            'properties': {
                'target_datetime': {
                    'type': 'string',
                    'description': '具体的倒计时终点时刻，即目标响应事件的时间。格式必须严格为 YYYY-MM-DD HH:MM:SS。务必使用当前时间计算！'
                },
                'content': {
                    'type': 'string',
                    'description': '提醒的具体名目内容（事件），如"提醒用户该吃药了"'
                }
            },
            'required': ['target_datetime', 'content']
        },
        handler=_set_reminder_handler
    )


def register_lottery_tool(
    registry: ToolRegistry,
    show_lottery_ui_func: Callable[[str, List[str]], bool]
) -> None:
    """注册随机抽签工具，工具执行时通过依赖注入触发前台抽签窗口。"""
    def _start_lottery_handler(arguments: Dict[str, Any]) -> Dict[str, Any]:
        title = str(arguments.get("title") or "随机抽签").strip()
        options_raw = arguments.get("options") or []
        if not isinstance(options_raw, list):
            return {
                "ok": False,
                "error": "options 必须是字符串数组",
            }

        options: List[str] = []
        for one in options_raw:
            text = str(one).strip()
            if text:
                options.append(text)

        # 去重后至少保留两项，避免“抽签”退化为单选。
        options = list(dict.fromkeys(options))
        if len(options) < 2:
            return {
                "ok": False,
                "error": "抽签选项至少需要 2 个",
            }

        shown = show_lottery_ui_func(title, options)
        if not shown:
            return {
                "ok": False,
                "error": "前台抽签窗口创建失败",
            }

        return {
            "ok": True,
            "message": f"已向前台发送抽签窗口：{title}",
            "title": title,
            "options_count": len(options),
        }

    registry.register_tool(
        name="start_lottery",
        description="【互动利器】创建一个前台随机抽签窗口。**不要只等用户提到抽签才用！**当你想和用户找点乐子玩个游戏、决定某件小事（甚至只是“今天你夸我还是我夸你”）、当对话陷入平淡，或者你纯粹想给用户制造一个意想不到的惊喜与互动时，随时大胆地主动为你和用户抛出这个抽奖面板！",
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "抽签窗口标题，如：今天中午吃什么",
                },
                "options": {
                    "type": "array",
                    "description": "抽签候选项字符串列表，建议 2-12 项",
                    "items": {"type": "string"},
                },
            },
            "required": ["title", "options"],
        },
        handler=_start_lottery_handler,
    )


def register_live2d_tools(
    registry: ToolRegistry,
    get_char_folder_func: Callable[[], str],
    change_model_func: Callable[[str], None]
) -> None:
    """
    注册获取和切换前台角色 Live2D 模型的专用大模型工具。
    采用依赖注入方式传入获取角色名以及切换模型的执行回调。
    """
    def _fetch_all_live2d_models_handler(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """具体的获取可用 Live2D 模型列表的处理闭包函数。"""
        char_folder = get_char_folder_func()
        if char_folder == 'sakiko':
            return {
                "ok": False,
                "error": "祥子（sakiko）存在双重状态机制，不支持通过常规方式切换Live2D服装"
            }

        import glob
        import os
        base_path = os.path.normpath(os.path.join(os.path.dirname(__file__), '../../live2d_related', char_folder))
        default_live2d_json_list = glob.glob(os.path.join(base_path, 'live2D_model', '*.model.json'))
        default_live2d_json = default_live2d_json_list[0].replace("\\", "/") if default_live2d_json_list else None

        models = []
        if default_live2d_json:
            models.append({'model_name': '默认', 'model_json_path': default_live2d_json})

        extra_path = os.path.join(base_path, 'extra_model')
        if os.path.exists(extra_path):
            for model_dir in os.listdir(extra_path):
                dp = os.path.join(extra_path, model_dir)
                if os.path.isdir(dp):
                    jsons = glob.glob(os.path.join(dp, '*.model.json'))
                    if jsons:
                        models.append({'model_name': model_dir, 'model_json_path': jsons[0].replace("\\", "/")})

        return {'ok': True, 'character_folder': char_folder, 'models': models}

    def _change_character_live2d_handler(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """具体的执行前台 Live2D 模型切换效果的闭包回调执行器。"""
        target_path = arguments.get('model_json_path', '').strip()
        if not target_path:
            return {'ok': False, 'error': 'model_json_path 参数不能为空'}

        char_folder = get_char_folder_func()
        if char_folder == 'sakiko':
            return {
                "ok": False,
                "error": "祥子（sakiko）存在双重状态机制，不支持通过常规方式切换Live2D服装"
            }

        change_model_func(target_path)
        return {'ok': True, 'message': f'成功发送异步切换服装指令：{target_path}'}

    registry.register_tool(
        name='fetch_all_live2d_models',
        description='获取角色拥有的所有衣服/Live2D模型列表。**重点：不要等用户明确要求，你要视情况主动换装！**当你想给用户一个视觉惊喜，或者配合当前聊天气氛（如聊到睡觉、生病、出门等场景）时，请私下调用此工具翻看衣柜，准备换装。',
        parameters={'type': 'object', 'properties': {}, 'required': []},
        handler=_fetch_all_live2d_models_handler
    )

    registry.register_tool(
        name='change_character_live2d',
        description='【主动视觉表现】将角色的服装换为指定路径。**核心扮演动作：用换装制造惊喜，绝不被动等待！**不需要用户允许或要求，比如当用户夸你时主动换上特制装扮，聊到晚安主动换上睡衣。用突然发生的外观变化给用户惊喜感！(必须先调用 fetch_all_live2d_models 挑选合适的衣服)',
        parameters={
            'type': 'object',
            'properties': {'model_json_path': {'type': 'string', 'description': '需要切换到的 Live2D 模型对应的 json 文件绝对或相对路径（必填）'}},
            'required': ['model_json_path']
        },
        handler=_change_character_live2d_handler
    )

def register_file_reading_tools(registry: ToolRegistry) -> None:
    """将文件阅读工具集（目录浏览 / 分页读取 / 关键字搜索）注册到工具注册表。"""
    registry.register_tool(
        name="list_directory",
        description=(
            "列出指定目录下的所有文件和子文件夹。"
            "当用户让你看某个文件夹里有什么、或者你需要先了解项目结构再阅读代码时，调用此工具。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dir_path": {
                    "type": "string",
                    "description": "要浏览的目录路径（绝对路径或相对路径）",
                },
            },
            "required": ["dir_path"],
        },
        handler=FileReadingTools.list_directory,
    )

    registry.register_tool(
        name="read_file_content",
        description=(
            "读取指定纯文本/代码文件的内容。支持按行号分页读取，每次最多返回 200 行。"
            "若文件未读完，返回结果会包含提示信息和下一页的 start_line 值，你可以多次调用来逐页阅读大文件。"
            "当用户让你阅读、查看、分析某个文件内容时使用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要读取的文件路径（绝对路径或相对路径）",
                },
                "start_line": {
                    "type": "integer",
                    "description": "起始行号（从1开始，默认1）",
                },
                "end_line": {
                    "type": "integer",
                    "description": "结束行号（默认为 start_line + 199，即一次读200行）",
                },
            },
            "required": ["file_path"],
        },
        handler=FileReadingTools.read_file_content,
    )

    registry.register_tool(
        name="grep_search_in_file",
        description=(
            "在指定文件中搜索关键字，返回所有匹配行以及每处匹配前后各5行的上下文。"
            "适用于大文件中快速定位函数名、变量名、错误信息等，比逐页翻阅更高效。"
            "当用户让你在文件中找某个内容、或你需要快速定位代码位置时使用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要搜索的文件路径（绝对路径或相对路径）",
                },
                "keyword": {
                    "type": "string",
                    "description": "要搜索的关键字（大小写不敏感）",
                },
            },
            "required": ["file_path", "keyword"],
        },
        handler=FileReadingTools.grep_search_in_file,
    )

    registry.register_tool(
        name="export_document",
        description=(
            "【语音对话防刷屏-生成本地文档工具】"
            "当你需要向用户提供以下大段文字信息时，**必须**调用此工具将其保存为文档："
            "1. 小说概括、剧情总结、文章长篇大论的分析；"
            "2. 具体的代码修改意见、重构后的完整代码、长段报错日志；"
            "3. 任何包含 Markdown 复杂排版（表格、长列表）的内容。"
            "工具会将你整理好的内容直接生成文件放到软件的 tool_data 文件夹中，并会自动在用户电脑上弹窗打开该文件。"
            "调用此工具后，你在当轮聊天的回复可以是：'我把内容整理成文档发给你了，你可以打开看看哦'（或者符合人设的简洁回复），千万不要把详细内容复述一遍，因为在对话中直接发送长文本内容会造成刷屏且体验极差。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "要保存的文件名（如 '剧情总结.txt'、'修改建议.md'）。如果不提供绝对路径，将默认保存到软件的 tool_data 文件夹中。",
                },
                "content": {
                    "type": "string",
                    "description": "需要写入文档的具体内容正文，支持完整的 Markdown 格式排版。",
                },
            },
            "required": ["filename", "content"],
        },
        handler=FileReadingTools.export_document,
    )

#-------------------------------- 以下为各工具的具体实现--------------------------------
class DateTimeTool:
    @staticmethod
    def execute(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """具体的获取本地时间工具，包含返回默认 ISO 格式或者是指定的 strftime 格式的结果。"""
        now = datetime.datetime.now().astimezone()
        fmt = arguments.get("format")
        formatted = now.strftime(fmt) if isinstance(fmt, str) and fmt else now.isoformat()
        return {
            "ok": True,
            "timestamp": now.timestamp(),
            "iso": now.isoformat(),
            "formatted": formatted,
            "timezone": str(now.tzinfo),
        }


class SystemHardwareTool:
    @staticmethod
    def execute(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """具体的系统状态分析工具。获取主机设备的 CPU 占用情况、温度、显卡的详情和可运行内存记录等资源状态。"""
        include_gpu = bool(arguments.get("include_gpu", True))
        cpu_percent = psutil.cpu_percent(interval=0.2)
        memory = psutil.virtual_memory()

        # Windows 平台：优先 NVIDIA 查询，并尝试读取 CPU 温度。
        if os.name == "nt":
            cpu_temp_payload = SystemHardwareTool._query_cpu_temperature_psutil()
            gpu_payload = {
                "available": False,
                "message": "gpu_query_disabled",
            }
            if include_gpu:
                gpu_payload = SystemHardwareTool._query_nvidia_gpu_status()

            return {
                "ok": True,
                "platform": "windows",
                "cpu": {
                    "usage_percent": cpu_percent,
                    "temperature": cpu_temp_payload,
                    "physical_cores": psutil.cpu_count(logical=False),
                    "logical_cores": psutil.cpu_count(logical=True),
                },
                "memory": {
                    "total": memory.total,
                    "used": memory.used,
                    "usage_percent": memory.percent,
                },
                "gpu": gpu_payload,
            }

        # macOS 平台：通过 system_profiler 获取硬件与图形信息。
        if sys.platform == "darwin":
            hardware_payload = SystemHardwareTool._query_macos_hardware_overview()
            cpu_temp_payload = SystemHardwareTool._query_cpu_temperature_psutil()
            gpu_payload = {
                "available": False,
                "message": "gpu_query_disabled",
            }
            if include_gpu:
                gpu_payload = SystemHardwareTool._query_macos_gpu_status()

            return {
                "ok": True,
                "platform": "macos",
                "cpu": {
                    "usage_percent": cpu_percent,
                    "temperature": cpu_temp_payload,
                    "physical_cores": psutil.cpu_count(logical=False),
                    "logical_cores": psutil.cpu_count(logical=True),
                    "chip": hardware_payload.get("chip"),
                    "cpu_model": hardware_payload.get("cpu_model"),
                },
                "memory": {
                    "total": memory.total,
                    "used": memory.used,
                    "usage_percent": memory.percent,
                    "physical_memory_text": hardware_payload.get("physical_memory_text"),
                },
                "gpu": gpu_payload,
                "hardware": {
                    "model_name": hardware_payload.get("model_name"),
                    "model_identifier": hardware_payload.get("model_identifier"),
                    "serial_number": hardware_payload.get("serial_number"),
                },
            }

        # 其他平台暂不支持。
        return {
            "ok": False,
            "error": "unsupported_platform",
            "message": f"platform={sys.platform}",
        }

    @staticmethod
    def _query_cpu_temperature_psutil() -> Dict[str, Any]:
        cpu_temp_payload: Dict[str, Any] = {
            "available": False,
            "value_celsius": None,
            "source": "psutil",
        }

        try:
            sensors_fn = getattr(psutil, "sensors_temperatures", None)
            sensors = sensors_fn() if callable(sensors_fn) else {}
            if isinstance(sensors, dict) and sensors:
                first = None
                for entries in sensors.values():
                    if entries:
                        first = entries[0]
                        break
                if first is not None:
                    cpu_temp_payload = {
                        "available": True,
                        "value_celsius": getattr(first, "current", None),
                        "source": "psutil",
                    }
        except Exception:
            cpu_temp_payload = {
                "available": False,
                "value_celsius": None,
                "source": "psutil",
            }

        return cpu_temp_payload

    @staticmethod
    def _query_macos_hardware_overview() -> Dict[str, Any]:
        command = ["system_profiler", "SPHardwareDataType", "-json"]
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
        except Exception as exc:
            return {
                "available": False,
                "message": f"system_profiler_unavailable: {exc}",
            }

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            return {
                "available": False,
                "message": f"system_profiler_failed: {stderr}" if stderr else "system_profiler_failed",
            }

        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return {
                "available": False,
                "message": "system_profiler_json_decode_failed",
            }

        entries = payload.get("SPHardwareDataType") or []
        first = entries[0] if isinstance(entries, list) and entries else {}
        if not isinstance(first, dict):
            first = {}

        return {
            "available": True,
            "chip": first.get("chip_type") or first.get("machine_name"),
            "cpu_model": first.get("cpu_type"),
            "physical_memory_text": first.get("physical_memory"),
            "model_name": first.get("machine_name"),
            "model_identifier": first.get("machine_model"),
            "serial_number": first.get("serial_number"),
        }

    @staticmethod
    def _query_macos_gpu_status() -> Dict[str, Any]:
        command = ["system_profiler", "SPDisplaysDataType", "-json"]
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
        except Exception as exc:
            return {
                "available": False,
                "message": f"system_profiler_unavailable: {exc}",
            }

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            return {
                "available": False,
                "message": f"system_profiler_failed: {stderr}" if stderr else "system_profiler_failed",
            }

        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return {
                "available": False,
                "message": "system_profiler_json_decode_failed",
            }

        entries = payload.get("SPDisplaysDataType") or []
        cards: List[Dict[str, Any]] = []

        if isinstance(entries, list):
            for item in entries:
                if not isinstance(item, dict):
                    continue
                cards.append(
                    {
                        "name": item.get("sppci_model") or item.get("spdisplays_model") or item.get("_name"),
                        "vendor": item.get("spdisplays_vendor") or item.get("spdisplays_vendor-id"),
                        "vram": item.get("spdisplays_vram") or item.get("spdisplays_vram_shared"),
                        "metal": item.get("spdisplays_metal"),
                    }
                )

        return {
            "available": bool(cards),
            "cards": cards,
        }

    @staticmethod
    def _query_nvidia_gpu_status() -> Dict[str, Any]:
        command = [
            "nvidia-smi",
            "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]

        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=2)
        except Exception as exc:
            return {
                "available": False,
                "message": f"nvidia_smi_unavailable: {exc}",
            }

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            return {
                "available": False,
                "message": f"nvidia_smi_failed: {stderr}" if stderr else "nvidia_smi_failed",
            }

        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        cards: List[Dict[str, Any]] = []

        for line in lines:
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 5:
                continue
            name, temp, util, used_mem, total_mem = parts
            cards.append(
                {
                    "name": name,
                    "temperature_celsius": WeatherTool._safe_int(temp),
                    "utilization_percent": WeatherTool._safe_int(util),
                    "memory_used_mb": WeatherTool._safe_int(used_mem),
                    "memory_total_mb": WeatherTool._safe_int(total_mem),
                }
            )

        return {
            "available": bool(cards),
            "cards": cards,
        }


class WebSearchTool:
    @staticmethod
    def execute(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """具体的在线网页接口分发中心（工具端）。由它中转各大平台的 Web 搜索操作，按 provider 实现相应接口的 HTTP 调用。"""
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")

        provider = "bocha"    #TODO: 先写死用bocha，后续再开放参数
        top_k = int(arguments.get("top_k") or 5)

        dispatcher = {
            "bocha": WebSearchTool._search_bocha,
            "bing": WebSearchTool._search_bing,
            "tavily": WebSearchTool._search_tavily,
            "brave": WebSearchTool._search_brave,
            "google": WebSearchTool._search_google,
        }
        if provider not in dispatcher:
            raise ValueError(f"unsupported provider: {provider}")

        started_at = time.time()
        results = dispatcher[provider](query=query, top_k=top_k)
        return {
            "ok": True,
            "provider": provider,
            "query": query,
            "result_count": len(results),
            "latency_ms": int((time.time() - started_at) * 1000),
            "results": results,
        }

    @staticmethod
    def _search_bocha(query: str, top_k: int) -> List[Dict[str, Any]]:
        api_key = os.getenv("BOCHA_API_KEY", DEFAULT_BOCHA_API_KEY)
        url = "https://api.bocha.cn/v1/web-search"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {"query": query}

        response = requests.post(url, headers=headers, json=payload, timeout=12)
        response.raise_for_status()
        data = response.json()

        raw_items = data.get("data") or data.get("results") or data
        return WebSearchTool._normalize_search_items(raw_items, top_k=top_k)

    @staticmethod
    def _search_bing(query: str, top_k: int) -> List[Dict[str, Any]]:
        api_key = os.getenv("BING_SEARCH_API_KEY", "")
        if not api_key:
            raise ValueError("BING_SEARCH_API_KEY is not configured")

        endpoint = os.getenv("BING_SEARCH_ENDPOINT", "https://api.bing.microsoft.com/v7.0/search")
        headers = {"Ocp-Apim-Subscription-Key": api_key}
        params = {
            "q": query,
            "count": max(1, min(50, top_k)),
            "mkt": "zh-CN",
        }

        response = requests.get(endpoint, headers=headers, params=params, timeout=12)
        response.raise_for_status()
        data = response.json()
        raw_items = ((data.get("webPages") or {}).get("value")) or []
        return WebSearchTool._normalize_search_items(raw_items, top_k=top_k)

    @staticmethod
    def _search_tavily(query: str, top_k: int) -> List[Dict[str, Any]]:
        api_key = os.getenv("TAVILY_API_KEY", "")
        if not api_key:
            raise ValueError("TAVILY_API_KEY is not configured")

        url = "https://api.tavily.com/search"
        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": max(1, min(20, top_k)),
        }

        response = requests.post(url, json=payload, timeout=12)
        response.raise_for_status()
        data = response.json()
        raw_items = data.get("results") or []
        return WebSearchTool._normalize_search_items(raw_items, top_k=top_k)

    @staticmethod
    def _search_brave(query: str, top_k: int) -> List[Dict[str, Any]]:
        api_key = os.getenv("BRAVE_SEARCH_API_KEY", "")
        if not api_key:
            raise ValueError("BRAVE_SEARCH_API_KEY is not configured")

        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {
            "X-Subscription-Token": api_key,
            "Accept": "application/json",
        }
        params = {
            "q": query,
            "count": max(1, min(20, top_k)),
            "country": "cn",
        }

        response = requests.get(url, headers=headers, params=params, timeout=12)
        response.raise_for_status()
        data = response.json()
        raw_items = ((data.get("web") or {}).get("results")) or []
        return WebSearchTool._normalize_search_items(raw_items, top_k=top_k)

    @staticmethod
    def _search_google(query: str, top_k: int) -> List[Dict[str, Any]]:
        api_key = os.getenv("GOOGLE_SEARCH_API_KEY", "")
        cse_id = os.getenv("GOOGLE_CSE_ID", "")
        if not api_key or not cse_id:
            raise ValueError("GOOGLE_SEARCH_API_KEY and GOOGLE_CSE_ID are required")

        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": api_key,
            "cx": cse_id,
            "q": query,
            "num": max(1, min(10, top_k)),
        }

        response = requests.get(url, params=params, timeout=12)
        response.raise_for_status()
        data = response.json()
        raw_items = data.get("items") or []
        return WebSearchTool._normalize_search_items(raw_items, top_k=top_k)

    @staticmethod
    def _normalize_search_items(raw_items: Any, top_k: int) -> List[Dict[str, Any]]:
        items: List[Any]
        if isinstance(raw_items, list):
            items = raw_items
        elif isinstance(raw_items, dict):
            if isinstance(raw_items.get("value"), list):
                items = raw_items.get("value") or []
            elif isinstance((raw_items.get("webPages") or {}).get("value"), list):
                items = (raw_items.get("webPages") or {}).get("value") or []
            elif isinstance(raw_items.get("results"), list):
                items = raw_items.get("results") or []
            elif isinstance(raw_items.get("data"), list):
                items = raw_items.get("data") or []
            elif isinstance(raw_items.get("items"), list):
                items = raw_items.get("items") or []
            else:
                items = []
        else:
            items = []

        normalized: List[Dict[str, Any]] = []
        for item in items[: max(1, top_k)]:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "title": item.get("title") or item.get("name") or "",
                    "url": item.get("url") or item.get("link") or "",
                    "snippet": item.get("snippet") or item.get("description") or item.get("body") or "",
                }
            )
        return normalized


class WeatherTool:
    @staticmethod
    def execute(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """主入口气候天气 API（工具端）。用于检索一个位置的天气并在不提供城市参数的前提下自动调用 IP 地址辅助查询天气。"""
        city = str(arguments.get("city") or "").strip()
        if not city:
            city = WeatherTool._detect_city_from_ip()

        if not city:
            raise ValueError("Unable to determine city")

        lat, lon, resolved_name = WeatherTool._geocode_city(city)
        weather = WeatherTool._fetch_open_meteo_current_weather(lat=lat, lon=lon)

        return {
            "ok": True,
            "city": resolved_name,
            "location": {
                "latitude": lat,
                "longitude": lon,
            },
            "weather": weather,
        }

    @staticmethod
    def _detect_city_from_ip() -> str:
        urls = [
            "http://ip-api.com/json/?lang=zh-CN",
            "https://ipwho.is/",
        ]
        session = requests.Session()
        session.trust_env = False
        for url in urls:
            try:
                response = session.get(url, timeout=6)
                response.raise_for_status()
                payload = response.json()
                city = str(payload.get("city") or "").strip()
                if city:
                    return city
            except Exception:
                continue
        return ""

    @staticmethod
    def _geocode_city(city: str) -> Tuple[float, float, str]:
        url = "https://geocoding-api.open-meteo.com/v1/search"
        params = {
            "name": city,
            "count": 1,
            "language": "zh",
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        payload = response.json()

        results = payload.get("results") or []
        if not results:
            raise ValueError(f"city not found: {city}")

        first = results[0]
        latitude = float(first.get("latitude"))
        longitude = float(first.get("longitude"))
        name = str(first.get("name") or city)
        country = str(first.get("country") or "")
        resolved_name = f"{name}, {country}" if country else name
        return latitude, longitude, resolved_name

    @staticmethod
    def _fetch_open_meteo_current_weather(lat: float, lon: float) -> Dict[str, Any]:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "timezone": "auto",
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        payload = response.json()
        current = payload.get("current") or {}

        weather_code = WeatherTool._safe_int(current.get("weather_code"))
        return {
            "temperature_celsius": current.get("temperature_2m"),
            "apparent_temperature_celsius": current.get("apparent_temperature"),
            "humidity_percent": current.get("relative_humidity_2m"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "weather_code": weather_code,
            "weather_description": WeatherTool._weather_code_to_text(weather_code),
            "observed_at": current.get("time"),
        }

    @staticmethod
    def _weather_code_to_text(code: Optional[int]) -> str:
        mapping = {
            0: "Clear sky",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Fog",
            48: "Depositing rime fog",
            51: "Light drizzle",
            53: "Moderate drizzle",
            55: "Dense drizzle",
            61: "Slight rain",
            63: "Moderate rain",
            65: "Heavy rain",
            71: "Slight snow",
            73: "Moderate snow",
            75: "Heavy snow",
            80: "Rain showers",
            81: "Rain showers",
            82: "Violent rain showers",
            95: "Thunderstorm",
        }
        if code is None:
            return "Unknown"
        return mapping.get(code, "Unknown")

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


class FileReadingTools:
    """纯文字文件阅读工具集合（目录浏览 / 分页读取 / 关键字搜索）。"""

    # ---------- 可调常量 ----------
    MAX_LINES_PER_PAGE = 200          # 每次最多返回行数
    MAX_CHARS_PER_PAGE = 8000         # 每次最多返回字符数（二次保护）
    GREP_CONTEXT_LINES = 5            # 搜索结果前后各显示行数
    ALLOWED_EXTENSIONS: set = {
        # 纯文本 / 文档
        ".txt", ".md", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl",
        ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
        # 代码
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h",
        ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt",
        ".scala", ".lua", ".r", ".m", ".sql", ".sh", ".bash", ".zsh",
        ".bat", ".ps1", ".psm1", ".psd1",
        # Web
        ".html", ".htm", ".css", ".scss", ".less", ".vue", ".svelte",
        # 数据 / 配置
        ".gitignore", ".dockerignore", ".editorconfig", ".properties",
        ".gradle", ".cmake", ".makefile",
    }

    # ------------------------------------------------------------------ #
    #  1. list_directory — 目录/文件浏览
    # ------------------------------------------------------------------ #
    @staticmethod
    def list_directory(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """列出指定目录下的文件和子目录，供大模型"看到"有哪些文件可读。"""
        dir_path = str(arguments.get("dir_path") or "").strip()
        if not dir_path:
            return {"ok": False, "error": "dir_path 参数不能为空"}

        dir_path = os.path.expanduser(dir_path)
        dir_path = os.path.abspath(dir_path)

        if not os.path.exists(dir_path):
            return {"ok": False, "error": f"路径不存在: {dir_path}"}
        if not os.path.isdir(dir_path):
            return {"ok": False, "error": f"路径不是目录: {dir_path}"}

        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            return {"ok": False, "error": f"无权限访问目录: {dir_path}"}

        items: List[Dict[str, Any]] = []
        for name in entries:
            full = os.path.join(dir_path, name)
            is_dir = os.path.isdir(full)
            item: Dict[str, Any] = {
                "name": name,
                "type": "directory" if is_dir else "file",
            }
            if not is_dir:
                try:
                    size = os.path.getsize(full)
                    item["size_bytes"] = size
                    # 方便大模型理解文件大小
                    if size < 1024:
                        item["size_display"] = f"{size} B"
                    elif size < 1024 * 1024:
                        item["size_display"] = f"{size / 1024:.1f} KB"
                    else:
                        item["size_display"] = f"{size / (1024 * 1024):.1f} MB"
                except OSError:
                    pass
            items.append(item)

        return {
            "ok": True,
            "dir_path": dir_path,
            "total_entries": len(items),
            "entries": items,
        }

    # ------------------------------------------------------------------ #
    #  2. read_file_content — 分页精确读取（核心）
    # ------------------------------------------------------------------ #
    @staticmethod
    def read_file_content(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        读取指定文件的内容片段（按行分页）。
        每次最多返回 MAX_LINES_PER_PAGE 行 或 MAX_CHARS_PER_PAGE 字符，
        若文件未读完会在返回结果末尾附加下一页提示。
        """
        file_path = str(arguments.get("file_path") or "").strip()
        if not file_path:
            return {"ok": False, "error": "file_path 参数不能为空"}

        file_path = os.path.expanduser(file_path)
        file_path = os.path.abspath(file_path)

        if not os.path.exists(file_path):
            return {"ok": False, "error": f"文件不存在: {file_path}"}
        if not os.path.isfile(file_path):
            return {"ok": False, "error": f"路径不是文件: {file_path}"}

        # 扩展名安全检查（无后缀的文件允许尝试读取）
        _, ext = os.path.splitext(file_path)
        ext_lower = ext.lower()
        basename_lower = os.path.basename(file_path).lower()
        # 允许无扩展名文件（如 Makefile, Dockerfile）和白名单扩展名
        if ext_lower and ext_lower not in FileReadingTools.ALLOWED_EXTENSIONS:
            # 额外兜底：检查特殊文件名
            special_names = {
                "makefile", "dockerfile", "vagrantfile", "rakefile",
                "gemfile", "procfile", "cmakelists.txt",
            }
            if basename_lower not in special_names:
                return {
                    "ok": False,
                    "error": f"不支持读取该文件类型: {ext}（仅支持纯文本或代码文件）",
                }

        start_line = max(1, int(arguments.get("start_line") or 1))
        end_line_requested = int(arguments.get("end_line") or start_line + FileReadingTools.MAX_LINES_PER_PAGE - 1)

        # 强制分页限制
        if end_line_requested - start_line + 1 > FileReadingTools.MAX_LINES_PER_PAGE:
            end_line_requested = start_line + FileReadingTools.MAX_LINES_PER_PAGE - 1

        # 尝试自动检测编码
        encoding = FileReadingTools._detect_encoding(file_path)

        try:
            with open(file_path, "r", encoding=encoding, errors="replace") as f:
                all_lines = f.readlines()
        except Exception as exc:
            return {"ok": False, "error": f"读取文件失败: {exc}"}

        total_lines = len(all_lines)
        if total_lines == 0:
            return {
                "ok": True,
                "file_path": file_path,
                "total_lines": 0,
                "start_line": 1,
                "end_line": 0,
                "content": "(空文件)",
                "has_more": False,
            }

        # 修正越界
        if start_line > total_lines:
            return {
                "ok": False,
                "error": f"start_line={start_line} 超出文件总行数 {total_lines}",
            }
        actual_end = min(end_line_requested, total_lines)

        # 按行提取，再做字符数二次截断
        selected = all_lines[start_line - 1 : actual_end]
        content = "".join(selected)

        char_truncated = False
        if len(content) > FileReadingTools.MAX_CHARS_PER_PAGE:
            content = content[: FileReadingTools.MAX_CHARS_PER_PAGE]
            char_truncated = True

        has_more = actual_end < total_lines or char_truncated

        result: Dict[str, Any] = {
            "ok": True,
            "file_path": file_path,
            "total_lines": total_lines,
            "start_line": start_line,
            "end_line": actual_end,
            "content": content,
            "has_more": has_more,
        }

        if has_more:
            next_start = actual_end + 1
            result["hint"] = (
                f"[文件未读完，当前是 {start_line}-{actual_end} 行，"
                f"总共有 {total_lines} 行，"
                f"若需继续阅读请再次调用本工具传入 start_line={next_start}]"
            )

        return result

    # ------------------------------------------------------------------ #
    #  3. grep_search_in_file — 文件内关键字搜索
    # ------------------------------------------------------------------ #
    @staticmethod
    def grep_search_in_file(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        在指定文件中搜索关键字，返回匹配行及其前后各 GREP_CONTEXT_LINES 行的上下文。
        适用于大文件中快速定位代码段或关键信息。
        """
        file_path = str(arguments.get("file_path") or "").strip()
        keyword = str(arguments.get("keyword") or "").strip()

        if not file_path:
            return {"ok": False, "error": "file_path 参数不能为空"}
        if not keyword:
            return {"ok": False, "error": "keyword 参数不能为空"}

        file_path = os.path.expanduser(file_path)
        file_path = os.path.abspath(file_path)

        if not os.path.exists(file_path):
            return {"ok": False, "error": f"文件不存在: {file_path}"}
        if not os.path.isfile(file_path):
            return {"ok": False, "error": f"路径不是文件: {file_path}"}

        encoding = FileReadingTools._detect_encoding(file_path)

        try:
            with open(file_path, "r", encoding=encoding, errors="replace") as f:
                all_lines = f.readlines()
        except Exception as exc:
            return {"ok": False, "error": f"读取文件失败: {exc}"}

        total_lines = len(all_lines)
        keyword_lower = keyword.lower()
        ctx = FileReadingTools.GREP_CONTEXT_LINES

        # 收集所有匹配行号（1-indexed）
        match_line_numbers: List[int] = []
        for idx, line in enumerate(all_lines):
            if keyword_lower in line.lower():
                match_line_numbers.append(idx + 1)

        if not match_line_numbers:
            return {
                "ok": True,
                "file_path": file_path,
                "total_lines": total_lines,
                "keyword": keyword,
                "match_count": 0,
                "matches": [],
                "message": f"未在文件中找到关键字「{keyword}」",
            }

        # 组装上下文片段，合并相邻区域
        matches: List[Dict[str, Any]] = []
        total_chars = 0
        max_result_chars = FileReadingTools.MAX_CHARS_PER_PAGE

        for line_no in match_line_numbers:
            context_start = max(1, line_no - ctx)
            context_end = min(total_lines, line_no + ctx)
            snippet_lines = all_lines[context_start - 1 : context_end]
            snippet = "".join(snippet_lines)

            total_chars += len(snippet)
            matches.append({
                "match_line": line_no,
                "context_start": context_start,
                "context_end": context_end,
                "snippet": snippet,
            })

            # 避免返回内容过大
            if total_chars > max_result_chars:
                remaining = len(match_line_numbers) - len(matches)
                if remaining > 0:
                    matches.append({
                        "truncated": True,
                        "message": f"结果过多，已截断。还有 {remaining} 处匹配未展示，"
                                   f"请缩小搜索范围或使用 read_file_content 工具定位阅读。",
                    })
                break

        return {
            "ok": True,
            "file_path": file_path,
            "total_lines": total_lines,
            "keyword": keyword,
            "match_count": len(match_line_numbers),
            "matches": matches,
        }

    # ------------------------------------------------------------------ #
    #  4. export_document — 导出内容到文件（防语音刷屏利器）
    # ------------------------------------------------------------------ #
    @staticmethod
    def export_document(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        将大段文本或代码输出到本地文件。
        将文件保存到软件目录下的 tool_data 文件夹。
        """
        filename = str(arguments.get("filename") or "").strip()
        content = str(arguments.get("content") or "")

        if not filename:
            return {"ok": False, "error": "filename 参数不能为空"}
        if not content:
            return {"ok": False, "error": "content 不能为空"}

        # 净化文件名，防止非法字符
        safe_filename = re.sub(r'[\\/*?:"<>|]', "", os.path.basename(filename))
        if not safe_filename:
            safe_filename = "agent_output.md"

        # 放到 tool_data
        tool_data_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tool_data"))
        os.makedirs(tool_data_path, exist_ok=True)
        file_path = os.path.join(tool_data_path, safe_filename)

        try:
            # 确保父目录存在
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            # 使用系统默认程序自动打开该文件
            if sys.platform == "win32":
                os.startfile(file_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", file_path])
            else:
                subprocess.Popen(["xdg-open", file_path])

        except Exception as exc:
            return {"ok": False, "error": f"写入或打开文件失败: {exc}"}

        return {
            "ok": True,
            "file_path": file_path,
            "message": f"成功将 {len(content)} 字符写入文件：{file_path}",
        }

    # ------------------------------------------------------------------ #
    #  辅助方法
    # ------------------------------------------------------------------ #
    @staticmethod
    def _detect_encoding(file_path: str) -> str:
        """简易编码探测：先尝试 UTF-8，失败则回退到 GBK（覆盖中文 Windows 常见场景）。"""
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    f.read(4096)
                return enc
            except (UnicodeDecodeError, UnicodeError):
                continue
        return "utf-8"
