from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt5.QtWidgets import QApplication, QDialog

from chat.chat import Chat, Message
from emotion_enum import EmotionEnum
from qtUI import ChatBackupExportOptionsDialog, ChatBackupMultiExportDialog, ChatGUI
from ui_main.components.chat_display import ChatDisplay


class ChatBackupDialogTestCase(unittest.TestCase):
    """验证对话备份导出相关弹窗的轻量交互行为。"""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        """创建测试用 Qt 应用实例。"""
        existing_application = QApplication.instance()
        cls.app = (
            existing_application
            if isinstance(existing_application, QApplication)
            else QApplication([])
        )

    def test_export_options_include_audio_by_default(self) -> None:
        """单对话导出弹窗默认包含历史语音。"""
        dialog = ChatBackupExportOptionsDialog(
            "导出测试",
            "测试.dsakiko-chat.zip",
            "D_sakiko 对话备份 (*.dsakiko-chat.zip);;Zip 文件 (*.zip)",
        )
        self.assertTrue(dialog.include_audio())

    def test_single_export_dialog_requires_output_path(self) -> None:
        """单对话导出路径为空时不接受弹窗。"""
        dialog = ChatBackupExportOptionsDialog(
            "导出测试",
            "测试.dsakiko-chat.zip",
            "D_sakiko 对话备份 (*.dsakiko-chat.zip);;Zip 文件 (*.zip)",
        )

        dialog._accept_if_valid()

        self.assertNotEqual(dialog.result(), QDialog.Accepted)
        self.assertIn("请选择", dialog.validation_label.text())

    def test_single_export_dialog_returns_absolute_output_path(self) -> None:
        """单对话导出弹窗返回绝对导出路径。"""
        dialog = ChatBackupExportOptionsDialog(
            "导出测试",
            "测试.dsakiko-chat.zip",
            "D_sakiko 对话备份 (*.dsakiko-chat.zip);;Zip 文件 (*.zip)",
        )
        dialog.path_edit.setText("relative-backup.zip")

        self.assertEqual(os.path.abspath("relative-backup.zip"), dialog.output_path())

    def test_multi_export_dialog_defaults_and_quick_selection(self) -> None:
        """多选导出弹窗默认全选，并支持全选和全不选。"""
        chats = [
            Chat(name="对话 A", chat_id="chat-a"),
            Chat(name="对话 B", chat_id="chat-b"),
        ]
        dialog = ChatBackupMultiExportDialog(chats)

        self.assertEqual(["chat-a", "chat-b"], dialog.selected_chat_ids())
        self.assertTrue(dialog.include_audio())

        dialog._set_all_checked(False)
        self.assertEqual([], dialog.selected_chat_ids())

        dialog._set_all_checked(True)
        self.assertEqual(["chat-a", "chat-b"], dialog.selected_chat_ids())


class ChatDisplayForkSignalTestCase(unittest.TestCase):
    """验证聊天显示区提供分叉对话信号。"""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        """创建测试用 Qt 应用实例。"""
        existing_application = QApplication.instance()
        cls.app = (
            existing_application
            if isinstance(existing_application, QApplication)
            else QApplication([])
        )

    def test_fork_chat_signal_emits_message_index(self) -> None:
        """分叉信号会携带消息索引。"""
        display = ChatDisplay()
        emitted_indices: list[int] = []
        display.forkChatRequested.connect(emitted_indices.append)

        display.forkChatRequested.emit(3)

        self.assertEqual([3], emitted_indices)

    def test_fork_chat_action_only_allowed_at_turn_boundaries(self) -> None:
        """分叉位置只允许在用户消息或连续角色回复末尾。"""
        display = ChatDisplay()
        messages = [
            self._message("User", "用户消息"),
            self._message("祥子", "角色回复第一段"),
            self._message("祥子", "角色回复第二段"),
            self._message("User", "下一轮用户消息"),
        ]
        for index, message in enumerate(messages):
            display._remember_message_meta(index, message)

        self.assertTrue(display._can_fork_after_message(0))
        self.assertFalse(display._can_fork_after_message(1))
        self.assertTrue(display._can_fork_after_message(2))
        self.assertTrue(display._can_fork_after_message(3))

    @staticmethod
    def _message(character_name: str, text: str) -> Message:
        """构造测试用聊天消息。"""
        return Message(
            character_name=character_name,
            text=text,
            translation="",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="",
        )


class ChatDeleteAudioTooltipTestCase(unittest.TestCase):
    """验证删除语音勾选框不可用时的提示文案。"""

    def test_disabled_delete_audio_tooltip_distinguishes_shared_audio(self) -> None:
        """有相关语音但被其他对话引用时，应提示共享引用原因。"""
        tooltip = ChatGUI._disabled_delete_audio_tooltip(has_related_audio=True)

        self.assertIn("其他对话引用", tooltip)

    def test_disabled_delete_audio_tooltip_handles_no_audio(self) -> None:
        """没有相关语音时，应提示没有可删除语音。"""
        tooltip = ChatGUI._disabled_delete_audio_tooltip(has_related_audio=False)

        self.assertIn("没有相关语音", tooltip)


if __name__ == "__main__":
    unittest.main()
