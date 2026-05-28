from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from chat.chat import Chat
from chat.chat import ChatManager
from chat.chat import Message
from chat_flow.events import AssistantSegmentDraft
from chat_flow.state_updater import ConversationStateUpdater
from emotion_enum import EmotionEnum


class ConversationStateUpdaterTestCase(unittest.TestCase):
    """验证生成结果状态更新器的可观察行为。"""

    def test_set_segment_result_updates_audio_path_and_translation(self) -> None:
        """按对话编号和消息索引写回生成音频路径与翻译。"""
        chat = self._chat_with_messages()
        updater = ConversationStateUpdater(ChatManager([chat]))

        result = updater.set_segment_result(
            chat_id=chat.chat_id,
            message_index=0,
            audio_path="/tmp/generated.wav",
            translation="新的翻译",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.updated_count, 1)
        self.assertEqual(chat.message_list[0].audio_path, "/tmp/generated.wav")
        self.assertEqual(chat.message_list[0].translation, "新的翻译")

    def test_set_segment_result_reports_missing_chat(self) -> None:
        """目标对话不存在时返回失败结果且不抛异常。"""
        updater = ConversationStateUpdater(ChatManager([]))

        result = updater.set_segment_result(
            chat_id="missing",
            message_index=0,
            audio_path="/tmp/generated.wav",
            translation="新的翻译",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.updated_count, 0)

    def test_set_segment_result_reports_invalid_message_index(self) -> None:
        """消息索引越界时返回失败结果且不修改对话。"""
        chat = self._chat_with_messages()
        updater = ConversationStateUpdater(ChatManager([chat]))

        result = updater.set_segment_result(
            chat_id=chat.chat_id,
            message_index=99,
            audio_path="/tmp/generated.wav",
            translation="新的翻译",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(chat.message_list[0].audio_path, "")
        self.assertEqual(chat.message_list[0].translation, "旧翻译")

    def test_mark_remaining_no_audio_updates_empty_audio_paths_only(self) -> None:
        """将剩余片段中尚未生成的音频标记为无音频。"""
        chat = self._chat_with_messages()
        chat.message_list[1].audio_path = "/tmp/already.wav"
        updater = ConversationStateUpdater(ChatManager([chat]))
        drafts = [
            self._draft(0),
            self._draft(1),
            self._draft(2),
        ]

        result = updater.mark_remaining_no_audio(
            chat_id=chat.chat_id,
            segment_drafts=drafts,
            start_index=0,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.updated_count, 2)
        self.assertEqual(chat.message_list[0].audio_path, "NO_AUDIO")
        self.assertEqual(chat.message_list[1].audio_path, "/tmp/already.wav")
        self.assertEqual(chat.message_list[2].audio_path, "NO_AUDIO")

    def _chat_with_messages(self) -> Chat:
        """创建包含三条 assistant 消息的测试对话。"""
        return Chat(
            message_list=[
                Message("祥子", "第一句", "旧翻译", EmotionEnum.HAPPINESS, ""),
                Message("祥子", "第二句", "", EmotionEnum.HAPPINESS, ""),
                Message("祥子", "第三句", "", EmotionEnum.HAPPINESS, ""),
            ],
        )

    def _draft(self, message_index: int) -> AssistantSegmentDraft:
        """创建测试用 assistant 片段草稿。"""
        return AssistantSegmentDraft(
            message_index=message_index,
            text="文本",
            translation="",
            emotion="LABEL_0",
            force_no_audio=False,
        )


if __name__ == "__main__":
    unittest.main()
