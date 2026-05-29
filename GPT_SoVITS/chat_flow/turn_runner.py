from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Protocol
from typing import TypeAlias

from chat.chat import Chat
from chat.chat import Message
from chat.chat_meta import ToolCallHistoryRecordMeta
from chat_flow.audio_scheduler import PENDING_AUDIO
from chat_flow.tool_context import ChatToolCallRecordSink
from chat_flow.tool_context import ForegroundResolver
from chat_flow.tool_context import ToolSideEffectPorts
from chat_flow.tool_context import ToolTurnContext
from emotion_enum import EmotionEnum

if TYPE_CHECKING:
    from chat.tool_calling import AgentRunResult
    from chat.tool_calling import ToolCallingAgentRuntime


CompletionResponse: TypeAlias = object


class CompletionFunction(Protocol):
    """描述 one-shot turn runner 需要的大模型补全接口。"""

    def __call__(self, **kwargs: object) -> CompletionResponse:
        """执行一次补全请求。"""


@dataclass(frozen=True)
class ChatTurnSettings:
    """保存一轮文本生成开始时捕获的不可变设置。"""

    audio_enabled: bool
    audio_language_choice: str
    sakiko_state: bool = True
    character: object | None = None
    reasoning_kwargs: dict[str, object] | None = None
    cancellation_token: CancellationToken | None = None


class CancellationToken:
    """保存一轮对话是否已经收到取消请求。"""

    def __init__(self) -> None:
        """初始化为未取消状态。"""
        self._cancelled = False

    def cancel(self) -> None:
        """标记当前轮次已取消。"""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """返回当前轮次是否已取消。"""
        return self._cancelled


@dataclass(frozen=True)
class ChatTurnStarted:
    """表示一轮对话已经开始。"""

    chat_id: str
    turn_id: str


@dataclass(frozen=True)
class ChatTurnPhaseChanged:
    """表示一轮对话进入新的处理阶段。"""

    chat_id: str
    turn_id: str
    phase: str


@dataclass(frozen=True)
class ChatUserMessageCommitted:
    """表示用户消息已经写入目标对话历史。"""

    chat_id: str
    turn_id: str
    message_index: int
    text: str


@dataclass(frozen=True)
class ChatAssistantSegmentCommitted:
    """表示 assistant 文本片段已经写入目标对话历史。"""

    chat_id: str
    turn_id: str
    message_index: int
    character_name: str
    text: str
    translation: str
    emotion: str
    audio_path: str


@dataclass(frozen=True)
class ChatToolCallRecordsUpdated:
    """表示一轮对话的工具调用记录已经写入或更新。"""

    chat_id: str
    turn_id: str


@dataclass(frozen=True)
class ChatPendingUserTurnQueued:
    """表示用户输入已进入当前对话的非持久排队状态。"""

    chat_id: str
    turn_id: str
    text: str


@dataclass(frozen=True)
class ChatTurnCancelled:
    """表示一轮对话已取消。"""

    chat_id: str
    turn_id: str


@dataclass(frozen=True)
class ChatTurnFailed:
    """表示一轮对话失败。"""

    chat_id: str
    turn_id: str
    message: str


@dataclass(frozen=True)
class ChatTurnCompleted:
    """表示一轮对话已经成功完成。"""

    chat_id: str
    turn_id: str


@dataclass(frozen=True)
class ModelSegment:
    """保存模型结构化输出中的一个 assistant 片段。"""

    text: str
    emotion: str
    translation: str


ChatTurnEvent: TypeAlias = (
    ChatTurnStarted
    | ChatTurnPhaseChanged
    | ChatUserMessageCommitted
    | ChatAssistantSegmentCommitted
    | ChatToolCallRecordsUpdated
    | ChatPendingUserTurnQueued
    | ChatTurnCancelled
    | ChatTurnFailed
    | ChatTurnCompleted
)


