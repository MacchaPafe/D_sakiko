from __future__ import annotations

import os
import sys
import unittest
from typing import Callable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import qtUI
from chat.chat import Chat
from chat.chat import Message
from chat_flow.events import ConversationUiEvent
from chat_flow.events import ConversationUiEventType
from chat_flow.events import ApplicationQuitUiMessageEvent
from chat_flow.events import LotteryUiCommandEvent
from chat_flow.events import TransientConversationMessageEvent
from emotion_enum import EmotionEnum


class FakeChatDisplay:
    """记录聊天展示组件收到的工具状态行。"""

    def __init__(self) -> None:
        """初始化空状态行列表。"""
        self.lines: list[tuple[str, str, str]] = []
        self.transient_lines: list[tuple[str, str, bool, int]] = []
        self.messages: list[tuple[Message, int, bool, int]] = []

    def append_tool_status_line(self, tool_call_id: str, text: str, status: str = "running") -> None:
        """记录一次工具状态展示。"""
        self.lines.append((tool_call_id, text, status))

    def append_transient_text(
        self,
        character_name: str,
        text: str,
        *,
        stream: bool = False,
        interval_ms: int = 30,
    ) -> None:
        """记录一次临时对话文本展示。"""
        self.transient_lines.append((character_name, text, stream, interval_ms))

    def append_message(
        self,
        message: Message,
        msg_index: int,
        *,
        stream: bool = False,
        interval_ms: int = 30,
    ) -> None:
        """记录一次普通消息展示。"""
        self.messages.append((message, msg_index, stream, interval_ms))


class FakeConversationWindow:
    """提供 MainWindow 工具事件处理所需的最小对象。"""

    def __init__(self) -> None:
        """初始化当前对话和工具调用展示缓存。"""
        self.current_chat_id = "chat-1"
        self.tool_call_records_cache: dict[str, dict[str, object]] = {}
        self.chat_display = FakeChatDisplay()
        self.active_turn_id: str | None = None
        self.active_turn_phase = ""
        self.current_character = FakeCharacter("祥子")
        self.chat_manager = FakeChatManager(
            Chat(
                message_list=[
                    Message("祥子", "旧文本", "旧翻译", EmotionEnum.HAPPINESS, ""),
                ],
                chat_id="chat-1",
            )
        )
        self.current_chat = self.chat_manager.chat
        self.live2d_text_queue = FakeEventQueue()
        self.refreshed_chat_list = False

    def _is_cancelled_turn_payload(self, payload: dict[str, object]) -> bool:
        """测试对象不模拟已取消轮次。"""
        return False

    def _is_active_turn_payload(self, payload: dict[str, object]) -> bool:
        """测试对象不限制活跃轮次。"""
        return True

    def refresh_chat_list(self) -> None:
        """记录聊天列表刷新动作。"""
        self.refreshed_chat_list = True

    def _handle_structured_response(self, payload: dict[str, object]) -> None:
        """复用 MainWindow 的结构化响应处理。"""
        qtUI.ChatGUI._handle_structured_response(self, payload)

    def _append_tool_status_line(self, tool_call_id: str, text: str, status: str = "running") -> None:
        """复用 MainWindow 的状态行追加逻辑。"""
        qtUI.ChatGUI._append_tool_status_line(self, tool_call_id, text, status)

    def _handle_tool_call_start_event(self, payload: dict[str, object]) -> None:
        """复用 MainWindow 的工具调用开始处理。"""
        qtUI.ChatGUI._handle_tool_call_start_event(self, payload)

    def _handle_tool_call_update_event(self, payload: dict[str, object]) -> None:
        """复用 MainWindow 的工具调用完成处理。"""
        qtUI.ChatGUI._handle_tool_call_update_event(self, payload)


class FakeStatusBox:
    """记录状态提示框收到的文本。"""

    def __init__(self) -> None:
        """初始化空状态文本列表。"""
        self.lines: list[str] = []

    def clear(self) -> None:
        """清空已记录的状态文本。"""
        self.lines.clear()

    def append(self, text: str) -> None:
        """记录一条状态提示文本。"""
        self.lines.append(text)


