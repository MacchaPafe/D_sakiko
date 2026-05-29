from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chat_flow.events import AssistantSegmentDraft
from chat_flow.events import LotteryUiCommandEvent
from chat_flow.events import UiMessageEventType


class ChatFlowEventsTestCase(unittest.TestCase):
    """验证 chat_flow 跨线程事件协议。"""

    def test_assistant_segment_draft_serializes_queue_payload_fields(self) -> None:
        """assistant 片段草稿应序列化为队列可传递字段。"""
        draft = AssistantSegmentDraft(
            message_index=4,
            text="こんにちは。",
            translation="你好。",
            emotion="happiness",
            force_no_audio=True,
        )

        self.assertEqual(
            draft.to_dict(),
            {
                "message_index": 4,
                "text": "こんにちは。",
                "translation": "你好。",
                "emotion": "happiness",
                "force_no_audio": True,
            },
        )

    def test_lottery_ui_command_event_keeps_message_event_type(self) -> None:
        """抽签窗口事件仍保留 Qt 消息通道使用的事件类型。"""
        event = LotteryUiCommandEvent(title="晚饭", options=("寿司", "拉面"))

        self.assertEqual(event.event_type, UiMessageEventType.LOTTERY_COMMAND)
        self.assertEqual(event.options, ("寿司", "拉面"))


if __name__ == "__main__":
    unittest.main()