class ChatGenerationSession:
    """保存一轮生成可复用的长生命周期依赖。"""

    def __init__(
        self,
        completion: CompletionFunction,
        model_name: str,
        *,
        tool_runtime: ToolCallingAgentRuntime | None = None,
        foreground_resolver: ForegroundResolver | None = None,
        side_effect_ports: ToolSideEffectPorts | None = None,
    ) -> None:
        """保存补全函数与模型名称。"""
        self._completion = completion
        self._model_name = model_name
        self._tool_runtime = tool_runtime
        self._foreground_resolver = foreground_resolver
        self._side_effect_ports = side_effect_ports or ToolSideEffectPorts()

    def run_foreground_turn(
        self,
        *,
        chat: Chat,
        chat_id: str,
        turn_id: str,
        user_text: str,
        character_name: str,
        settings: ChatTurnSettings,
    ) -> list[ChatTurnEvent]:
        """执行一轮前台文本生成。"""
        runner = TurnRunner(
            completion=self._completion,
            model_name=self._model_name,
            tool_runtime=self._tool_runtime,
            foreground_resolver=self._foreground_resolver,
            side_effect_ports=self._side_effect_ports,
        )
        return runner.run(
            chat=chat,
            chat_id=chat_id,
            turn_id=turn_id,
            user_text=user_text,
            character_name=character_name,
            settings=settings,
        )


class TurnRunner:
    """执行一轮显式目标 chat 的文本生成。"""

    def __init__(
        self,
        completion: CompletionFunction,
        model_name: str,
        *,
        tool_runtime: ToolCallingAgentRuntime | None = None,
        foreground_resolver: ForegroundResolver | None = None,
        side_effect_ports: ToolSideEffectPorts | None = None,
    ) -> None:
        """保存一轮运行所需的大模型入口。"""
        self._completion = completion
        self._model_name = model_name
        self._tool_runtime = tool_runtime
        self._foreground_resolver = foreground_resolver
        self._side_effect_ports = side_effect_ports or ToolSideEffectPorts()

    def run(
        self,
        *,
        chat: Chat,
        chat_id: str,
        turn_id: str,
        user_text: str,
        character_name: str,
        settings: ChatTurnSettings,
    ) -> list[ChatTurnEvent]:
        """提交用户消息、请求模型并写入 assistant 回复。"""
        if chat.chat_id != chat_id:
            raise ValueError("turn runner target chat_id does not match chat object")

        events: list[ChatTurnEvent] = [ChatTurnStarted(chat_id=chat_id, turn_id=turn_id)]
        with chat.runtime.lock:
            user_message = Message(
                character_name="User",
                text=user_text,
                translation="",
                emotion=EmotionEnum.HAPPINESS,
                audio_path="",
            )
            chat.add_message(user_message)
            user_message_index = len(chat.message_list) - 1
            llm_messages = build_runtime_llm_messages(
                chat=chat,
                character_name=character_name,
                settings=settings,
            )

        events.append(ChatUserMessageCommitted(
            chat_id=chat_id,
            turn_id=turn_id,
            message_index=user_message_index,
            text=user_text,
        ))
        events.append(ChatTurnPhaseChanged(chat_id=chat_id, turn_id=turn_id, phase="llm"))

        if _is_cancelled(settings.cancellation_token):
            events.append(ChatTurnCancelled(chat_id=chat_id, turn_id=turn_id))
            return events

        try:
            tool_result: AgentRunResult | None = None
            tool_record_sink: ChatToolCallRecordSink | None = None
            if self._tool_runtime is None or self._foreground_resolver is None:
                response = self._completion(
                    model=self._model_name,
                    messages=llm_messages,
                    stream=False,
                    **(settings.reasoning_kwargs or {}),
                )
                content = extract_response_content(response)
                base_messages = llm_messages
            else:
                tool_record_sink = ChatToolCallRecordSink(
                    chat=chat,
                    chat_id=chat_id,
                    turn_id=turn_id,
                    character_name=character_name,
                    audio_enabled=settings.audio_enabled,
                )
                tool_result = self._tool_runtime.run(
                    model=self._model_name,
                    messages=llm_messages,
                    llm_kwargs={
                        "stream": False,
                        **(settings.reasoning_kwargs or {}),
                    },
                    tool_call_record_sink=tool_record_sink,
                    tool_context=ToolTurnContext(
                        chat_id=chat_id,
                        turn_id=turn_id,
                        character_name=character_name,
                        foreground_resolver=self._foreground_resolver,
                        side_effect_ports=self._side_effect_ports,
                    ),
                )
                content = tool_result.final_content
                base_messages = tool_result.messages
            if _is_cancelled(settings.cancellation_token):
                events.append(ChatTurnCancelled(chat_id=chat_id, turn_id=turn_id))
                return events
            if tool_record_sink is not None:
                for snapshot in tool_record_sink.get_message_snapshots():
                    events.append(ChatAssistantSegmentCommitted(
                        chat_id=chat_id,
                        turn_id=turn_id,
                        message_index=snapshot.message_index,
                        character_name=character_name,
                        text=snapshot.text,
                        translation=snapshot.translation,
                        emotion=snapshot.emotion,
                        audio_path=snapshot.audio_path,
                    ))
                if tool_record_sink.has_record_updates():
                    events.append(ChatToolCallRecordsUpdated(chat_id=chat_id, turn_id=turn_id))
            if tool_result is not None and (tool_result.tool_rounds or tool_result.tool_errors):
                with chat.runtime.lock:
                    chat.append_tool_call_history(
                        ToolCallHistoryRecordMeta(
                            time=int(time.time()),
                            role=character_name,
                            tool_rounds=tool_result.tool_rounds,
                            tool_errors=tool_result.tool_errors,
                        )
                    )
            segments = parse_or_normalize_model_segments(
                content,
                audio_language_choice=settings.audio_language_choice,
                completion=self._completion,
                model_name=self._model_name,
                base_messages=base_messages,
                reasoning_kwargs=settings.reasoning_kwargs or {},
            )
        except Exception as exc:
            events.append(ChatTurnFailed(chat_id=chat_id, turn_id=turn_id, message=str(exc)))
            return events

        with chat.runtime.lock:
            if _is_cancelled(settings.cancellation_token):
                events.append(ChatTurnCancelled(chat_id=chat_id, turn_id=turn_id))
                return events
            for segment in segments:
                audio_path = PENDING_AUDIO if settings.audio_enabled else "NO_AUDIO"
                message = Message(
                    character_name=character_name,
                    text=segment.text,
                    translation=segment.translation,
                    emotion=EmotionEnum.from_string(segment.emotion),
                    audio_path=audio_path,
                )
                chat.add_message(message)
                events.append(ChatAssistantSegmentCommitted(
                    chat_id=chat_id,
                    turn_id=turn_id,
                    message_index=len(chat.message_list) - 1,
                    character_name=character_name,
                    text=message.text,
                    translation=message.translation,
                    emotion=message.emotion.as_label(),
                    audio_path=message.audio_path,
                ))

        events.append(ChatTurnCompleted(chat_id=chat_id, turn_id=turn_id))
        return events


