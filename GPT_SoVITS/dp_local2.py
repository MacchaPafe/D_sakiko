from __future__ import annotations

import random
import re
import time,os
import uuid
import threading
import textwrap
from copy import deepcopy

import json
from queue import Empty
from typing import Optional, Sequence

import litellm

from qconfig import d_sakiko_config, THIRD_PARTY_OPENAI_COMPAT_PROVIDER_IDS
from llm_model_utils import ensure_openai_compatible_model
from character import CharacterAttributes
from log import get_logger

from chat.chat import Chat, Message, ChatManager
from chat.chat_meta import ToolCallHistoryRecordMeta, ToolCallRecordMeta
from chat.tool_calling import ToolCallingAgentRuntime
from emotion_enum import EmotionEnum
from deepseek_prompt_cache_debug import CACHE_DEBUG_PHASE_KEY, log_prompt_cache_usage

TOOL_CALL_START_EVENT_PREFIX = "__TOOL_CALL_START__:"
TOOL_CALL_UPDATE_EVENT_PREFIX = "__TOOL_CALL_UPDATE__:"
NO_AUDIO_TEXT_EVENT_PREFIX = "__NO_AUDIO_TEXT__:"
LOTTERY_UI_EVENT_PREFIX = "__LOTTERY_UI_CMD__:"
logger = get_logger(__name__)


def completion(**kwargs):
    """
    调用 litellm 的 completion 接口。
    如果模型属于 DeepSeek 模型，进行一次特殊的参数修改：
    将推理强度从 reasoning_effort 参数转换为 extra_body，因为 reasoning_effort 参数在 litellm 的 DeepSeek 实现中会被忽略。
    将 thinking 参数转换为 extra_body 中的 thinking 字段，格式为 {"type": "enabled"} 或 {"type": "disabled"}，因为 litellm 会忽略 thinking="disabled" 参数。
    """
    cache_debug_phase = str(kwargs.pop(CACHE_DEBUG_PHASE_KEY, "unknown") or "unknown")
    if "model" in kwargs and isinstance(kwargs["model"], str):
        # 只查找 deepseek 系列模型
        # 这里没有检验 base url，但愿第三方 API 提供 deepseek 的时候遵循和官网一样的 extra_body 协议……
        # 理论上讲，第三方 API 的行为应该和官方一样才对
        if "deepseek" in kwargs["model"].lower():
            extra_body = kwargs.get("extra_body")
            if not isinstance(extra_body, dict):
                extra_body = {}
            else:
                extra_body = dict(extra_body)

            # 记录是否禁用了推理
            thinking_disabled = False
            if "thinking" in kwargs:
                thinking_value = kwargs.pop("thinking", None)
                if isinstance(thinking_value, dict):
                    thinking_type = thinking_value.get("type")
                    thinking_disabled = thinking_type != "enabled"
                    extra_body["thinking"] = {
                        "type": "enabled" if thinking_type == "enabled" else "disabled"
                    }

            if "reasoning_effort" in kwargs:
                reasoning_effort = kwargs.pop("reasoning_effort")
                # 如果没有启用推理，不能加入 reasoning_effort 参数
                if not thinking_disabled:
                    if reasoning_effort == "xhigh":
                        # DeepSeek 官方文档推荐用 max 代替 xhigh 强度
                        reasoning_effort = "max"
                    extra_body["reasoning_effort"] = reasoning_effort
            # 保证不启用推理时一定没传入 reasoning_effort 参数
            if thinking_disabled:
                extra_body.pop("reasoning_effort", None)
            if extra_body:
                kwargs["extra_body"] = extra_body

    request_kwargs_for_log = dict(kwargs)
    request_kwargs_for_log[CACHE_DEBUG_PHASE_KEY] = cache_debug_phase
    response = litellm.completion(**kwargs)
    log_prompt_cache_usage(response, request_kwargs_for_log, logger)
    return response


