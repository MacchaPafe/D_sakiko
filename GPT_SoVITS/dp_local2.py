import random
import re
import time,os
from copy import deepcopy

import json
from typing import Sequence

import litellm

from qconfig import d_sakiko_config, THIRD_PARTY_OPENAI_COMPAT_PROVIDER_IDS
from llm_model_utils import ensure_openai_compatible_model
from character import CharacterAttributes
from log import get_logger

from chat.chat import Chat, Message, ChatManager
from chat.chat_meta import ToolCallHistoryRecordMeta, ToolCallRecordMeta
from chat.tool_calling import ToolCallingAgentRuntime
from emotion_enum import EmotionEnum

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
    if "model" in kwargs and isinstance(kwargs["model"], str):
        if "deepseek" in kwargs["model"].lower():
            extra_body = kwargs.get("extra_body")
            if not isinstance(extra_body, dict):
                extra_body = {}
            else:
                extra_body = dict(extra_body)

            if "reasoning_effort" in kwargs:
                reasoning_effort = kwargs.pop("reasoning_effort")
                if reasoning_effort == "xhigh":
                    # DeepSeek 官方文档推荐用 max 代替 xhigh 强度
                    reasoning_effort = "max"
                extra_body["reasoning_effort"] = reasoning_effort
            if "thinking" in kwargs:
                thinking_value = kwargs.pop("thinking", None)
                if isinstance(thinking_value, dict):
                    extra_body["thinking"] = {
                        "type": "enabled" if thinking_value.get("type") == "enabled" else "disabled"
                    }

            if extra_body:
                kwargs["extra_body"] = extra_body

    return litellm.completion(**kwargs)


