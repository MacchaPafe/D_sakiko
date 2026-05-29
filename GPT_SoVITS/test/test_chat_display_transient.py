from __future__ import annotations

import os
import sys
from types import TracebackType
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt5.QtWidgets import QApplication

from chat.chat import Chat, Message, PendingUserTurn
from chat.chat_meta import ToolCallRecordMeta
from chat_flow.audio_scheduler import PENDING_AUDIO
from emotion_enum import EmotionEnum
from ui_main.components.chat_display import ChatDisplay


class CountingLock:
    """记录显示渲染是否通过运行时锁读取聊天状态。"""

    def __init__(self) -> None:
        """初始化计数器。"""
        self.enter_count = 0
        self.exit_count = 0

    def __enter__(self) -> bool:
        """记录进入锁上下文。"""
        self.enter_count += 1
        return True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """记录退出锁上下文。"""
        self.exit_count += 1


class ChatDisplayTransientTestCase(unittest.TestCase):
    """验证聊天显示组件的非持久化文本渲染路径。"""

    @classmethod
    def setUpClass(cls) -> None:
        """确保测试进程中存在 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def test_transient_text_has_prefix_and_no_message_actions(self) -> None:
        """非持久化文本应显示角色前缀，但不创建消息锚点或消息元数据。"""
        display = ChatDisplay()

        display.append_transient_text("祥子", "（再见）")

        html = display.toHtml()
        self.assertIn("祥子", html)
        self.assertIn("（再见）", display.toPlainText())
        self.assertNotIn("?msg=", html)
        self.assertNotIn("href=", html)
        self.assertEqual(display._message_meta_by_index, {})

    def test_transient_text_can_stream_without_message_actions(self) -> None:
        """非持久化文本启用流式显示时也不应创建消息锚点或消息元数据。"""
        display = ChatDisplay()

        display.append_transient_text("祥子", "（再见）", stream=True, interval_ms=1)
        self.assertTrue(display.is_streaming())
        display.finish_stream_now()

        html = display.toHtml()
        self.assertIn("祥子", html)
        self.assertIn("（再见）", display.toPlainText())
        self.assertNotIn("?msg=", html)
        self.assertNotIn("href=", html)
        self.assertEqual(display._message_meta_by_index, {})

    def test_render_chat_reads_history_under_runtime_lock(self) -> None:
        """完整渲染聊天记录时应持有 Chat runtime 的同一把锁。"""
        display = ChatDisplay()
        chat = Chat(message_list=[
            Message("User", "你好", "", EmotionEnum.HAPPINESS, "")
        ])
        lock = CountingLock()
        chat.runtime.lock = lock

        display.render_chat(chat)

        self.assertEqual(lock.enter_count, 1)
        self.assertEqual(lock.exit_count, 1)
        self.assertIn("你好", display.toPlainText())

    def test_render_chat_shows_pending_user_turn_without_message_anchor(self) -> None:
        """pending 用户文本应显示在当前聊天框，但不绑定持久消息索引。"""
        display = ChatDisplay()
        chat = Chat()
        chat.runtime.pending_user_turns.append(PendingUserTurn(turn_id="turn-1", text="排队消息"))

        display.render_chat(chat)

        html = display.toHtml()
        self.assertIn("排队消息", display.toPlainText())
        self.assertNotIn("?msg=", html)

    def test_pending_audio_message_renders_as_non_playable(self) -> None:
        """PENDING_AUDIO 应作为非播放哨兵渲染，而不是音频链接。"""
        display = ChatDisplay()
        chat = Chat(message_list=[
            Message("祥子", "音频生成中", "", EmotionEnum.HAPPINESS, PENDING_AUDIO)
        ])

        display.render_chat(chat)

        html = display.toHtml()
        self.assertIn("音频生成中", display.toPlainText())
        self.assertIn("no_audio:?msg=0", html)
        self.assertNotIn("PENDING_AUDIO", html)

    def test_render_chat_shows_persisted_tool_records_bound_to_message_history(self) -> None:
        """完整历史重渲染应显示绑定到消息索引的持久化工具记录。"""
        display = ChatDisplay()
        chat = Chat(message_list=[
            Message("祥子", "我查一下。", "", EmotionEnum.HAPPINESS, "")
        ])
        chat.append_tool_call_record(ToolCallRecordMeta(
            tool_call_id="call-1",
            tool_name="lookup",
            message_index=0,
            status="completed",
            result_content="found it",
            duration_sec=0.25,
            ok=True,
        ))

        display.render_chat(chat)

        self.assertIn("<工具调用完成 0.25秒>", display.toPlainText())
        self.assertIn("我查一下。", display.toPlainText())
        self.assertIn("toolcall:call-1", display.toHtml())


if __name__ == "__main__":
    unittest.main()