class FakeCharacter:
    """提供测试用当前角色对象。"""

    def __init__(self, character_name: str) -> None:
        """保存角色名。"""
        self.character_name = character_name


class FakeChatManager:
    """提供按编号查找对话的最小接口。"""

    def __init__(self, chat: Chat) -> None:
        """保存单个测试对话。"""
        self.chat = chat

    def get_chat_by_id(self, chat_id: str) -> Chat | None:
        """按编号返回测试对话。"""
        if chat_id == self.chat.chat_id:
            return self.chat
        return None


class FakeMessageWindow:
    """提供 MainWindow 消息队列事件处理所需的最小对象。"""

    def __init__(self) -> None:
        """初始化消息窗口的可观测状态。"""
        self.saved = False
        self.closed = False
        self.messages_box = FakeStatusBox()
        self.qt2dp_queue = FakeEventQueue()
        self._lottery_dialog_ref: object | None = None
        self._message_command_handlers: dict[str, Callable[[str], None]] = {}

    def save_data(self) -> None:
        """记录保存动作。"""
        self.saved = True

    def close(self) -> None:
        """记录关闭动作。"""
        self.closed = True

    def _dispatch_message_command(self, message: str) -> bool:
        """复用 MainWindow 的字符串命令分发逻辑。"""
        return qtUI.ChatGUI._dispatch_message_command(self, message)

    def _handle_lottery_ui_command(self, event: LotteryUiCommandEvent) -> None:
        """复用 MainWindow 的 typed 抽签命令处理。"""
        qtUI.ChatGUI._handle_lottery_ui_command(self, event)


class FakeEventQueue:
    """记录被发送到队列的事件载荷。"""

    def __init__(self) -> None:
        """初始化空载荷列表。"""
        self.items: list[object] = []

    def put(self, item: object) -> None:
        """记录一次队列写入。"""
        self.items.append(item)


class FakeLotteryDialog:
    """替代真实抽签窗口，捕获构造参数和显示动作。"""

    last_instance: FakeLotteryDialog | None = None

    def __init__(
        self,
        title: str,
        options: list[str],
        on_result: Callable[[str], None],
        parent: object | None = None,
    ) -> None:
        """保存抽签窗口参数，避免测试启动真实 Qt 窗口。"""
        self.title = title
        self.options = options
        self.on_result = on_result
        self.parent = parent
        self.was_shown = False
        FakeLotteryDialog.last_instance = self

    def show(self) -> None:
        """记录窗口已被展示。"""
        self.was_shown = True


