from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from chat.chat import Chat
from chat_flow.audio_synthesizer import NO_AUDIO
from chat_flow.events import AssistantSegmentDraft


class ChatLookup(Protocol):
    """描述状态更新器需要的对话查找接口。"""

    def get_chat_by_id(self, chat_id: str) -> Chat | None:
        """按稳定编号查找对话。"""


@dataclass(frozen=True)
class ConversationStateUpdateResult:
    """描述一次对话状态更新的结果。"""

    success: bool
    updated_count: int
    reason: str = ""


class ConversationStateUpdater:
    """集中写入生成回复的音频路径、翻译和无音频标记。"""

    def __init__(self, chat_lookup: ChatLookup) -> None:
        """创建基于对话数据模型的状态更新器。"""
        self._chat_lookup = chat_lookup

    def set_segment_result(
        self,
        *,
        chat_id: str,
        message_index: int,
        audio_path: str,
        translation: str,
    ) -> ConversationStateUpdateResult:
        """设置单个 assistant 片段的生成音频路径和翻译。"""
        chat = self._chat_lookup.get_chat_by_id(chat_id)
        if chat is None:
            return ConversationStateUpdateResult(False, 0, "missing_chat")
        if not self._is_valid_message_index(chat, message_index):
            return ConversationStateUpdateResult(False, 0, "invalid_message_index")

        message = chat.message_list[message_index]
        message.audio_path = audio_path
        message.translation = translation
        return ConversationStateUpdateResult(True, 1)

    def mark_remaining_no_audio(
        self,
        *,
        chat_id: str,
        segment_drafts: list[AssistantSegmentDraft],
        start_index: int = 0,
    ) -> ConversationStateUpdateResult:
        """将剩余尚未生成音频的 assistant 片段标记为无音频。"""
        chat = self._chat_lookup.get_chat_by_id(chat_id)
        if chat is None:
            return ConversationStateUpdateResult(False, 0, "missing_chat")

        updated_count = 0
        for draft in segment_drafts[max(0, start_index):]:
            message_index = draft.message_index
            if not self._is_valid_message_index(chat, message_index):
                continue
            message = chat.message_list[message_index]
            if message.audio_path:
                continue
            message.audio_path = NO_AUDIO
            updated_count += 1
        return ConversationStateUpdateResult(True, updated_count)

    @staticmethod
    def _is_valid_message_index(chat: Chat, message_index: int) -> bool:
        """判断消息索引是否指向当前对话中的一条消息。"""
        return 0 <= message_index < len(chat.message_list)
