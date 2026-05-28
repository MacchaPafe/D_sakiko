from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt5.QtWidgets import QApplication

from ui_main.components.chat_display import ChatDisplay


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


if __name__ == "__main__":
    unittest.main()
