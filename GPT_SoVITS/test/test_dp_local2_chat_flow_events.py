from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import dp_local2
from chat_flow.events import ConversationUiEvent
from chat_flow.events import ConversationUiEventType
from chat_flow.events import DispatcherInputEvent
from chat_flow.events import DispatcherInputEventType
from chat_flow.events import LotteryUiCommandEvent


class FakeQueue:
    """记录发送到 UI 队列的载荷。"""

    def __init__(self) -> None:
        """初始化空的载荷列表。"""
        self.items: list[object] = []

    def put(self, item: object) -> None:
        """保存一次队列写入。"""
        self.items.append(item)


class FakeCharacter:
    """提供测试用当前角色。"""

    def __init__(self, character_name: str) -> None:
        """保存角色名。"""
        self.character_name = character_name


class DpLocal2ChatFlowEventsTestCase(unittest.TestCase):
    """验证 dp_local2 使用 chat_flow 事件协议发送 UI 事件。"""

    def test_tool_call_started_event_is_emitted_as_conversation_ui_event(self) -> None:
        """工具调用开始事件应按 typed event 序列化后进入 UI 队列。"""
        queue = FakeQueue()

        dp_local2.DSLocalAndVoiceGen._emit_tool_ui_event(
            queue,
            ConversationUiEventType.TOOL_CALL_STARTED,
            {
                "chat_id": "chat-1",
                "tool_call_id": "call-1",
                "tool_name": "search",
                "message_index": 3,
            },
        )

        self.assertEqual(len(queue.items), 1)
        event = ConversationUiEvent.from_payload(queue.items[0])
        self.assertEqual(event.event_type, ConversationUiEventType.TOOL_CALL_STARTED)
        self.assertEqual(event.chat_id, "chat-1")
        self.assertEqual(event.data["tool_call_id"], "call-1")

    def test_tool_call_updated_event_is_emitted_as_conversation_ui_event(self) -> None:
        """工具调用更新事件应按 typed event 序列化后进入 UI 队列。"""
        queue = FakeQueue()

        dp_local2.DSLocalAndVoiceGen._emit_tool_ui_event(
            queue,
            ConversationUiEventType.TOOL_CALL_UPDATED,
            {
                "chat_id": "chat-1",
                "tool_call_id": "call-1",
                "tool_name": "search",
                "duration_sec": 2.25,
                "status": "completed",
            },
        )

        self.assertEqual(len(queue.items), 1)
        event = ConversationUiEvent.from_payload(queue.items[0])
        self.assertEqual(event.event_type, ConversationUiEventType.TOOL_CALL_UPDATED)
        self.assertEqual(event.data["duration_sec"], 2.25)

    def test_lottery_command_is_emitted_as_typed_ui_message_event(self) -> None:
        """抽签工具应发送 typed UI 消息事件，而不是字符串前缀 JSON。"""
        queue = FakeQueue()

        dp_local2.DSLocalAndVoiceGen._emit_lottery_ui_event(queue, "晚饭", ["寿司", "拉面"])

        self.assertEqual(queue.items, [LotteryUiCommandEvent(title="晚饭", options=("寿司", "拉面"))])

    def test_exit_command_emits_typed_dispatcher_input_event(self) -> None:
        """退出命令应发送 typed dispatcher 输入事件，而不是裸字符串。"""
        subject = object.__new__(dp_local2.DSLocalAndVoiceGen)
        subject.get_current_character = lambda: FakeCharacter("祥子")
        text_queue = FakeQueue()

        handled = dp_local2.DSLocalAndVoiceGen._handle_non_message_command(
            subject,
            {"type": "exit"},
            text_queue,
            FakeQueue(),
            FakeQueue(),
            FakeQueue(),
            FakeQueue(),
        )

        self.assertTrue(handled)
        event = DispatcherInputEvent.from_payload(text_queue.items[0])
        self.assertEqual(event.event_type, DispatcherInputEventType.EXIT)
        self.assertEqual(event.data["character_name"], "祥子")


if __name__ == "__main__":
    unittest.main()
