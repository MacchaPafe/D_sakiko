from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chat_flow.events import ConversationUiEvent
from chat_flow.events import ConversationUiEventType
from chat_flow.events import DispatcherInputEvent
from chat_flow.events import DispatcherInputEventType
from chat_flow.events import AssistantSegmentDraft
from chat_flow.events import TurnPhase
from chat_flow.events import TurnStatus
from chat_flow.events import is_legacy_tool_call_payload


class ChatFlowEventsTestCase(unittest.TestCase):
    """验证 chat_flow 跨线程事件协议。"""

    def test_tool_call_started_event_round_trips_as_serialized_payload(self) -> None:
        """工具调用开始事件应以保留旧 wire type 的字典完成往返。"""
        event = ConversationUiEvent(
            event_type=ConversationUiEventType.TOOL_CALL_STARTED,
            chat_id="chat-1",
            data={
                "tool_call_id": "call-1",
                "tool_name": "search",
                "message_index": 2,
                "status": "running",
            },
        )

        payload = event.to_dict()
        parsed = ConversationUiEvent.from_payload(payload)

        self.assertEqual(ConversationUiEventType.TOOL_CALL_STARTED.value, "tool_call_started")
        self.assertEqual(TurnPhase.TTS.value, "tts")
        self.assertEqual(TurnStatus.OK.value, "ok")
        self.assertEqual(payload["type"], "tool_call_started")
        self.assertEqual(parsed, event)

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

    def test_old_string_tool_call_payload_is_rejected_by_parser(self) -> None:
        """旧字符串前缀工具调用载荷应被协议解析器拒绝。"""
        payload = '__TOOL_CALL_START__:{"tool_call_id":"legacy"}'

        self.assertTrue(is_legacy_tool_call_payload(payload))
        with self.assertRaises(TypeError):
            ConversationUiEvent.from_payload(payload)

    def test_dispatcher_input_event_round_trips_model_response_payload(self) -> None:
        """调度器输入事件应按当前 model_response wire type 往返。"""
        event = DispatcherInputEvent(
            event_type=DispatcherInputEventType.MODEL_RESPONSE,
            data={"chat_id": "chat-1", "turn_id": "turn-1", "turn_complete": True},
        )

        payload = event.to_dict()
        parsed = DispatcherInputEvent.from_payload(payload)

        self.assertEqual(payload["type"], "model_response")
        self.assertEqual(parsed, event)


if __name__ == "__main__":
    unittest.main()