class QtUiChatFlowEventsTestCase(unittest.TestCase):
    """验证 Qt 对话窗口消费 chat_flow UI 事件。"""

    def test_typed_tool_call_events_append_existing_status_lines(self) -> None:
        """typed 工具调用事件应追加与旧 UI 行为一致的状态文本。"""
        window = FakeConversationWindow()
        started = ConversationUiEvent(
            event_type=ConversationUiEventType.TOOL_CALL_STARTED,
            chat_id="chat-1",
            data={"tool_call_id": "call-1", "tool_name": "search"},
        )
        updated = ConversationUiEvent(
            event_type=ConversationUiEventType.TOOL_CALL_UPDATED,
            chat_id="chat-1",
            data={"tool_call_id": "call-1", "tool_name": "search", "duration_sec": 1.5},
        )

        window._handle_structured_response(started.to_dict())
        window._handle_structured_response(updated.to_dict())

        self.assertEqual(
            window.chat_display.lines,
            [
                ("call-1", "<正在调用工具...>", "running"),
                ("call-1", "<工具调用完成 1.50秒>", "completed"),
            ],
        )

    def test_legacy_string_prefixed_tool_call_payload_is_rejected(self) -> None:
        """旧字符串前缀工具调用载荷不应再触发工具状态行追加。"""
        window = FakeConversationWindow()

        qtUI.ChatGUI.handle_response(
            window,
            '__TOOL_CALL_START__:{"tool_call_id":"legacy","tool_name":"search"}',
        )

        self.assertEqual(window.chat_display.lines, [])
        self.assertEqual(window.tool_call_records_cache, {})

    def test_typed_application_quit_message_event_closes_window(self) -> None:
        """typed 退出消息事件应保存并关闭窗口，裸 bye 只作为普通状态文本显示。"""
        window = FakeMessageWindow()

        qtUI.ChatGUI.handle_messages(window, "bye")
        self.assertFalse(window.saved)
        self.assertFalse(window.closed)
        self.assertEqual(window.messages_box.lines, ["bye"])

        qtUI.ChatGUI.handle_messages(window, ApplicationQuitUiMessageEvent())

        self.assertTrue(window.saved)
        self.assertTrue(window.closed)

    def test_typed_lottery_message_event_opens_dialog_and_reports_result(self) -> None:
        """typed 抽签消息事件应打开抽签窗口，并把结果送回输入流。"""
        window = FakeMessageWindow()
        original_dialog = qtUI.LotteryDialog
        try:
            qtUI.LotteryDialog = FakeLotteryDialog
            qtUI.ChatGUI.handle_messages(
                window,
                LotteryUiCommandEvent(title="晚饭", options=("寿司", "拉面")),
            )
        finally:
            qtUI.LotteryDialog = original_dialog

        dialog = FakeLotteryDialog.last_instance
        self.assertIsNotNone(dialog)
        assert dialog is not None
        self.assertEqual(dialog.title, "晚饭")
        self.assertEqual(dialog.options, ["寿司", "拉面"])
        self.assertTrue(dialog.was_shown)

        dialog.on_result("拉面")

        self.assertEqual(len(window.qt2dp_queue.items), 1)
        self.assertIn("抽签主题：晚饭", str(window.qt2dp_queue.items[0]))
        self.assertIn("结果是：拉面", str(window.qt2dp_queue.items[0]))
        self.assertEqual(window.messages_box.lines, ["抽签结果：拉面"])

    def test_legacy_lottery_string_prefix_is_not_dispatched(self) -> None:
        """旧抽签字符串前缀不应再触发抽签命令分发。"""
        window = FakeMessageWindow()
        FakeLotteryDialog.last_instance = None

        qtUI.ChatGUI.handle_messages(window, '__LOTTERY_UI_CMD__:{"title":"晚饭","options":["寿司","拉面"]}')

        self.assertEqual(window.qt2dp_queue.items, [])
        self.assertIsNone(FakeLotteryDialog.last_instance)
        self.assertEqual(
            window.messages_box.lines,
            ['__LOTTERY_UI_CMD__:{"title":"晚饭","options":["寿司","拉面"]}'],
        )

    def test_transient_conversation_event_appends_current_chat_display_only(self) -> None:
        """临时对话事件应只追加到当前聊天框展示路径。"""
        window = FakeConversationWindow()
        event = TransientConversationMessageEvent(
            chat_id="chat-1",
            character_name="祥子",
            text="（再见）",
            stream=False,
        )

        window._handle_structured_response(event.to_dict())

        self.assertEqual(window.chat_display.transient_lines, [("祥子", "（再见）", False, 30)])
        self.assertEqual(window.chat_display.lines, [])

    def test_assistant_segment_ready_uses_event_snapshot_without_state_writeback(self) -> None:
        """assistant 片段事件应使用事件快照展示，不再由 Qt 回填生成状态。"""
        window = FakeConversationWindow()
        event = ConversationUiEvent(
            event_type=ConversationUiEventType.ASSISTANT_SEGMENT_READY,
            chat_id="chat-1",
            turn_id="turn-1",
            data={
                "message_index": 0,
                "character_name": "祥子",
                "text": "新文本",
                "translation": "新翻译",
                "emotion": "LABEL_0",
                "audio_path": "/tmp/generated.wav",
            },
        )

        window._handle_structured_response(event.to_dict())

        persisted_message = window.current_chat.message_list[0]
        self.assertEqual(persisted_message.audio_path, "")
        self.assertEqual(persisted_message.translation, "旧翻译")
        displayed_message, msg_index, stream, _interval_ms = window.chat_display.messages[0]
        self.assertEqual(msg_index, 0)
        self.assertTrue(stream)
        self.assertEqual(displayed_message.text, "新文本")
        self.assertEqual(displayed_message.translation, "新翻译")
        self.assertEqual(displayed_message.audio_path, "/tmp/generated.wav")


if __name__ == "__main__":
    unittest.main()