def _is_cancelled(cancellation_token: CancellationToken | None) -> bool:
    """判断可选取消 token 是否已经取消。"""
    return cancellation_token is not None and cancellation_token.is_cancelled()


def build_runtime_llm_messages(
    *,
    chat: Chat,
    character_name: str,
    settings: ChatTurnSettings,
) -> list[dict[str, object]]:
    """构造本轮发送给模型的历史，并注入渲染器需要的输出协议。"""
    messages = [
        dict(message)
        for message in chat.build_llm_query(character_name, is_simplify=True)
    ]
    append_machine_output_contract(
        messages,
        audio_language_choice=settings.audio_language_choice,
    )
    messages.append({
        "role": "user",
        "content": build_turn_runtime_controls(
            character_name=character_name,
            settings=settings,
        ),
    })
    return messages


def append_machine_output_contract(
    messages: list[dict[str, object]],
    *,
    audio_language_choice: str,
) -> None:
    """把 Machine Output Contract 追加到 system prompt，缺失 system 时新建一条。"""
    contract = build_machine_output_contract_instruction(audio_language_choice)
    for message in messages:
        if message.get("role") != "system":
            continue
        content = str(message.get("content") or "")
        if "Machine Output Contract" in content:
            return
        message["content"] = content + "\n\n" + contract if content else contract
        return
    messages.insert(0, {"role": "system", "content": contract})