class DSLocalAndVoiceGen:
    def __init__(self,characters: Sequence[CharacterAttributes], chat_manager: ChatManager):
        self.character_list = characters
        self.chat_manager = chat_manager
        self.character_by_name: dict[str, CharacterAttributes] = {
            character.character_name: character for character in self.character_list
        }
        self.current_chat_id = self.chat_manager.ensure_default_single_character_chat(self.character_list).chat_id

        # 语音输出的语言
        self.audio_language = ["中英混合", "日英混合"]
        self.audio_language_choice = self.audio_language[1]

        try:
            self.model = __import__('live2d_1').get_live2d()
        except ImportError:
            self.model = None
            logger.warning("未获取到 UP 的 API Key, 请在启动参数配置中设置自己的 API Key")

        # 是否为角色通过 GPT-SoVITS 模型生成音频
        self.if_generate_audio = True

        # 当前角色是否是祥子
        self.if_sakiko = False
        # 祥子的状态是黑祥还是白祥，True：黑祥，False：白祥
        self.sakiko_state = True
        # 角色在空闲时，页面上方状态会显示的话。从列表中随机选择。
        self.idle_texts = ["...", "...", "就绪", "..."]
        
        # 实例化工具调用运行时。传入的大模型补全请求会被委托给 self._completion_with_current_config。
        # 这样成功将 LLM 内部具体请求格式和 Agent 工具调用逻辑解耦。
        self.tool_runtime = ToolCallingAgentRuntime(
            llm_completion=self._completion_with_current_config,
            debug=True,
        )
        # 仅在本次程序运行期间使用的工具调用记录缓存；退出时会一并写入 Chat.meta。
        self._session_tool_call_records: list[dict] = []
        # 标记为取消的对话轮次。涉及这些轮次的对话不会被处理。
        self._cancelled_turns: set[tuple[str, str]] = set()
        self._cancelled_turns_lock = threading.Lock()
        self.initial()

    def initial(self):
        # 聊天记录已经由 ChatManager 在初始化时加载完成，无需再从旧版 JSON 文件读取
        self._refresh_current_character_flags()
        with open('./text/restr.rep', 'r', encoding='utf-8') as f:
            self.restr = f.read()

    @property
    def current_chat(self) -> Chat:
        """获取当前对话对象。"""
        chat = self.chat_manager.get_chat_by_id(self.current_chat_id)
        if chat is None:
            chat = self.chat_manager.ensure_default_single_character_chat(self.character_list)
            self.current_chat_id = chat.chat_id
        return chat

    def get_current_character(self) -> CharacterAttributes:
        """
        获取当前对话绑定的角色对象。
        """
        character_name = self.current_chat.get_character_name()
        if character_name is None:
            raise ValueError("当前对话无法确定角色。")
        character = self.character_by_name.get(character_name)
        if character is None:
            raise ValueError(f"找不到当前对话绑定的角色：{character_name}")
        return character

    def switch_chat(self, chat_id: str) -> bool:
        """
        切换当前普通对话。
        """
        chat = self.chat_manager.get_chat_by_id(chat_id)
        if chat is None:
            return False
        self.current_chat_id = chat.chat_id
        self._session_tool_call_records = [record.to_dict() for record in chat.get_tool_call_records()]
        self._refresh_current_character_flags()
        return True

    def _refresh_current_character_flags(self) -> None:
        """
        根据当前对话角色刷新运行时角色标记。
        """
        try:
            self.if_sakiko = self.get_current_character().character_name == "祥子"
        except ValueError:
            self.if_sakiko = False

    def request_cancel_turn(self, chat_id: str, turn_id: str) -> None:
        """
        线程安全地登记一轮对话取消请求。
        """
        if not chat_id or not turn_id:
            return
        with self._cancelled_turns_lock:
            self._cancelled_turns.add((chat_id, turn_id))

    def is_turn_cancelled(self, chat_id: str, turn_id: str) -> bool:
        """
        判断一轮对话是否已被前端请求取消。
        """
        if not chat_id or not turn_id:
            return False
        with self._cancelled_turns_lock:
            return (chat_id, turn_id) in self._cancelled_turns

    def clear_cancelled_turn(self, chat_id: str, turn_id: str) -> None:
        """
        清理已经完成收尾的取消请求，将其从“已取消列表”中移除。
        """
        if not chat_id or not turn_id:
            return
        with self._cancelled_turns_lock:
            self._cancelled_turns.discard((chat_id, turn_id))

    def trim_list_to_340kb(self, data_list):
        working_list = deepcopy(data_list)
        MAX_SIZE = 340 * 1024  # 主流大模型的上下文现在基本都是128Ktoken，差不多500KB，这里设置340KB以防万一
        while len(json.dumps(working_list, ensure_ascii=False).encode('utf-8')) > MAX_SIZE:
            del working_list[1]
        return working_list

    @staticmethod
    def concat_provider_and_model(provider: str, model: str) -> str:
        """
        智能的将模型提供商和模型名称连接在一起。
        如果 model 不包含 / 符号，那么就将 provider 和 model 用 / 连接起来。
        如果 model 已经包含 / 符号，我们认为此时包含了提供商信息，那么就直接返回 model。

        :param provider: 模型提供商，如 openai、deepseek
        :param model: 模型名称，如 gpt-4、deepseek-chat。也可以输入 deepseek/deepseek-chat 这种带提供商前缀的完整名称。此时，输入的 provider 会被忽略。
        """
        if '/' in model:
            return model
        else:
            return f"{provider}/{model}"

    @staticmethod
    def normalize_model_for_provider(provider: str, model: str) -> str:
        """根据 provider 类型决定是否尝试自动补全前缀。

        - 第三方 OpenAI 兼容端点：强制走 openai/ 路由（若未带 openai/ 则自动补齐）。
        - litellm 内置 provider：若用户未输入前缀，则尝试补全为 provider/model。
        """
        if provider in THIRD_PARTY_OPENAI_COMPAT_PROVIDER_IDS:
            return ensure_openai_compatible_model(model)
        return DSLocalAndVoiceGen.concat_provider_and_model(provider, model)

    def report_message_to_main_ui(self, message_queue, is_text_generating_queue, message: str):
        """
        向主界面的消息栏汇报一条错误信息，并且删除最新正在发给模型的对话记录。
        """
        message_queue.put(message)
        is_text_generating_queue.get()
        # 删除未能成功发送的信息
        if self.current_chat.message_list:
            self.current_chat.message_list.pop()
        # 源代码如此，我也不知道为啥要休息一会
        #time.sleep(2) 不休息也已经可以了

    @staticmethod
    def _truncate_error_detail(text: str, limit: int = 1800) -> str:
        text = str(text or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit] + "\n...(错误信息过长，已截断；日志中保留完整 traceback)"

    @staticmethod
    def _redact_sensitive_error_text(text: str) -> str:
        text = str(text or "")
        text = re.sub(
            r"(?i)(api[_ -]?key|authorization|x-api-key)([\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+",
            r"\1\2***",
            text,
        )
        text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "sk-***", text)
        return text

    @staticmethod
    def _format_exception_value(value) -> str:
        if value is None:
            return ""
        if isinstance(value, (str, int, float, bool)):
            return DSLocalAndVoiceGen._redact_sensitive_error_text(str(value))
        try:
            return DSLocalAndVoiceGen._redact_sensitive_error_text(
                json.dumps(value, ensure_ascii=False, default=str)
            )
        except Exception:
            return DSLocalAndVoiceGen._redact_sensitive_error_text(repr(value))

    @classmethod
    def _format_exception_details(cls, exc: BaseException) -> str:
        exc_summary = cls._redact_sensitive_error_text(str(exc))
        lines = [f"{type(exc).__module__}.{type(exc).__name__}: {exc_summary}"]
        for attr in (
            "status_code",
            "message",
            "llm_provider",
            "model",
            "code",
            "param",
            "type",
            "body",
            "request_id",
            "litellm_debug_info",
        ):
            if not hasattr(exc, attr):
                continue
            value = getattr(exc, attr, None)
            formatted = cls._format_exception_value(value)
            if formatted:
                lines.append(f"{attr}: {formatted}")

        response = getattr(exc, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            if status_code is not None:
                lines.append(f"response.status_code: {status_code}")
            response_text = getattr(response, "text", None)
            if response_text:
                lines.append(f"response.text: {cls._redact_sensitive_error_text(response_text)}")

        cause = exc.__cause__ or exc.__context__
        if cause is not None and cause is not exc:
            cause_summary = cls._redact_sensitive_error_text(str(cause))
            lines.append(f"caused_by: {type(cause).__module__}.{type(cause).__name__}: {cause_summary}")
        return "\n".join(lines)

    def _report_model_exception(
        self,
        message_queue,
        is_text_generating_queue,
        user_message: str,
        exc: BaseException,
    ) -> None:
        details = self._format_exception_details(exc)
        logger.error(
            "大模型请求失败：%s\n%s",
            user_message,
            details,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        ui_message = f"{user_message}\n\n详细错误信息：\n{self._truncate_error_detail(details)}"
        self.report_message_to_main_ui(message_queue, is_text_generating_queue, ui_message)

    def _build_runtime_system_instruction(self) -> str:
        """
        构造稳定的运行期系统提示词，描述渲染器需要的机器输出协议。
        """
        parts: list[str] = []
        restriction = str(getattr(self, "restr", "") or "").strip()
        if restriction:
            parts.append(f"[角色边界]\n{restriction}")

        parts.append(textwrap.dedent(
            """# Machine Output Contract
            你不是直接向用户输出自由文本。
            你正在为一个语音与聊天渲染器生成结构化数据。

            最终回复必须只返回一个 JSON array，不要输出 Markdown 代码块、解释文字、前缀或后缀。

            JSON array 的每个元素都是一个对话段落对象，结构为：
            [
            {
                "text": "角色实际说出口的台词",
                "emotion": "happiness | sadness | anger | surprise | fear | disgust | like",
                "translation": "中文翻译；仅当 runtime.reply_language 为 ja_with_zh_translation 时必须存在"
            }
            ]

            当角色需要说两句话，并且 runtime.reply_language 为 ja_with_zh_translation 时，输出示例为：
            [
            {
                "text": "今日は少し疲れましたが、あなたと話していると落ち着きます。",
                "translation": "今天稍微有点累，不过和你说话会让我平静下来。",
                "emotion": "happiness"
            },
            {
                "text": "だから、もう少しだけここにいてもいいですか。",
                "translation": "所以，我可以再在这里待一会儿吗？",
                "emotion": "like"
            }
            ]

            当 runtime.reply_language 为 zh_only 时，输出示例为：
            [
            {
                "text": "今天稍微有点累，不过和你说话会让我平静下来。",
                "emotion": "happiness"
            },
            {
                "text": "所以，我可以再在这里待一会儿吗？",
                "emotion": "like"
            }
            ]

            规则：
            1. 顶层 JSON array 必须是非空数组。
            2. 每个 segment 表示一次自然的语义停顿，通常为 1 到 3 句。
            3. text 必须符合角色人设和当前上下文。
            4. emotion 必须从指定枚举中选择，且只能选择一个。
            5. 当 runtime.reply_language 为 zh_only 时，严禁输出 translation 字段。
            6. 当 runtime.reply_language 为 ja_with_zh_translation 时，每个 segment 都必须有 translation 字段。
            7. 每个段落对象严禁输出 text、emotion、translation 之外的字段。
            8. 请求末尾可能包含一条 <runtime_controls> 消息。它是应用传入的本轮元数据，不是用户说出口的话。"""
        ))
        return "\n\n".join(parts)

    def _build_turn_runtime_controls(self) -> str:
        """
        构造仅描述单个 user 消息的短运行期控制消息。即系统提示词中提到的 <runtime_controls> 消息内容。
        目前每条 user 消息会有如下的运行时控制信息：
        - reply_language: 当前的语音输出语言设置，可能的值为 "ja_with_zh_translation"（日英混合）和 "zh_only"（纯中文）。
        - sakiko_tone: 祥子的语言风格（如果当前角色是祥子），可能的值为 "dark"（黑祥）、"light"（白祥）和 "none"（非祥子角色）。
        """
        reply_language = (
            "ja_with_zh_translation"
            if self.audio_language_choice == '日英混合'
            else "zh_only"
        )
        sakiko_tone = "none"
        if self.if_sakiko:
            sakiko_tone = "dark" if self.sakiko_state else "light"

        return (
            "<runtime_controls>\n"
            f"reply_language: {reply_language}\n"
            f"sakiko_tone: {sakiko_tone}\n"
            "</runtime_controls>"
        )

    @staticmethod
    def _append_runtime_controls_message(messages: list[dict[str, object]], controls: str) -> None:
        """
        将本轮控制信息作为独立短消息追加到请求副本末尾。
        """
        messages.append({"role": "user", "content": controls})

    def _build_llm_messages_for_chat_turn(self, character_name: str) -> list[dict[str, object]]:
        """
        基于当前对话构造发送给 LLM 的请求副本。
        """
        messages = [
            dict(one)
            # 基于角色视角（即 AI 输出的内容为完整的带格式内容），并尽量简化
            for one in self.current_chat.build_llm_query(
                perspective=character_name,
                is_simplify=True,
            )
        ]
        runtime_system_instruction = self._build_runtime_system_instruction()
        system_idx = -1

        # 为对话中的第一条 system 消息追加运行时系统提示词；该提示词不会存储到对话历史中。
        for idx, one in enumerate(messages):
            if one.get("role") == "system":
                system_idx = idx
                break
        if system_idx >= 0:
            content = str(messages[system_idx].get("content") or "")
            messages[system_idx]["content"] = content + "\n" + runtime_system_instruction
        else:
            messages.insert(0, {"role": "system", "content": runtime_system_instruction})

        # 追加一条额外的用户消息描述本轮对话的选择（比如语言模式和祥子语气），该消息同样不会存储到对话历史中。
        self._append_runtime_controls_message(messages, self._build_turn_runtime_controls())
        return messages

    def _build_current_reasoning_kwargs_snapshot(self) -> dict[str, object]:
        """
        为当前用户输入创建一份推理配置快照。

        工具调用运行时可能在同一轮用户输入内多次请求模型。这里将推理设置转换为显式
        kwargs，保证本轮所有工具调用请求使用同一套 thinking/reasoning_effort 配置。
        """
        reasoning_meta = self.current_chat.meta.llm_reasoning
        reasoning_kwargs: dict[str, object] = {"_reasoning_snapshot_locked": True}

        if reasoning_meta.enabled == "on":
            reasoning_kwargs["thinking"] = {"type": "enabled"}
        elif reasoning_meta.enabled == "off":
            reasoning_kwargs["thinking"] = {"type": "disabled"}

        if reasoning_meta.effort != "default":
            reasoning_kwargs["reasoning_effort"] = reasoning_meta.effort

        return reasoning_kwargs

    def _completion_with_current_config(self, model, messages, tools=None, tool_choice="auto", **kwargs):
        """
        runtime 发起 LLM 请求的底层入口。

        在进行请求时，此函数会根据当前 chat 的设置，传入是否启用推理以及推理强度设定。如果 kwargs 中已经包含了相关设定，则以 kwargs 中的为准。
        :param model: 当前请求使用的模型名称
        :param messages: 请求发送的所有信息
        :param tools: 请求提供的 tool
        :param tool_choice: 工具选择策略，默认为 "auto"，表示由模型决定使用哪个工具；也可以指定为特定工具的名称，强制使用该工具（前提是 tools 中有这个工具）。
        :param kwargs: 其他传递给 litellm.completion 的参数。请注意如下参数在 kwargs 中传入时不会生效：
        model, messages, api_key, base_url, tools, tool_choice
        """
        # 提取运行时参数，避免后续和显式传参重复。
        runtime_kwargs = dict(kwargs)
        stream = runtime_kwargs.pop("stream", False)
        timeout = runtime_kwargs.pop("timeout", 30)
        reasoning_snapshot_locked = bool(runtime_kwargs.pop("_reasoning_snapshot_locked", False))
        cache_debug_phase = str(runtime_kwargs.pop(CACHE_DEBUG_PHASE_KEY, model) or model)
        # 不允许通过 kwargs 手动指定这些参数；这些参数只能在本函数内构建
        for protected_key in ("model", "messages", "api_key", "base_url", "tools", "tool_choice"):
            runtime_kwargs.pop(protected_key, None)

        # 根据当前 API 配置选择模型、鉴权信息和可选 base_url。
        if d_sakiko_config.use_default_deepseek_api.value:
            logger.debug("正在使用 UP 的 DeepSeek API")
            completion_kwargs = {
                "model": "deepseek/deepseek-v4-flash",
                "messages": messages,
                "api_key": self.model,
                "stream": stream,
                "timeout": timeout,
            }
        elif d_sakiko_config.enable_custom_llm_api_provider.value:
            logger.debug("正在使用自定义大模型 API")
            logger.debug("API Base: %s", d_sakiko_config.custom_llm_api_url.value)
            logger.debug("API Model: %s", d_sakiko_config.custom_llm_api_model.value)
            completion_kwargs = {
                "model": ensure_openai_compatible_model(d_sakiko_config.custom_llm_api_model.value),
                "messages": messages,
                "api_key": d_sakiko_config.custom_llm_api_key.value,
                "base_url": d_sakiko_config.custom_llm_api_url.value,
                "stream": stream,
                "timeout": timeout,
            }
        else:
            logger.debug("正在使用预定义大模型 API")
            logger.debug("Provider: %s", d_sakiko_config.llm_api_provider.value)
            logger.debug("Model: %s", d_sakiko_config.llm_api_model.value[d_sakiko_config.llm_api_provider.value])
            provider = d_sakiko_config.llm_api_provider.value
            model_name = d_sakiko_config.llm_api_model.value.get(provider, "")
            api_key = d_sakiko_config.llm_api_key.value.get(provider, "")
            base_url = d_sakiko_config.llm_api_base_url.value.get(provider)

            completion_kwargs = {
                "model": self.normalize_model_for_provider(provider, model_name),
                "messages": messages,
                "api_key": api_key,
                "stream": stream,
                "timeout": timeout,
            }
            if base_url:
                completion_kwargs["base_url"] = base_url

        if tools is not None:
            completion_kwargs["tools"] = tools
            completion_kwargs["tool_choice"] = tool_choice

        # 如果调用方没有显式传入推理相关参数，则根据当前 Chat 的配置补充。
        extra_body = runtime_kwargs.get("extra_body")
        extra_body_has_reasoning = (
            isinstance(extra_body, dict)
            and ("thinking" in extra_body or "reasoning_effort" in extra_body)
        )
        has_explicit_reasoning = (
            reasoning_snapshot_locked
            or "thinking" in runtime_kwargs
            or "reasoning_effort" in runtime_kwargs
            or extra_body_has_reasoning
        )
        if not has_explicit_reasoning:
            reasoning_meta = self.current_chat.meta.llm_reasoning
            if reasoning_meta.enabled == "on":
                runtime_kwargs["thinking"] = {"type": "enabled"}
            elif reasoning_meta.enabled == "off":
                runtime_kwargs["thinking"] = {"type": "disabled"}

            if reasoning_meta.effort != "default":
                runtime_kwargs["reasoning_effort"] = reasoning_meta.effort

        # 合并运行时参数并发起请求。runtime_kwargs 不允许覆盖上面确定的核心请求字段。
        completion_kwargs.update(runtime_kwargs)
        completion_kwargs[CACHE_DEBUG_PHASE_KEY] = cache_debug_phase
        return completion(**completion_kwargs)

    @staticmethod
    def _wait_audio_turn_complete(is_audio_play_complete):
        while is_audio_play_complete.get() is None:
            time.sleep(0.3)

    @staticmethod
    def _clear_text_generating_flag_if_needed(is_text_generating_queue) -> None:
        """
        取消收尾时，确保 Live2D 不会一直停留在思考状态。
        """
        try:
            is_text_generating_queue.get_nowait()
        except Empty:
            return
        except Exception:
            return

    @staticmethod
    def _emit_tool_ui_event(dp2qt_queue, prefix: str, payload: dict):
        event_type = "tool_call_started" if prefix == TOOL_CALL_START_EVENT_PREFIX else "tool_call_updated"
        event_payload = dict(payload)
        event_payload["type"] = event_type
        dp2qt_queue.put(event_payload)

    def _build_model_response_payload(
        self,
        turn_id: str,
        character_name: str,
        segments: list[dict[str, object]],
        turn_complete: bool = True,
    ) -> dict[str, object]:
        """
        构造发送给主线程语音合成链路的模型回复事件。

        :param turn_id: 当前轮次对话的 id（用户每在输入框输入一次文字并发送，算作一轮对话）
        :param character_name: 当前角色的名称
        :param segments: 本次模型回复的文本段落列表，每个段落包含 text、translation、emotion 等字段
        :param turn_complete: 这轮消息是否结束了。对于模型在工具调用时发送的过渡消息，该值为 False；对于正常的完整回复，该值为 True
        """
        return {
            "type": "model_response",
            "chat_id": self.current_chat.chat_id,
            "turn_id": turn_id,
            "character_name": character_name,
            "sakiko_state": self.sakiko_state,
            "audio_language_choice": self.audio_language_choice,
            "if_generate_audio": self.if_generate_audio,
            "turn_complete": turn_complete,
            "segments": segments,
        }

    def _emit_turn_complete(self, dp2qt_queue, chat_id: str, turn_id: str, status: str = "ok") -> None:
        """
        通知 UI 某一轮对话已经结束。
        """
        dp2qt_queue.put({
            "type": "assistant_turn_complete",
            "chat_id": chat_id,
            "turn_id": turn_id,
            "status": status,
        })
        # 如果某轮对话被标记为取消且调用了本函数，那么说明整轮对话处理流程完成了
        # 直接从取消列表中删掉这轮对话。
        if status in {"cancelled", "error"}:
            self.clear_cancelled_turn(chat_id, turn_id)

    @staticmethod
    def _segment_from_message(message_index: int, message: Message, force_no_audio: bool) -> dict[str, object]:
        """
        从一条 assistant 消息构造跨线程片段数据。
        """
        return {
            "message_index": message_index,
            "text": message.text,
            "translation": message.translation,
            "emotion": message.emotion.as_label(),
            "force_no_audio": force_no_audio,
        }

    @staticmethod
    def _strip_json_markdown_fence(text: str) -> str:
        """
        去掉模型偶尔包裹在 JSON 外层的 Markdown 代码块标记。
        """
        json_str = text.strip()
        if json_str.startswith('```'):
            json_str = re.sub(r'^```(?:json)?\s*', '', json_str)
            json_str = re.sub(r'\s*```$', '', json_str)
        return json_str.strip()

    @staticmethod
    def _extract_response_content(response: object) -> str:
        """
        从 LiteLLM/OpenAI 风格响应中提取 assistant content。
        """
        message: object = None
        if isinstance(response, dict):
            choices = response.get("choices")
            if isinstance(choices, list) and choices:
                first_choice = choices[0]
                if isinstance(first_choice, dict):
                    message = first_choice.get("message")
        else:
            choices = getattr(response, "choices", None)
            if isinstance(choices, list) and choices:
                first_choice = choices[0]
                message = getattr(first_choice, "message", None)

        if isinstance(message, dict):
            return str(message.get("content") or "")
        if message is not None:
            return str(getattr(message, "content", "") or "")
        return ""

    @staticmethod
    def _emotion_from_model_value(emotion: str) -> EmotionEnum:
        """
        将模型输出的情绪字符串转换为程序内部枚举。
        """
        return EmotionEnum.from_string(emotion)

    @staticmethod
    def _allowed_model_emotions() -> set[str]:
        """
        返回机器输出协议允许的情绪值。
        """
        return {
            "happiness",
            "sadness",
            "anger",
            "surprise",
            "fear",
            "disgust",
            "like",
        }

    def _parse_model_segments_payload(self, content: str) -> list[dict[str, str]]:
        """
        解析并校验模型输出的顶层 JSON array 对话段落。
        """
        json_str = self._strip_json_markdown_fence(content)
        if not json_str:
            raise ValueError("模型返回内容为空。")

        parsed_data = json.loads(json_str)
        if not isinstance(parsed_data, list):
            raise ValueError("模型输出的 JSON 顶层必须是 array。")

        if not parsed_data:
            raise ValueError("模型输出的 JSON array 不能为空。")

        allowed_keys = {"text", "emotion", "translation"}
        allowed_emotions = self._allowed_model_emotions()
        need_translation = self.audio_language_choice == '日英混合'
        segments: list[dict[str, str]] = []
        for index, item in enumerate(parsed_data):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index + 1} 个段落不是 JSON object。")

            extra_keys = set(str(key) for key in item.keys()) - allowed_keys
            if extra_keys:
                raise ValueError(f"第 {index + 1} 个段落包含多余字段：{sorted(extra_keys)}。")

            text = str(item.get("text") or "").strip()
            if not text:
                raise ValueError(f"第 {index + 1} 个段落缺少 text。")

            emotion = str(item.get("emotion") or "").strip().lower()
            if emotion not in allowed_emotions:
                raise ValueError(f"第 {index + 1} 个段落 emotion 不合法：{emotion or '空'}。")

            segment = {"text": text, "emotion": emotion}
            if need_translation:
                translation = str(item.get("translation") or "").strip()
                if not translation:
                    raise ValueError(f"第 {index + 1} 个段落缺少 translation。")
                segment["translation"] = translation
            segments.append(segment)

        return segments

    def _segments_to_messages(
        self,
        character_name: str,
        segments: list[dict[str, str]],
        force_no_audio: bool = False,
    ) -> list[int]:
        """
        将校验后的模型段落写入当前对话，并返回新增消息索引。
        """
        added_msg_indices: list[int] = []
        for item in segments:
            msg = Message(
                character_name=character_name,
                text=item["text"],
                translation=item.get("translation", ""),
                emotion=self._emotion_from_model_value(item["emotion"]),
                audio_path="NO_AUDIO" if force_no_audio else ("" if self.if_generate_audio else "NO_AUDIO"),
            )
            self.current_chat.add_message(msg)
            added_msg_indices.append(len(self.current_chat.message_list) - 1)
        return added_msg_indices

    def _run_json_completion_with_empty_retry(
        self,
        messages: list[dict[str, object]],
        reasoning_kwargs: dict[str, object],
        max_tokens: int = -1,
        phase: str = "final_json",
    ) -> tuple[str, bool]:
        """
        发起最终结构化回复请求，并在空 content 时静默重试一次。

        返回值中的 bool 表示本次成功返回内容时是否实际启用了 JSON Mode。
        """
        use_json_mode = self._supports_json_object_response_format_for_current_config()
        for _ in range(2):
            request_kwargs = {
                "stream": False,
                "timeout": 30,
                **reasoning_kwargs,
            }
            if max_tokens > 0:
                request_kwargs["max_tokens"] = max_tokens
            if use_json_mode:
                request_kwargs["response_format"] = {"type": "json_object"}
            request_kwargs[CACHE_DEBUG_PHASE_KEY] = phase
            try:
                response = self._completion_with_current_config(
                    model="json-runtime",
                    messages=messages,
                    tools=None,
                    tool_choice="none",
                    **request_kwargs,
                )
            except litellm.exceptions.BadRequestError as exc:
                if use_json_mode and self._is_response_format_unsupported(exc):
                    logger.warning("当前模型或服务不支持 JSON Mode，降级为普通补全。")
                    use_json_mode = False
                    fallback_kwargs = {
                        "stream": False,
                        "timeout": 30,
                        CACHE_DEBUG_PHASE_KEY: f"{phase}_fallback",
                        **reasoning_kwargs,
                    }
                    if max_tokens > 0:
                        fallback_kwargs["max_tokens"] = max_tokens
                    if max_tokens > 0:
                        response = self._completion_with_current_config(
                            model="json-runtime",
                            messages=messages,
                            tools=None,
                            tool_choice="none",
                            **fallback_kwargs,
                        )
                    else:
                        response = self._completion_with_current_config(
                            model="json-runtime",
                            messages=messages,
                            tools=None,
                            tool_choice="none",
                            **fallback_kwargs,
                        )
                else:
                    raise

            last_content = self._extract_response_content(response).strip()
            if last_content:
                return last_content, use_json_mode

        raise ValueError("模型连续返回空内容。")

    def _supports_json_object_response_format_for_current_config(self) -> bool:
        """
        使用 LiteLLM 的模型参数表判断当前配置是否应尝试 JSON Mode。
        """
        get_supported_params = getattr(litellm, "get_supported_openai_params", None)
        if not callable(get_supported_params):
            return False

        model = self._current_litellm_model_name()
        try:
            params = get_supported_params(model=model)
        except Exception as exc:
            logger.warning("查询模型 JSON Mode 支持失败，降级为普通补全：%s", exc)
            return False

        return isinstance(params, list) and "response_format" in params

    def _current_litellm_model_name(self) -> str:
        """
        根据当前配置解析 LiteLLM 实际收到的 model 字符串。
        """
        if d_sakiko_config.use_default_deepseek_api.value:
            return "deepseek/deepseek-v4-flash"
        if d_sakiko_config.enable_custom_llm_api_provider.value:
            return ensure_openai_compatible_model(d_sakiko_config.custom_llm_api_model.value)

        provider = d_sakiko_config.llm_api_provider.value
        model_name = d_sakiko_config.llm_api_model.value.get(provider, "")
        return self.normalize_model_for_provider(provider, model_name)

    @staticmethod
    def _is_response_format_unsupported(exc: litellm.exceptions.BadRequestError) -> bool:
        """
        判断 BadRequest 是否来自 response_format/JSON Mode 不兼容。
        """
        message = str(getattr(exc, "message", str(exc)) or "").lower()
        return (
            "response_format" in message
            or "json_object" in message
            or "json mode" in message
        )

    def _build_final_json_messages(
        self,
        base_messages: list[dict[str, object]],
        candidate_content: str = "",
    ) -> list[dict[str, object]]:
        """
        基于工具调用临时历史构造最终 JSON 回复请求。
        """
        messages = [dict(one) for one in base_messages]
        candidate = str(candidate_content or "").strip()
        if candidate:
            messages.append({
                "role": "assistant",
                "content": candidate,
            })
        messages.append({
            "role": "user",
            "content": (
                "请将上一条 assistant 候选回复转换为本轮最终回复。\n"
                "上一条 assistant 仅是候选草稿，不代表格式正确；如果它已经是自然语言或格式不合格，"
                "请转换为正确 JSON array，不要照抄错误格式。\n"
                "只输出符合 Machine Output Contract 的 JSON array。"
            ),
        })
        return messages

    def _build_format_retry_messages(self, invalid_content: str, reason: str) -> list[dict[str, object]]:
        """
        构造 schema 格式纠正 retry 请求。
        """
        return [
            {
                "role": "system",
                "content": self._build_runtime_system_instruction(),
            },
            {
                "role": "user",
                "content": (
                    "下面是模型输出，可能不是合法 JSON，或不符合业务 schema。\n"
                    "请只纠正 JSON 结构、字段和缺失项，不要改写语义。\n"
                    f"校验错误：{reason}\n"
                    f"本轮控制信息：\n{self._build_turn_runtime_controls()}\n"
                    "原始输出：\n"
                    f"{invalid_content}"
                ),
            },
        ]

    def _retry_model_segments_format(
        self,
        invalid_content: str,
        reason: str,
        reasoning_kwargs: dict[str, object],
    ) -> list[dict[str, str]]:
        """
        对 schema 不合格的非空输出做一次格式纠正 retry。
        """
        logger.warning("模型输出格式不合格，正在尝试纠正。错误原因：%s；原始输出：%s", reason, invalid_content)
        retry_content, _used_json_mode = self._run_json_completion_with_empty_retry(
            self._build_format_retry_messages(invalid_content, reason),
            reasoning_kwargs,
            phase="format_retry",
        )
        return self._parse_model_segments_payload(retry_content)

    def _run_final_json_completion(
        self,
        runtime_messages: list[dict[str, object]],
        candidate_content: str,
        reasoning_kwargs: dict[str, object],
    ) -> list[dict[str, str]]:
        """
        在工具调用阶段结束后，先验收最终候选内容。

        如果工具循环最后一次模型输出已经符合 Machine Output Contract，直接复用；
        只有候选内容不合格时，才发起最终 JSON Mode 请求让模型收口格式。
        """
        candidate = str(candidate_content or "").strip()
        if candidate:
            try:
                return self._parse_model_segments_payload(candidate)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.debug("工具循环最终候选格式不合格，改用最终 JSON 收口请求。原因：%s", exc)

        generated, used_json_mode = self._run_json_completion_with_empty_retry(
            self._build_final_json_messages(runtime_messages, candidate_content),
            reasoning_kwargs,
        )

        try:
            return self._parse_model_segments_payload(generated)
        except json.JSONDecodeError as exc:
            if not used_json_mode:
                return self._retry_model_segments_format(
                    invalid_content=generated,
                    reason="输出不是合法 JSON。",
                    reasoning_kwargs=reasoning_kwargs,
                )
            raise ValueError("JSON Mode 返回了非空但不合法的 JSON。") from exc
        except ValueError as exc:
            return self._retry_model_segments_format(
                invalid_content=generated,
                reason=str(exc),
                reasoning_kwargs=reasoning_kwargs,
            )

    def _report_model_format_error(
        self,
        message_queue,
        is_text_generating_queue,
        message: str,
    ) -> None:
        """
        向 UI 报告模型输出格式错误，但保留用户原始消息。
        """
        logger.warning("模型输出格式错误：%s", message)
        message_queue.put(f"模型返回内容格式不符合要求，请重试。\n\n详细信息：\n{message}")
        self._clear_text_generating_flag_if_needed(is_text_generating_queue)

    def _record_tool_call_started(
        self,
        dp2qt_queue,
        tool_call_id: str,
        tool_name: str,
        message_index: int,
    ) -> None:
        now_ts = int(time.time())
        record = ToolCallRecordMeta(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            message_index=message_index,
            status="running",
            result_content="工具运行中...",
            duration_sec=None,
            started_at=now_ts,
            updated_at=now_ts,
        )
        self.current_chat.append_tool_call_record(record)
        one = record.to_dict()
        one["chat_id"] = self.current_chat.chat_id

        self._session_tool_call_records.append(dict(one))
        self._session_tool_call_records = self._session_tool_call_records[-300:]

        self._emit_tool_ui_event(dp2qt_queue, TOOL_CALL_START_EVENT_PREFIX, one)

    def _record_tool_call_completed(self, dp2qt_queue, tool_exec: dict) -> None:
        target_id = str(tool_exec.get("tool_call_id") or "")
        if not target_id:
            return

        records = self.current_chat.get_tool_call_records()
        for one in reversed(records):
            if one.tool_call_id == target_id:
                one.status = "completed"
                one.duration_sec = float(tool_exec.get("duration_sec") or 0.0)
                one.result_content = str(tool_exec.get("result_content") or "")
                one.ok = bool(tool_exec.get("ok", True))
                one.updated_at = int(time.time())
                self.current_chat.update_tool_call_record(one)
                payload = one.to_dict()
                payload["chat_id"] = self.current_chat.chat_id
                self._emit_tool_ui_event(dp2qt_queue, TOOL_CALL_UPDATE_EVENT_PREFIX, payload)

                for j in range(len(self._session_tool_call_records) - 1, -1, -1):
                    session_one = self._session_tool_call_records[j]
                    if str(session_one.get("tool_call_id")) == target_id:
                        self._session_tool_call_records[j] = dict(payload)
                        break
                break

    def _build_interim_message_callback(self, text_queue, is_audio_play_complete, is_text_generating_queue, dp2qt_queue, chat_id: str, turn_id: str):
        def _callback(interim_text: str, tool_calls=None, is_placeholder: bool = False):
            #print("------------\n\n\n","收到 interim callback，文本内容：", interim_text)
            # 如果收到取消信息，则不再让 LLM 生成，直接返回。
            if self.is_turn_cancelled(chat_id, turn_id):
                return
            cleaned = (interim_text or "").strip()
            raw_tool_calls = tool_calls if isinstance(tool_calls, list) else []
            has_tool_calls = len(raw_tool_calls) > 0
            if has_tool_calls and not cleaned:
                cleaned = "..."
            if not cleaned:
                return

            fallback_no_audio = has_tool_calls and bool(is_placeholder)
            character_name = self.get_current_character().character_name
            added_msg_indices: list[int] = []
            try:
                json_str = cleaned
                if json_str.startswith('```'):
                    json_str = re.sub(r'^```(?:json)?\s*', '', json_str)
                    json_str = re.sub(r'\s*```$', '', json_str)
                parsed_data = json.loads(json_str)
                if isinstance(parsed_data, list):
                    for item in parsed_data:
                        text = item.get('text', '')
                        if not text:
                            continue
                        msg = Message(
                            character_name=character_name,
                            text=text,
                            translation=item.get('translation', ''),
                            emotion=EmotionEnum.from_string(item.get('emotion', 'happiness')),
                            audio_path="NO_AUDIO" if fallback_no_audio else ("" if self.if_generate_audio else "NO_AUDIO")
                        )
                        self.current_chat.add_message(msg)
                        added_msg_indices.append(len(self.current_chat.message_list) - 1)
                elif isinstance(parsed_data, dict):
                    msg = Message(
                        character_name=character_name,
                        text=parsed_data.get('text', cleaned),
                        translation=parsed_data.get('translation', ''),
                        emotion=EmotionEnum.from_string(parsed_data.get('emotion', 'happiness')),
                        audio_path="NO_AUDIO" if fallback_no_audio else ("" if self.if_generate_audio else "NO_AUDIO")
                    )
                    self.current_chat.add_message(msg)
                    added_msg_indices.append(len(self.current_chat.message_list) - 1)
                else:
                    raise ValueError("Unexpected JSON type")
            except (json.JSONDecodeError, KeyError, AttributeError, ValueError):
                assistant_msg = Message(
                    character_name=character_name,
                    text=cleaned,
                    translation="",
                    emotion=EmotionEnum.HAPPINESS,
                    audio_path="NO_AUDIO" if fallback_no_audio else ("" if self.if_generate_audio else "NO_AUDIO")
                )
                self.current_chat.add_message(assistant_msg)
                added_msg_indices.append(len(self.current_chat.message_list) - 1)

            if has_tool_calls and added_msg_indices:
                bind_msg_index = added_msg_indices[0]
                for one_call in raw_tool_calls:
                    tool_call_id = str(getattr(one_call, "tool_call_id", "") or "")
                    tool_name = str(getattr(one_call, "name", "") or "unknown")
                    if tool_call_id:
                        self._record_tool_call_started(
                            dp2qt_queue=dp2qt_queue,
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            message_index=bind_msg_index,
                        )

            # main2.main_thread 中 text_queue 与 is_text_generating_queue 是配对消费关系。
            is_text_generating_queue.put('no_complete')
            segments = [
                self._segment_from_message(index, self.current_chat.message_list[index], fallback_no_audio)
                for index in added_msg_indices
            ]
            # 这条消息是 AI 生成的工具调用过渡消息，不是对话结束
            # 手动设置 turn_complete=False
            text_queue.put(self._build_model_response_payload(turn_id, character_name, segments, turn_complete=False))
            self._wait_audio_turn_complete(is_audio_play_complete)

        return _callback

    def _normalize_input_command(self, raw_input: object) -> Optional[dict[str, object]]:
        """
        将旧字符串输入和新结构化输入统一成命令字典。
        """
        if isinstance(raw_input, dict):
            return raw_input
        if not isinstance(raw_input, str):
            return None

        text = raw_input.strip()
        if text == "bye":
            return {"type": "exit"}
        if text in {"mask", "conv", "v", "s", "clr", "l", "start_talking", "stop_talking", "change_l2d_background"}:
            return {"type": "legacy_command", "command": text, "chat_id": self.current_chat.chat_id}
        if text.startswith("change_l2d_model"):
            return {"type": "legacy_command", "command": text, "chat_id": self.current_chat.chat_id}
        return {"type": "send_message", "chat_id": self.current_chat.chat_id, "text": raw_input}

    def _require_current_chat(self, command: dict[str, object], message_queue) -> Optional[Chat]:
        """
        校验命令的 chat_id 是否存在且等于当前对话；不一致时拒绝执行。
        """
        chat_id = command.get("chat_id")
        if not isinstance(chat_id, str) or not chat_id:
            message_queue.put("命令缺少对话编号，已拒绝执行。")
            logger.warning("拒绝缺少 chat_id 的命令：%s", command)
            return None
        chat = self.chat_manager.get_chat_by_id(chat_id)
        if chat is None:
            message_queue.put("找不到目标对话，已拒绝执行。")
            logger.warning("拒绝指向不存在 chat_id 的命令：%s", command)
            return None
        if chat_id != self.current_chat_id:
            message_queue.put("当前对话与命令目标不一致，已拒绝执行。")
            logger.warning("拒绝跨当前对话执行的命令：%s，current_chat_id=%s", command, self.current_chat_id)
            return None
        return chat

    def _handle_non_message_command(
        self,
        command: dict[str, object],
        text_queue,
        message_queue,
        char_is_converted_queue,
        change_char_queue,
        dp2qt_queue,
    ) -> bool:
        """
        处理不需要调用大模型的命令；返回 True 表示本轮已经处理完毕。
        """
        command_type = str(command.get("type") or "")
        if command_type == "exit":
            text_queue.put("bye")
            return True

        if command_type == "switch_chat":
            chat_id = command.get("chat_id")
            if isinstance(chat_id, str) and self.switch_chat(chat_id):
                character = self.get_current_character()
                message_queue.put(f"已切换为对话：{self.current_chat.name}")
                dp2qt_queue.put({"type": "chat_switched", "chat_id": chat_id, "character_name": character.character_name})
            else:
                message_queue.put("切换对话失败：找不到目标对话。")
            return True

        if command_type == "send_message":
            return False

        chat = self._require_current_chat(command, message_queue)
        if chat is None:
            return True

        if command_type in {"clear_chat", "toggle_voice", "toggle_language"}:
            legacy_command_by_type = {
                "clear_chat": "clr",
                "toggle_voice": "v",
                "toggle_language": "l",
            }
            command = {
                "type": "legacy_command",
                "command": legacy_command_by_type[command_type],
                "chat_id": chat.chat_id,
            }
            command_type = "legacy_command"

        if command_type != "legacy_command":
            message_queue.put("未知命令，已拒绝执行。")
            logger.warning("未知 qt2dp 命令：%s", command)
            return True

        legacy_command = str(command.get("command") or "")
        if legacy_command == "mask":
            if self.if_sakiko:
                char_is_converted_queue.put("maskoff")
            else:
                message_queue.put("祥子好像不在<w>")
            time.sleep(2)
            return True
        if legacy_command == "conv":
            if self.if_sakiko:
                self.sakiko_state = not self.sakiko_state
                message_queue.put("已切换为" + ("黑祥" if self.sakiko_state else "白祥"))
                char_is_converted_queue.put(self.sakiko_state)
            else:
                message_queue.put("祥子好像不在<w>")
            time.sleep(2)
            return True
        if legacy_command == "v":
            if not self.get_current_character().has_valid_voice_model():
                message_queue.put("当前角色无法进行语音合成")
                logger.error("当前角色无法开启语音合成，缺少 GPT-SoVITS 模型或参考音频文件。")
                time.sleep(2)
                return True
            self.if_generate_audio = not self.if_generate_audio
            message_queue.put("已" + ("开启" if self.if_generate_audio else "关闭") + "语音合成")
            time.sleep(2)
            return True
        if legacy_command == "s":
            message_queue.put("请在右侧对话列表中切换对话。")
            time.sleep(1)
            return True
        if legacy_command == "clr":
            self.current_chat.clear_message_list()
            self._session_tool_call_records.clear()
            message_queue.put("已清空当前对话的聊天记录")
            time.sleep(2)
            return True
        if legacy_command in {"start_talking", "stop_talking", "change_l2d_background"}:
            change_char_queue.put({"type": legacy_command})
            return True
        if legacy_command.startswith("change_l2d_model"):
            message_queue.put("参数化 Live2D 模型命令已停用，请使用模型选择界面。")
            logger.warning("拒绝旧 Live2D 模型命令：%s", legacy_command)
            return True
        if legacy_command == "l":
            self.audio_language_choice = "中英混合" if self.audio_language_choice == "日英混合" else "日英混合"
            message_queue.put("已切换语言为：" + ("中文" if self.audio_language_choice == "中英混合" else "日文"))
            time.sleep(2)
            return True

        message_queue.put("未知命令，已拒绝执行。")
        logger.warning("未知 legacy 命令：%s", legacy_command)
        return True

    def text_generator(self,
                       text_queue,
                       is_audio_play_complete,
                       is_text_generating_queue,
                       dp2qt_queue,
                       qt2dp_queue,
                       message_queue,
                       char_is_converted_queue,
                       change_char_queue,
                       AudioGenerator):
        
        # --- 定时提醒功能支持 ---
        from chat.reminder_manager import ReminderManager
        # 使用闭包回调直接将消息推入当前函数内的 qt2dp_queue，让下一次循环被读写
        reminder_mgr = ReminderManager(trigger_callback=lambda msg: qt2dp_queue.put(msg))
        
        # --- 注册依赖前端环境的动态工具 ---
        def _get_char_folder() -> str:
            return self.get_current_character().character_folder_name

        def _change_live2d_model(new_model_json: str) -> None:
            character_name = self.get_current_character().character_name
            try:
                self.current_chat.update_custom_live2d_model_meta(character_name, new_model_json)
                self.chat_manager.save()
            except Exception:
                logger.exception("工具调用切换 Live2D 模型失败")
                return
            change_char_queue.put({
                "type": "switch_live2d",
                "character_name": character_name,
                "model_json": new_model_json,
            })

        from chat.tool_calling import register_live2d_tools, register_reminder_tool, register_lottery_tool

        def _show_lottery_ui(title: str, options: list[str]) -> bool:
            payload = {
                "title": title,
                "options": options,
            }
            message_queue.put(LOTTERY_UI_EVENT_PREFIX + json.dumps(payload, ensure_ascii=False))
            return True

        register_live2d_tools(
            self.tool_runtime.tool_registry,
            get_char_folder_func=_get_char_folder,
            change_model_func=_change_live2d_model
        )
        register_reminder_tool(
            self.tool_runtime.tool_registry,
            add_reminder_func=reminder_mgr.add_reminder
        )
        register_lottery_tool(
            self.tool_runtime.tool_registry,
            show_lottery_ui_func=_show_lottery_ui
        )
        # ---------------------------------

        while True:
            #user_input = input(">>>输入bye退出聊天，输入“lan”更改语音语言，输入“model”更改LLM\n"
                               #+ f">>>当前模型：{self.model_choice}  语音语言：{self.audio_language_choice}\n>>>")
            while not AudioGenerator.is_change_complete:
                time.sleep(0.4)

            #message_queue.put(f"输入bye：退出程序	l：切换语言	m：更改LLM\n"+("conv：切换祥子状态	"if self.if_sakiko else '')+"clr：清空聊天记录	v：关闭/开启语音\n当前语言："+("中文" if self.audio_language_choice=="中英混合" else "日文")+ "		语音："+("开启" if self.if_generate_audio else "关闭")+('	mask...'if self.sakiko_state and self.if_sakiko else ''))
            message_queue.put(self.idle_texts[random.randint(0,len(self.idle_texts)-1)])
            #message_queue.put("语音："+("开启" if self.if_generate_audio else "关闭")+"语言："+("中文" if self.audio_language_choice=="中英混合" else "日文"))
            while True:
                if not qt2dp_queue.empty():
                    user_input=qt2dp_queue.get()
                    break
                time.sleep(1)
            # --------------------前面的一些判断逻辑

            command = self._normalize_input_command(user_input)
            if command is None:
                message_queue.put("无法识别输入命令，已忽略。")
                continue
            if self._handle_non_message_command(
                command=command,
                text_queue=text_queue,
                message_queue=message_queue,
                char_is_converted_queue=char_is_converted_queue,
                change_char_queue=change_char_queue,
                dp2qt_queue=dp2qt_queue,
            ):
                if command.get("type") == "exit":
                    break
                continue

            chat = self._require_current_chat(command, message_queue)
            if chat is None:
                continue

            # 保存原始用户输入（不含指令后缀），用于存储到 Chat 中
            command_text = command.get("text")
            raw_user_input = str(command_text if command_text is not None else "")
            if not raw_user_input:
                raw_user_input = "（什么也没说）"
            # 获得 qtUI 给我们的 turn id
            # 如果 qtUI 没有提供 turn_id，我们就自己生成一个，保证每轮对话都有唯一 id 供后续追踪和关联工具调用使用。
            raw_turn_id = command.get("turn_id")
            turn_id = raw_turn_id if isinstance(raw_turn_id, str) and raw_turn_id else uuid.uuid4().hex
            active_chat_id = chat.chat_id
            # 在处理用户输入前，先检查这轮对话是否已经被标记为取消了（可能用户在输入后又点了取消按钮）。如果已经取消了，就直接跳过处理，进入下一轮循环等待新输入。
            # 不过一般人手速没这么快吧（
            if self.is_turn_cancelled(active_chat_id, turn_id):
                self._emit_turn_complete(dp2qt_queue, active_chat_id, turn_id, "cancelled")
                continue

            # 将原始用户输入（不含任何指令后缀）存入 Chat
            user_msg = Message(
                character_name="User",
                text=raw_user_input,
                translation="",
                emotion=EmotionEnum.HAPPINESS,
                audio_path=""
            )
            self.current_chat.add_message(user_msg)

            character_name = self.get_current_character().character_name
            to_llm_msg = self._build_llm_messages_for_chat_turn(character_name)

            message_queue.put(f"{character_name}思考中...")
            is_text_generating_queue.put('no_complete')		#正在生成文字的标志
            # 为本轮用户输入锁定推理配置；如果用户在工具调用过程中修改 UI 设置，只会影响下一轮输入。
            reasoning_kwargs_snapshot = self._build_current_reasoning_kwargs_snapshot()
            # --------------------------
            try:
                # 构造 interim_callback（期间文本回调），用于接收大模型在决定调用工具前输出的“过渡台词”（如：“请稍等，我查一下天气”）
                # 该回调收到文本后会迅速将其推入 text_queue，触发前端的文字显示和后端的 GPT-SoVITS 语音合成。
                interim_callback = self._build_interim_message_callback(
                    text_queue=text_queue,
                    is_audio_play_complete=is_audio_play_complete,
                    is_text_generating_queue=is_text_generating_queue,
                    dp2qt_queue=dp2qt_queue,
                    chat_id=active_chat_id,
                    turn_id=turn_id,
                )
                
                # 启动带有工具调用能力的 Agent 运行循环
                # run() 内部会处理多轮 LLM 对话（如果触发了工具），直到提取出最终回答或达到循环上限才会返回 result。
                runtime_result = self.tool_runtime.run(
                    model="tool-runtime",
                    messages=self.trim_list_to_340kb(to_llm_msg),
                    llm_kwargs={
                        "stream": False,
                        "timeout": 30,
                        **reasoning_kwargs_snapshot,
                    },
                    on_interim_message=interim_callback,
                )

                # 在 AI 生成结束后，如果我们发现这轮对话在过程中被标记为取消了（可能用户在等待过程中点了取消按钮），就不处理结果了，直接进入下一轮循环等待新输入。
                if self.is_turn_cancelled(active_chat_id, turn_id):
                    self._clear_text_generating_flag_if_needed(is_text_generating_queue)
                    self._emit_turn_complete(dp2qt_queue, active_chat_id, turn_id, "cancelled")
                    continue

                for one_tool_exec in runtime_result.tool_execution_records:
                    self._record_tool_call_completed(dp2qt_queue, one_tool_exec)

                cleaned_text_of_model_response = runtime_result.final_content.strip()

                # 为后续上下文压缩和调试保留最小工具轨迹。
                self.current_chat.append_tool_call_history(
                    ToolCallHistoryRecordMeta(
                        time=int(time.time()),
                        role=character_name,
                        tool_rounds=runtime_result.tool_rounds,
                        tool_errors=runtime_result.tool_errors,
                    )
                )

            except litellm.exceptions.Timeout as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "请求超时，请检查网络连接或稍后再试。",
                    e,
                )
                self._emit_turn_complete(dp2qt_queue, active_chat_id, turn_id, "error")
                continue
            except litellm.exceptions.AuthenticationError as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "API Key 认证失败，请检查 Key 是否正确。",
                    e,
                )
                self._emit_turn_complete(dp2qt_queue, active_chat_id, turn_id, "error")
                continue
            except litellm.exceptions.RateLimitError as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "请求过于频繁，请稍后再试。",
                    e,
                )
                self._emit_turn_complete(dp2qt_queue, active_chat_id, turn_id, "error")
                continue
            except litellm.exceptions.APIConnectionError as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "与大模型网站建立连接失败，请检查网络。",
                    e,
                )
                self._emit_turn_complete(dp2qt_queue, active_chat_id, turn_id, "error")
                continue
            # 特殊捕获一个 API Key 余额不足的错误
            except litellm.exceptions.BadRequestError as e:
                # 悲伤的是，litellm 把异常封装的过头了，根本获得不了原始的状态码
                # 特殊处理 DeepSeek 无余额时返回的内容；其他 API 接口我也不清楚是否能捕获
                if "Insufficient Balance" in getattr(e, "message", str(e)):
                    self._report_model_exception(
                        message_queue,
                        is_text_generating_queue,
                        "账户余额不足，如果正在用up的api，请联系UP充值",
                        e,
                    )
                else:
                    self._report_model_exception(
                        message_queue,
                        is_text_generating_queue,
                        f"未知的请求错误，状态码：{getattr(e, 'status_code', '未知')}。",
                        e,
                    )
                self._emit_turn_complete(dp2qt_queue, active_chat_id, turn_id, "error")
                continue
            except litellm.exceptions.PermissionDeniedError as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "无法访问所请求的模型，请检查是否有权限使用该模型，或余额是否足够",
                    e,
                )
                self._emit_turn_complete(dp2qt_queue, active_chat_id, turn_id, "error")
                continue
            except Exception as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "出现了未知错误，请再试一次。",
                    e,
                )
                self._emit_turn_complete(dp2qt_queue, active_chat_id, turn_id, "error")
                continue

            # 为了修复 deepseek 经常出现格式错误的问题，这里新增了一个流程：
            # 将模型输出的消息发送回模型自己，让它自己检查格式并纠正（如果需要的话），直到输出符合要求为止。
            # 在这里请求中，强制启用 json_object 模式（如果模型支持），确保模型输出一定是 JSON 格式。
            # 如果模型输出格式有错误，会自动重试一次；如果再错误，则本次发送消息失败。
            try:
                model_segments = self._run_final_json_completion(
                    runtime_messages=runtime_result.messages,
                    candidate_content=cleaned_text_of_model_response,
                    reasoning_kwargs=reasoning_kwargs_snapshot,
                )
                added_msg_indices = self._segments_to_messages(character_name, model_segments)
            except ValueError as e:
                self._report_model_format_error(
                    message_queue,
                    is_text_generating_queue,
                    str(e),
                )
                self._emit_turn_complete(dp2qt_queue, active_chat_id, turn_id, "error")
                continue
            except Exception as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "模型格式纠正请求失败，请稍后再试。",
                    e,
                )
                self._emit_turn_complete(dp2qt_queue, active_chat_id, turn_id, "error")
                continue

            segments = [
                self._segment_from_message(index, self.current_chat.message_list[index], not self.if_generate_audio)
                for index in added_msg_indices
            ]
            text_queue.put(self._build_model_response_payload(turn_id, character_name, segments))

            while is_audio_play_complete.get() is None:
                time.sleep(0.5)		#不加就无法正常运行
