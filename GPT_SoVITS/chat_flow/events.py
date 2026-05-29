from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | dict[str, "JsonValue"] | list["JsonValue"]


class UiMessageEventType(StrEnum):
    """发送到 Qt 消息提示队列的事件类型。"""

    LOTTERY_COMMAND = "lottery_command"


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
class LotteryUiCommandEvent:
    """要求 Qt 主窗口打开抽签窗口的 UI 消息事件。"""

    title: str
    options: tuple[str, ...]
    event_type: UiMessageEventType = UiMessageEventType.LOTTERY_COMMAND