def build_machine_output_contract_instruction(audio_language_choice: str) -> str:
    """生成模型必须遵守的结构化段落输出协议。"""
    reply_language = reply_language_code(audio_language_choice)
    if reply_language == "ja_with_zh_translation":
        translation_rule = "每个对象必须包含 translation 字段，内容是 text 的中文翻译。"
        example = (
            '[{"text":"今日は少し疲れました。",'
            '"translation":"今天稍微有点累。",'
            '"emotion":"happiness"}]'
        )
    else:
        translation_rule = "每个对象严禁包含 translation 字段。"
        example = '[{"text":"今天稍微有点累。","emotion":"happiness"}]'

    return (
        "# Machine Output Contract\n"
        "你不是直接向用户输出自由文本。你正在为语音与聊天渲染器生成结构化数据。\n"
        "最终回复必须只返回一个 JSON array，不要输出 Markdown 代码块、解释文字、前缀或后缀。\n"
        "这个 JSON array 不是 OpenAI messages；不要输出 role、content、assistant、user 等字段。\n"
        "JSON array 的每个元素都必须是一个对话段落 JSON object，只能包含 text、emotion、translation 这些字段。\n"
        "text 是角色实际说出口的台词；emotion 必须是 happiness、sadness、anger、surprise、fear、disgust、like 之一。\n"
        f"{translation_rule}\n"
        f"当前 runtime.reply_language 为 {reply_language}。\n"
        f"示例：{example}\n"
        "请求末尾可能包含一条 <runtime_controls> 消息。它是应用传入的本轮元数据，不是用户说出口的话。"
    )


def build_turn_runtime_controls(
    *,
    character_name: str,
    settings: ChatTurnSettings,
) -> str:
    """构造本轮语言和角色状态控制信息。"""
    return (
        "<runtime_controls>\n"
        f"reply_language: {reply_language_code(settings.audio_language_choice)}\n"
        f"sakiko_tone: {sakiko_tone_code(character_name, settings)}\n"
        "</runtime_controls>"
    )


def reply_language_code(audio_language_choice: str) -> str:
    """将界面语言选项转换为模型提示中的短代码。"""
    if audio_language_choice == "日英混合":
        return "ja_with_zh_translation"
    return "zh_only"


def sakiko_tone_code(character_name: str, settings: ChatTurnSettings) -> str:
    """返回祥子专属语气控制代码，非祥子角色返回 none。"""
    names = {character_name}
    character = settings.character
    if character is not None:
        names.add(str(getattr(character, "character_name", "") or ""))
    if "祥子" not in names:
        return "none"
    return "dark" if settings.sakiko_state else "light"


def strip_json_markdown_fence(text: str) -> str:
    """去掉模型偶尔包裹在 JSON 外层的 Markdown 代码块标记。"""
    json_str = text.strip()
    if json_str.startswith("```"):
        json_str = re.sub(r"^```(?:json)?\s*", "", json_str)
        json_str = re.sub(r"\s*```$", "", json_str)
    return json_str.strip()


def extract_response_content(response: CompletionResponse) -> str:
    """从 LiteLLM/OpenAI 风格响应中提取 assistant content。"""
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