class DSLocalAndVoiceGen:
    def __init__(self,characters: Sequence[CharacterAttributes], chat_manager: ChatManager):
        self.character_list = characters
        self.chat_manager = chat_manager

        # 为每个角色获取或创建对应的 Chat 对象
        self.character_chats: list[Chat] = []
        for character in self.character_list:
            chat_obj = self.chat_manager.get_or_create_chat_for_character(character)
            self.character_chats.append(chat_obj)

        # 语音输出的语言
        self.audio_language = ["中英混合", "日英混合"]
        self.audio_language_choice = self.audio_language[1]

        self.model = __import__('live2d_1').get_live2d()
        # 是否为角色通过 GPT-SoVITS 模型生成音频
        self.if_generate_audio = True
        # 当前对话的角色是角色列表中的第几个
        self.current_char_index = 0

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
        self.initial()

    def initial(self):
        # 聊天记录已经由 ChatManager 在初始化时加载完成，无需再从旧版 JSON 文件读取
        if self.character_list[self.current_char_index].character_name == '祥子':
            self.if_sakiko = True
        else:
            self.if_sakiko = False
        with open('./text/restr.rep', 'r', encoding='utf-8') as f:
            self.restr = f.read()

    @property
    def current_chat(self) -> Chat:
        """获取当前角色对应的 Chat 对象"""
        return self.character_chats[self.current_char_index]

    def change_character(self):
        if len(self.character_list) == 1:
            self.current_char_index = 0
        else:
            if self.current_char_index < len(self.character_list) - 1:
                self.current_char_index += 1
            else:
                self.current_char_index = 0
        if self.character_list[self.current_char_index].character_name == '祥子':
            self.if_sakiko = True
        else:
            self.if_sakiko = False

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
        # 将“工具调用 + 输出格式 + 语言要求”统一放到 system 提示词，避免污染 user 消息。
        common_rules = (
            "[全局唯一输出协议]\n"
            "1. 你每一次的回复内容都必须且只能是 JSON 数组（Array）！\n"
            "2. 严禁输出数组之外的任何解释文字、提示语、Markdown 代码块、前后缀。\n"
            "3. 每个段落对象至少包含 text 与 emotion 字段，在日文输出时还有 translation（中文翻译）字段，但绝对不可再有别的字段\n"
        )
        return common_rules

    def _build_every_round_instruction(self) -> str:
        if self.audio_language_choice == '日英混合':
            return (
                "[本轮语言模式：日文]\n"
                + "1. text 必须是日文。\n"
                + "2. 每个段落都必须包含 translation（中文翻译）。\n"
                + "3. 每个段落必须包含emotion字段，且只能取：happiness, sadness, anger, surprise, fear, disgust, like, neutral。\n"
                + "4. 分段规则：回复的text较长时请按语义停顿拆成多个段落，每段建议 1-3 句。\n"
                + "5. 输出示例，必须严格遵守该格式："
                + """[
                        {
                            "text":"第一段日文",
                            "translation":"第一段中文翻译",
                            "emotion":"neutral"
                        },
                        {
                            "text":"第二段日文",
                            "translation":"第二段中文翻译",
                            "emotion":"neutral"
                        }
                    ]
                """
                + "6. 你被提供了一些工具（如 Web 搜索、获取时间日期等），可以在需要时调用它们获取你不清楚的信息。并不一定只有当用户明确要求时才调用这些工具：\n"
                + "7. 如果你决定调用工具：你应该同时输出一段“过渡台词”，但这段台词也必须放在 JSON 数组里，格式和上面的输出示例一致。\n"
            )
        return (
                "[本轮语言模式：中文]\n"
                + "1. text 必须是中文，不要出现日语假名。\n"
                + "2. 严禁有translation 字段！\n"
                + "3. 每个段落必须包含emotion字段，且只能取：happiness, sadness, anger, surprise, fear, disgust, like, neutral。\n"
                + "4. 分段规则：回复的text较长时请按语义停顿拆成多个段落，每段建议 1-3 句。\n"
                + "5. 输出示例，必须严格遵守该格式："
                + """[
                        {
                            "text":"第一段中文",
                            "emotion":"neutral"
                        },
                        {
                            "text":"第二段中文",
                            "emotion":"neutral"
                        }
                    ]
                """
                + "6. 你被提供了一些工具（如 Web 搜索、获取时间日期等），可以在需要时调用它们获取你不清楚的信息。并不一定只有当用户明确要求时才调用这些工具：\n"
                + "7. 如果你决定调用工具：你应该同时输出一段“过渡台词”，但这段台词也必须放在 JSON 数组里，格式和上面的输出示例一致。\n"
        )

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
        """
        # 提取运行时参数，避免后续和显式传参重复。
        runtime_kwargs = dict(kwargs)
        stream = runtime_kwargs.pop("stream", False)
        timeout = runtime_kwargs.pop("timeout", 30)
        reasoning_snapshot_locked = bool(runtime_kwargs.pop("_reasoning_snapshot_locked", False))
        for protected_key in ("model", "messages", "api_key", "base_url", "tools", "tool_choice"):
            runtime_kwargs.pop(protected_key, None)

        # 根据当前 API 配置选择模型、鉴权信息和可选 base_url。
        if d_sakiko_config.use_default_deepseek_api.value:
            logger.debug("正在使用 UP 的 DeepSeek API")
            completion_kwargs = {
                "model": "deepseek/deepseek-chat",
                "messages": messages,
                "api_key": self.model,
                "stream": stream,
                "timeout": timeout,
                "tools": tools,
                "tool_choice": tool_choice,
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
                "tools": tools,
                "tool_choice": tool_choice,
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
                "tools": tools,
                "tool_choice": tool_choice,
            }
            if base_url:
                completion_kwargs["base_url"] = base_url

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
        return completion(**completion_kwargs)

    @staticmethod
    def _wait_audio_turn_complete(is_audio_play_complete):
        while is_audio_play_complete.get() is None:
            time.sleep(0.3)

    @staticmethod
    def _emit_tool_ui_event(dp2qt_queue, prefix: str, payload: dict):
        dp2qt_queue.put(prefix + json.dumps(payload, ensure_ascii=False))

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
                self._emit_tool_ui_event(dp2qt_queue, TOOL_CALL_UPDATE_EVENT_PREFIX, payload)

                for j in range(len(self._session_tool_call_records) - 1, -1, -1):
                    session_one = self._session_tool_call_records[j]
                    if str(session_one.get("tool_call_id")) == target_id:
                        self._session_tool_call_records[j] = dict(payload)
                        break
                break

    def _build_interim_message_callback(self, text_queue, is_audio_play_complete, is_text_generating_queue, dp2qt_queue):
        def _callback(interim_text: str, tool_calls=None, is_placeholder: bool = False):
            #print("------------\n\n\n","收到 interim callback，文本内容：", interim_text)
            cleaned = (interim_text or "").strip()
            raw_tool_calls = tool_calls if isinstance(tool_calls, list) else []
            has_tool_calls = len(raw_tool_calls) > 0
            if has_tool_calls and not cleaned:
                cleaned = "..."
            if not cleaned:
                return

            fallback_no_audio = has_tool_calls and bool(is_placeholder)
            character_name = self.character_list[self.current_char_index].character_name
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
            if fallback_no_audio:
                text_queue.put(NO_AUDIO_TEXT_EVENT_PREFIX + cleaned)
            else:
                text_queue.put(cleaned)
            self._wait_audio_turn_complete(is_audio_play_complete)

        return _callback

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
            return self.character_list[self.current_char_index].character_folder_name

        def _change_live2d_model(new_model_json: str) -> None:
            change_char_queue.put(f'change_l2d_model#{new_model_json}')

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

            if user_input == 'bye':
                text_queue.put('bye')
                break

            elif user_input=='mask':
                if self.if_sakiko:
                    char_is_converted_queue.put('maskoff')
                else:
                    message_queue.put("祥子好像不在<w>")
                time.sleep(2)
                continue
            elif user_input=='conv':
                if self.if_sakiko:
                    self.sakiko_state=not self.sakiko_state
                    message_queue.put("已切换为" + ("黑祥" if self.sakiko_state else "白祥"))
                    char_is_converted_queue.put(self.sakiko_state)
                else:
                    message_queue.put("祥子好像不在<w>")
                time.sleep(2)
                continue
            elif user_input == 'v':
                if not self.character_list[self.current_char_index].has_valid_voice_model():
                    message_queue.put("当前角色无法进行语音合成")
                    logger.error("当前角色无法开启语音合成，缺少 GPT-SoVITS 模型或参考音频文件。")
                    time.sleep(2)
                    continue

                self.if_generate_audio=not self.if_generate_audio
                message_queue.put("已"+("开启" if self.if_generate_audio else "关闭")+"语音合成")
                time.sleep(2)
                continue
            elif user_input=='s':
                self.change_character()
                message_queue.put(f"已切换为：{self.character_list[self.current_char_index].character_name}\n正在加载GPT-SoVITS模型...")
                change_char_queue.put('yes')
                dp2qt_queue.put("changechange")
                AudioGenerator.change_character()
                time.sleep(0.1)
                continue
            elif user_input=='clr':
                self.current_chat.clear_message_list()
                self._session_tool_call_records.clear()
                message_queue.put("已清空角色的聊天记录")
                time.sleep(2)
                continue
            elif user_input in ['start_talking','stop_talking']:
                change_char_queue.put(user_input)
                continue
            elif user_input == 'change_l2d_background':
                change_char_queue.put('change_l2d_background')
                continue
            elif user_input.startswith('change_l2d_model'):
                change_char_queue.put(user_input)
                continue

            if user_input == 'l':
                self.audio_language_choice = "中英混合" if self.audio_language_choice == '日英混合' else '日英混合'
                message_queue.put("已切换语言为："+("中文" if self.audio_language_choice=='中英混合' else "日文"))
                time.sleep(2)
                continue

            # 保存原始用户输入（不含指令后缀），用于存储到 Chat 中
            raw_user_input = user_input

            if self.if_sakiko:
                if self.sakiko_state:
                    user_input = user_input +'（本句话用黑祥语气回答!）'
                else:
                    user_input = user_input + '（本句话用白祥语气回答!）'

            user_input = user_input + "\n" + self.restr +"\n" + self._build_every_round_instruction()  # 在用户输入后追加格式要求信息，减少上下文稀释的影响
            # 将原始用户输入（不含指令后缀）存入 Chat
            user_msg = Message(
                character_name="User",
                text=raw_user_input,
                translation="",
                emotion=EmotionEnum.HAPPINESS,
                audio_path=""
            )
            self.current_chat.add_message(user_msg)

            # 构建 LLM 请求：使用 Chat.build_llm_query 生成基础历史，然后修改最后一条用户消息为带指令后缀的版本
            character_name = self.character_list[self.current_char_index].character_name
            to_llm_msg = self.current_chat.build_llm_query(perspective=character_name, is_simplify=True)  # 生成简化版本的消息列表，减少不必要的字段

            # 将最后一条 user 消息的 content 替换为带指令后缀的版本
            for i in range(len(to_llm_msg) - 1, -1, -1):
                if to_llm_msg[i]["role"] == "user":
                    # 找到最后一条 user 消息，替换其中当前用户输入部分
                    to_llm_msg[i]["content"] = to_llm_msg[i]["content"].replace(raw_user_input, user_input)
                    break

            # 添加限制信息到 system prompt
            runtime_system_instruction =self._build_runtime_system_instruction()
            system_idx = -1
            for idx, one in enumerate(to_llm_msg):
                if one.get("role") == "system":
                    system_idx = idx
                    break
            if system_idx >= 0:
                to_llm_msg[system_idx]['content'] += "\n" + runtime_system_instruction
            else:
                to_llm_msg.insert(0, {"role": "system", "content": runtime_system_instruction})

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
                continue
            except litellm.exceptions.AuthenticationError as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "API Key 认证失败，请检查 Key 是否正确。",
                    e,
                )
                continue
            except litellm.exceptions.RateLimitError as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "请求过于频繁，请稍后再试。",
                    e,
                )
                continue
            except litellm.exceptions.APIConnectionError as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "与大模型网站建立连接失败，请检查网络。",
                    e,
                )
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
                continue
            except litellm.exceptions.PermissionDeniedError as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "无法访问所请求的模型，请检查是否有权限使用该模型，或余额是否足够",
                    e,
                )
                continue
            except Exception as e:
                self._report_model_exception(
                    message_queue,
                    is_text_generating_queue,
                    "出现了未知错误，请再试一次。",
                    e,
                )
                continue

            if not cleaned_text_of_model_response:
                self.report_message_to_main_ui(
                    message_queue,
                    is_text_generating_queue,
                    "模型API返回内容为空，请检查网络，然后重试一下吧"
                )
                continue
            # 解析 LLM 回复的 JSON 格式，提取实际文本、翻译和情感
            # 现在 LLM 可能返回 JSON 数组（多段回复），每段创建一个 Message
            try:
                json_str = cleaned_text_of_model_response.strip()
                if json_str.startswith('```'):
                    json_str = re.sub(r'^```(?:json)?\s*', '', json_str)
                    json_str = re.sub(r'\s*```$', '', json_str)
                parsed_data = json.loads(json_str)

                if isinstance(parsed_data, list):
                    # JSON 数组：每个元素 → 一个 Message
                    for item in parsed_data:
                        text = item.get('text', '')
                        if not text:
                            continue
                        msg = Message(
                            character_name=character_name,
                            text=text,
                            translation=item.get('translation', ''),
                            emotion=EmotionEnum.from_string(item.get('emotion', 'happiness')),
                            audio_path="" if self.if_generate_audio else "NO_AUDIO"  # 会在 qtUI 中覆盖
                        )
                        self.current_chat.add_message(msg)
                elif isinstance(parsed_data, dict):
                    # JSON 单对象
                    msg = Message(
                        character_name=character_name,
                        text=parsed_data.get('text', cleaned_text_of_model_response),
                        translation=parsed_data.get('translation', ''),
                        emotion=EmotionEnum.from_string(parsed_data.get('emotion', 'happiness')),
                        audio_path="" if self.if_generate_audio else "NO_AUDIO"
                    )
                    self.current_chat.add_message(msg)
                else:
                    raise ValueError("Unexpected JSON type")
            except (json.JSONDecodeError, KeyError, AttributeError, ValueError):
                # 非 JSON 格式，存储原始文本
                assistant_msg = Message(
                    character_name=character_name,
                    text=cleaned_text_of_model_response,
                    translation="",
                    emotion=EmotionEnum.HAPPINESS,
                    audio_path="" if self.if_generate_audio else "NO_AUDIO"
                )
                self.current_chat.add_message(assistant_msg)

            text_queue.put(cleaned_text_of_model_response)

            while is_audio_play_complete.get() is None:
                time.sleep(0.5)		#不加就无法正常运行
