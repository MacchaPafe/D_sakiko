from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from typing import Mapping
from typing import TypeAlias

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | dict[str, "JsonValue"] | list["JsonValue"]
class DispatcherInputEventType(StrEnum):
    """发送到调度器输入队列的事件类型。"""

    MODEL_RESPONSE = "model_response"
    EXIT = "exit"


class ConversationUiEventType(StrEnum):
    """发送到对话 UI 队列的事件类型。"""

    ASSISTANT_SEGMENT_READY = "assistant_segment_ready"
    ASSISTANT_TURN_PHASE = "assistant_turn_phase"
    ASSISTANT_TURN_COMPLETE = "assistant_turn_complete"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_UPDATED = "tool_call_updated"
    TRANSIENT_CONVERSATION_MESSAGE = "transient_conversation_message"


class UiMessageEventType(StrEnum):
    """发送到 Qt 消息提示队列的事件类型。"""

    APPLICATION_QUIT = "application_quit"
    LOTTERY_COMMAND = "lottery_command"


class TurnPhase(StrEnum):
    """对话轮次在 UI 中展示的处理阶段。"""

    LLM = "llm"
    TTS = "tts"
    RENDERING = "rendering"


class TurnStatus(StrEnum):
    """对话轮次结束时的状态。"""

    OK = "ok"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True)
class DispatcherInputEvent:
    """描述发送给文本生成调度器的结构化输入事件。"""

    event_type: DispatcherInputEventType
    data: dict[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """序列化为当前调度器输入队列使用的字典协议。"""
        payload = dict(self.data)
        payload["type"] = self.event_type.value
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> DispatcherInputEvent:
        """从队列载荷解析调度器输入事件，并拒绝非结构化载荷。"""
        if not isinstance(payload, Mapping):
            raise TypeError("dispatcher input event payload must be a mapping")

        raw_type = payload.get("type")
        if not isinstance(raw_type, str):
            raise ValueError("dispatcher input event payload is missing a string type")
        event_type = DispatcherInputEventType(raw_type)

        data: dict[str, JsonValue] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                raise TypeError("dispatcher input event payload keys must be strings")
            if key == "type":
                continue
            data[key] = _to_json_value(value)
        return cls(event_type=event_type, data=data)


@dataclass(frozen=True)
class AssistantSegmentDraft:
    """描述 assistant 回复片段的跨线程草稿。"""

    message_index: int
    text: str
    translation: str
    emotion: str
    force_no_audio: bool

    def to_dict(self) -> dict[str, JsonValue]:
        """序列化为可放入队列的字典。"""
        return {
            "message_index": self.message_index,
            "text": self.text,
            "translation": self.translation,
            "emotion": self.emotion,
            "force_no_audio": self.force_no_audio,
        }


@dataclass(frozen=True)
class ApplicationQuitUiMessageEvent:
    """要求 Qt 主窗口保存数据并退出程序的 UI 消息事件。"""

    event_type: UiMessageEventType = UiMessageEventType.APPLICATION_QUIT


@dataclass(frozen=True)
class LotteryUiCommandEvent:
    """要求 Qt 主窗口打开抽签窗口的 UI 消息事件。"""

    title: str
    options: tuple[str, ...]
    event_type: UiMessageEventType = UiMessageEventType.LOTTERY_COMMAND


@dataclass(frozen=True)
class TransientConversationMessageEvent:
    """描述只渲染到当前聊天框、不写入对话数据的临时对话文本。"""

    character_name: str
    text: str
    chat_id: str = ""
    stream: bool = True
    event_type: ConversationUiEventType = ConversationUiEventType.TRANSIENT_CONVERSATION_MESSAGE

    def to_dict(self) -> dict[str, JsonValue]:
        """序列化为当前对话 UI 队列使用的字典协议。"""
        payload: dict[str, JsonValue] = {
            "type": self.event_type.value,
            "character_name": self.character_name,
            "text": self.text,
            "stream": self.stream,
        }
        if self.chat_id:
            payload["chat_id"] = self.chat_id
        return payload


@dataclass(frozen=True)
class ConversationUiEvent:
    """描述从后台发送到 Qt 对话界面的结构化事件。"""

    event_type: ConversationUiEventType
    chat_id: str = ""
    turn_id: str = ""
    data: dict[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """序列化为当前队列边界使用的字典协议。"""
        payload = dict(self.data)
        payload["type"] = self.event_type.value
        if self.chat_id:
            payload["chat_id"] = self.chat_id
        if self.turn_id:
            payload["turn_id"] = self.turn_id
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> ConversationUiEvent:
        """从队列载荷解析 UI 事件，并拒绝非结构化载荷。"""
        if not isinstance(payload, Mapping):
            raise TypeError("conversation UI event payload must be a mapping")

        raw_type = payload.get("type")
        if not isinstance(raw_type, str):
            raise ValueError("conversation UI event payload is missing a string type")
        event_type = ConversationUiEventType(raw_type)

        chat_id = payload.get("chat_id")
        turn_id = payload.get("turn_id")
        data: dict[str, JsonValue] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                raise TypeError("conversation UI event payload keys must be strings")
            if key in {"type", "chat_id", "turn_id"}:
                continue
            data[key] = _to_json_value(value)

        return cls(
            event_type=event_type,
            chat_id=chat_id if isinstance(chat_id, str) else "",
            turn_id=turn_id if isinstance(turn_id, str) else "",
            data=data,
        )


def _to_json_value(value: object) -> JsonValue:
    """校验并返回能安全跨队列传递的 JSON 形值。"""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_to_json_value(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("conversation UI event nested payload keys must be strings")
            result[key] = _to_json_value(item)
        return result
    raise TypeError(f"conversation UI event payload value is not serializable: {type(value).__name__}")


def is_legacy_tool_call_payload(payload: object) -> bool:
    """判断载荷是否是已废弃的字符串前缀工具调用事件。"""
    return isinstance(payload, str) and payload.startswith(("__TOOL_CALL_START__:", "__TOOL_CALL_UPDATE__:"))