def parse_or_normalize_model_segments(
    content: str,
    *,
    audio_language_choice: str,
    completion: CompletionFunction,
    model_name: str,
    base_messages: list[dict[str, object]],
    reasoning_kwargs: dict[str, object],
) -> list[ModelSegment]:
    """先验收候选回复，必要时请求模型收口为最终 JSON 段落。"""
    try:
        return parse_model_segments_payload(
            content,
            audio_language_choice=audio_language_choice,
        )
    except (json.JSONDecodeError, ValueError):
        response = completion(
            model=model_name,
            messages=build_final_json_messages(
                base_messages,
                content,
                audio_language_choice=audio_language_choice,
            ),
            stream=False,
            **reasoning_kwargs,
        )
        normalized_content = extract_response_content(response)
        try:
            return parse_model_segments_payload(
                normalized_content,
                audio_language_choice=audio_language_choice,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            repair_response = completion(
                model=model_name,
                messages=build_format_retry_messages(
                    invalid_content=normalized_content,
                    reason=str(exc),
                    audio_language_choice=audio_language_choice,
                ),
                stream=False,
                **reasoning_kwargs,
            )
        return parse_model_segments_payload(
            extract_response_content(repair_response),
            audio_language_choice=audio_language_choice,
        )


def build_final_json_messages(
    base_messages: list[dict[str, object]],
    candidate_content: str,
    *,
    audio_language_choice: str,
) -> list[dict[str, object]]:
    """构造把候选回复转换成 Machine Output Contract JSON array 的请求。"""
    messages = [dict(message) for message in base_messages]
    append_machine_output_contract(
        messages,
        audio_language_choice=audio_language_choice,
    )
    candidate = candidate_content.strip()
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
            "只输出符合 Machine Output Contract 的 JSON array。\n"
            "注意：这里要输出的是段落数组，不是 OpenAI messages；"
            "不要输出 role/content，不要输出 [{\"role\":\"assistant\",\"content\":\"...\"}]。"
        ),
    })
    return messages


def build_format_retry_messages(
    *,
    invalid_content: str,
    reason: str,
    audio_language_choice: str,
) -> list[dict[str, object]]:
    """构造一次针对错误 JSON/schema 输出的格式纠正请求。"""
    return [
        {
            "role": "system",
            "content": build_machine_output_contract_instruction(audio_language_choice),
        },
        {
            "role": "user",
            "content": (
                "下面是模型输出，可能不是合法 JSON，或不符合 Machine Output Contract schema。\n"
                "请只纠正 JSON 结构、字段和缺失项，不要改写语义。\n"
                f"校验错误：{reason}\n"
                "特别注意：不要输出 OpenAI messages，不要输出 role/content。\n"
                "原始输出：\n"
                f"{invalid_content}"
            ),
        },
    ]


def parse_model_segments_payload(content: str, *, audio_language_choice: str) -> list[ModelSegment]:
    """解析并校验模型输出的顶层 JSON array 对话段落。"""
    json_str = strip_json_markdown_fence(content)
    if not json_str:
        raise ValueError("模型返回内容为空。")

    parsed_data = json.loads(json_str)
    if not isinstance(parsed_data, list):
        raise ValueError("模型输出的 JSON 顶层必须是 array。")
    if not parsed_data:
        raise ValueError("模型输出的 JSON array 不能为空。")

    need_translation = audio_language_choice == "日英混合"
    segments: list[ModelSegment] = []
    for index, item in enumerate(parsed_data):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 个段落不是 JSON object。")
        segments.append(_parse_segment(index, item, need_translation=need_translation))
    return segments


def _parse_segment(index: int, item: dict[object, object], *, need_translation: bool) -> ModelSegment:
    """解析并校验单个模型段落。"""
    allowed_keys = {"text", "emotion", "translation"}
    extra_keys = {str(key) for key in item.keys()} - allowed_keys
    if extra_keys:
        raise ValueError(f"第 {index + 1} 个段落包含多余字段：{sorted(extra_keys)}。")

    text = str(item.get("text") or "").strip()
    if not text:
        raise ValueError(f"第 {index + 1} 个段落缺少 text。")

    emotion = str(item.get("emotion") or "").strip().lower()
    if emotion not in allowed_model_emotions():
        raise ValueError(f"第 {index + 1} 个段落 emotion 不合法：{emotion or '空'}。")

    translation = ""
    if need_translation:
        translation = str(item.get("translation") or "").strip()
        if not translation:
            raise ValueError(f"第 {index + 1} 个段落缺少 translation。")

    return ModelSegment(text=text, emotion=emotion, translation=translation)


def allowed_model_emotions() -> set[str]:
    """返回机器输出协议允许的情绪值。"""
    return {
        "happiness",
        "sadness",
        "anger",
        "surprise",
        "fear",
        "disgust",
        "like",
    }
