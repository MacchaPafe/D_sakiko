import concurrent.futures
import dataclasses
import datetime
import json
import os
import subprocess
import time
import traceback
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil
import requests


ToolHandler = Callable[[Dict[str, Any]], Any]
LLMCompletionFn = Callable[..., Any]
InterimMessageCallback = Callable[..., Any]


DEFAULT_BOCHA_API_KEY = "sk-28078debe6d74828a57e63164fe0f23e"


@dataclasses.dataclass
class RegisteredTool:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: ToolHandler

    def as_openai_schema(self) -> Dict[str, Any]:
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
        self._tools[name] = RegisteredTool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
        )

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def build_tools_schema(self) -> List[Dict[str, Any]]:
        return [tool.as_openai_schema() for tool in self._tools.values()]

    def execute(self, request: ToolCallRequest) -> Tuple[bool, str]:
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


class ToolCallingAgentRuntime:
    def __init__(
        self,
        llm_completion: LLMCompletionFn,
        tool_registry: Optional[ToolRegistry] = None,
        max_tool_rounds: int = 8,
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
        if self.debug:
            print(f"[ToolCallingRuntime] {message}")

    @staticmethod
    def _safe_json_dumps(data: Any) -> str:
        try:
            return json.dumps(data, ensure_ascii=False, indent=2)
        except TypeError:
            return str(data)

    def _serialize_response(self, response: Any) -> str:
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
            print("---------------------------------",response,"---------------------------------")
            #self._log("LLM响应 raw json:\n" + self._serialize_response(response))
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
            history.append(assistant_message)

            # 遍历并执行所有工具调用，将结果转化为 tool 消息压入历史中供下一轮循环读取
            for call in tool_calls:
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

        return _truncate(json.dumps(payload, ensure_ascii=False, indent=2))

    @staticmethod
    def _parse_llm_response(response: Any) -> Dict[str, Any]:
        # 兼容 dict / LiteLLM ModelResponse 两种返回结构。
        message: Any = None
        finish_reason: Optional[str] = None

        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                first_choice = choices[0]
                if isinstance(first_choice, dict):
                    message = first_choice.get("message")
                    finish_reason = first_choice.get("finish_reason")
        else:
            choices = getattr(response, "choices", None)
            if choices:
                first_choice = choices[0]
                message = getattr(first_choice, "message", None)
                finish_reason = getattr(first_choice, "finish_reason", None)

        content = ""
        raw_tool_calls: List[Any] = []

        if isinstance(message, dict):
            content = message.get("content") or ""
            raw_tool_calls = message.get("tool_calls") or []
        elif message is not None:
            content = getattr(message, "content", "") or ""
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
            "interim_message": interim_message,
            "tool_calls": tool_calls if (tool_calls or is_tool_finish) else [],
            "final_content": parsed_from_content.get("final_content", content),
            "finish_reason": finish_reason,
        }

    @staticmethod
    def _parse_content_json(content: str) -> Dict[str, Any]:
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
        return "text" in payload and ("emotion" in payload or "translation" in payload)

    @staticmethod
    def _normalize_structured_text_payload(items: List[Any]) -> str:
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


def build_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    registry.register_tool(
        name="get_current_datetime",
        description="Get local current date and time on the user's machine.",
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
        handler=_tool_get_current_datetime,
    )

    registry.register_tool(
        name="get_system_hardware_status",
        description="Get Windows hardware status like CPU load and available GPU temperature when possible.",
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
        handler=_tool_get_system_hardware_status,
    )

    registry.register_tool(
        name="web_search",
        description="使用Web搜索引擎查询信息。当你不清楚答案或者需要最新信息时，可以调用这个工具。参数query是必需的，provider和top_k是可选的，默认为bocha和5。",
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
        handler=_tool_web_search,
    )

    registry.register_tool(
        name="get_weather",
        description="Get current weather for user location or specified city.",
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
        handler=_tool_get_weather,
    )

    return registry


def _tool_get_current_datetime(arguments: Dict[str, Any]) -> Dict[str, Any]:
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


def _tool_get_system_hardware_status(arguments: Dict[str, Any]) -> Dict[str, Any]:
    include_gpu = bool(arguments.get("include_gpu", True))

    cpu_percent = psutil.cpu_percent(interval=0.2)
    memory = psutil.virtual_memory()

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

    gpu_payload = {
        "available": False,
        "message": "gpu_query_disabled",
    }
    if include_gpu:
        gpu_payload = _query_nvidia_gpu_status()

    return {
        "ok": True,
        "cpu": {
            "usage_percent": cpu_percent,
            "temperature": cpu_temp_payload,
        },
        "memory": {
            "total": memory.total,
            "used": memory.used,
            "usage_percent": memory.percent,
        },
        "gpu": gpu_payload,
    }


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
                "temperature_celsius": _safe_int(temp),
                "utilization_percent": _safe_int(util),
                "memory_used_mb": _safe_int(used_mem),
                "memory_total_mb": _safe_int(total_mem),
            }
        )

    return {
        "available": bool(cards),
        "cards": cards,
    }


def _tool_web_search(arguments: Dict[str, Any]) -> Dict[str, Any]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")

    provider = str(arguments.get("provider") or "bocha").strip().lower()
    top_k = int(arguments.get("top_k") or 5)

    dispatcher = {
        "bocha": _search_bocha,
        "bing": _search_bing,
        "tavily": _search_tavily,
        "brave": _search_brave,
        "google": _search_google,
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
    return _normalize_search_items(raw_items, top_k=top_k)


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
    return _normalize_search_items(raw_items, top_k=top_k)


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
    return _normalize_search_items(raw_items, top_k=top_k)


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
    return _normalize_search_items(raw_items, top_k=top_k)


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
    return _normalize_search_items(raw_items, top_k=top_k)


def _normalize_search_items(raw_items: Any, top_k: int) -> List[Dict[str, Any]]:
    # 兼容不同 provider 的多种结构，统一归一到列表后再做切片。
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


def _tool_get_weather(arguments: Dict[str, Any]) -> Dict[str, Any]:
    city = str(arguments.get("city") or "").strip()
    if not city:
        city = _detect_city_from_ip()

    if not city:
        raise ValueError("Unable to determine city")

    lat, lon, resolved_name = _geocode_city(city)
    weather = _fetch_open_meteo_current_weather(lat=lat, lon=lon)

    return {
        "ok": True,
        "city": resolved_name,
        "location": {
            "latitude": lat,
            "longitude": lon,
        },
        "weather": weather,
    }


def _detect_city_from_ip() -> str:
    urls = [
        "http://ip-api.com/json/?lang=zh-CN",
        "https://ipwho.is/",
    ]
    for url in urls:
        try:
            response = requests.get(url, timeout=6)
            response.raise_for_status()
            payload = response.json()
            city = str(payload.get("city") or "").strip()
            if city:
                return city
        except Exception:
            continue
    return ""


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

    weather_code = _safe_int(current.get("weather_code"))
    return {
        "temperature_celsius": current.get("temperature_2m"),
        "apparent_temperature_celsius": current.get("apparent_temperature"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_speed_kmh": current.get("wind_speed_10m"),
        "weather_code": weather_code,
        "weather_description": _weather_code_to_text(weather_code),
        "observed_at": current.get("time"),
    }


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


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
