"""验证提交同意、冻结重试、纯文本 UI 及当前提示词来源。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QUrl
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton

from dp_local2 import DSLocalAndVoiceGen
from feedback.client import FeedbackClient, Receipt, ReceiptStore
from feedback.dialogs import FeedbackDialog, FeedbackHistoryDialog
from feedback.protocol import freeze_payload
from test.test_feedback import MemorySecrets, sample_chat
from ui_main.components.chat_display import ChatDisplay
from ui_main.theme import derive_theme_palette


class FeedbackUiTests(unittest.TestCase):
    """使用离屏 Qt 检查不会发生静默上传或更换正文。"""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        """复用当前 Qt 应用。"""
        existing = QApplication.instance()
        cls.app = existing if isinstance(existing, QApplication) else QApplication([])

    def wait_for_dialog(self, dialog: FeedbackDialog) -> None:
        """处理 Qt 事件直到后台测试操作结束。"""
        for _ in range(300):
            QTest.qWait(5)
            if dialog.job is None:
                return
        self.fail("反馈后台操作未结束")

    def test_cancel_has_no_upload_no_receipt_no_preview(self) -> None:
        """打开或取消反馈不构造正文、不写回执，也没有预览区。"""
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory) / "receipts", MemorySecrets())
            freeze = Mock()
            dialog = FeedbackDialog(title="测试", disclosure="将上传整段对话", freeze=freeze,
                                    palette=derive_theme_palette("#5588aa"), store=store)
            self.assertEqual(len(dialog.findChildren(QPlainTextEdit)), 1)
            dialog.reject()
            freeze.assert_not_called()
            self.assertEqual(store.all(), [])

    def test_retry_uses_same_frozen_bytes_and_receipt(self) -> None:
        """第一次响应丢失后即使源对话变化，重试仍使用原始编号和字节。"""
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory) / "receipts", MemorySecrets())
            chat = sample_chat()
            attempts: list[tuple[str, bytes]] = []

            def freeze(rating: str, comment: str) -> tuple[str, bytes]:
                """在首次确认时冻结源对话。"""
                return freeze_payload(app_version="test", rating=rating, comment=comment, chat=chat, system_prompt="now")

            def submit(client: FeedbackClient, receipt: Receipt, body: bytes) -> None:
                """模拟服务端已保存、第一次响应丢失的场景。"""
                self.assertEqual(len(store.all()), 1)
                self.assertTrue(store.token(receipt.request_id))
                attempts.append((receipt.request_id, body))
                if len(attempts) == 1:
                    raise ValueError("网络超时")
                client.store.confirm(receipt.request_id, "receipt")

            dialog = FeedbackDialog(title=chat.name, disclosure="将上传整段对话", freeze=freeze,
                                    palette=derive_theme_palette("#5588aa"), store=store, rating="up")
            with patch.dict(os.environ, {"DSAKIKO_FEEDBACK_URL": "https://feedback.example"}), patch.object(FeedbackClient, "submit", submit):
                dialog._submit()
                self.wait_for_dialog(dialog)
                self.assertEqual(dialog.submit.text(), "重试原提交")
                self.assertFalse(dialog.comment.isEnabled())
                chat.message_list[0].text = "edited-after-send"
                dialog._submit()
                self.wait_for_dialog(dialog)
            self.assertEqual(attempts[0], attempts[1])
            self.assertIsNone(dialog.body)
            self.assertEqual(store.all()[0].status, "submitted")

    def test_history_only_shows_title_and_withdraw(self) -> None:
        """回执列表中没有正文查看，标题按纯文本显示。"""
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory) / "receipts", MemorySecrets())
            request_id, _ = freeze_payload(app_version="test", rating="up", comment="")
            store.add(request_id, "<img src='https://attacker'>", "https://feedback.example")
            dialog = FeedbackHistoryDialog(store, derive_theme_palette("#5588aa"))
            self.assertEqual([b.text() for b in dialog.findChildren(QPushButton)], ["撤回"])
            self.assertFalse(dialog.findChildren(QPlainTextEdit))
            title = next(label for label in dialog.findChildren(QLabel) if label.text().startswith("<img"))
            self.assertEqual(title.textFormat(), 0)
            dialog.reject()

    def test_reply_links_after_streaming_emit_without_upload(self) -> None:
        """流式回复完成后出现入口，点击只发起弹窗信号。"""
        display = ChatDisplay(derive_theme_palette("#5588aa"))
        events: list[tuple[int, str]] = []
        display.feedbackRequested.connect(lambda index, rating: events.append((index, rating)))
        display.append_message(sample_chat().message_list[0], 0, stream=True)
        self.assertNotIn("feedback:up", display.toHtml())
        display.complete_feedback_turn()
        self.assertNotIn("feedback:up", display.toHtml())
        display.finish_stream_now()
        self.assertIn("feedback:up", display.toHtml())
        display._on_anchor_clicked(QUrl("feedback:down?msg=0"))
        self.assertEqual(events, [(0, "down")])

    def test_history_feedback_only_on_last_reply_of_each_turn(self) -> None:
        """多段历史回复每轮仅有一个入口，刷新和删除后目标随最后一句变化。"""
        chat = sample_chat()
        reply = chat.message_list[0]
        user = replace(reply, character_name="User", text="用户提问")
        chat.message_list = [user, reply, replace(reply, text="第一轮末句"), user,
                             reply, replace(reply, text="第二轮末句"), user]
        display = ChatDisplay(derive_theme_palette("#5588aa"))
        display.render_chat(chat)
        rendered = display.toHtml()
        self.assertEqual(rendered.count("feedback:up?msg="), 2)
        self.assertIn("feedback:up?msg=2", rendered)
        self.assertIn("feedback:up?msg=5", rendered)
        chat.message_list = chat.message_list[:5]
        display.render_chat(chat, pending_turn=True)
        self.assertEqual(display.toHtml().count("feedback:up?msg="), 1)
        display.render_chat(chat)
        self.assertEqual(display.toHtml().count("feedback:up?msg="), 2)
        self.assertIn("feedback:up?msg=4", display.toHtml())
        # 让历史渲染安排的滚动回调在控件销毁前完成。
        QTest.qWait(20)

    def test_live_feedback_waits_for_turn_completion_and_keeps_previous_turn(self) -> None:
        """逐句输出和工具过渡不添加入口，整轮结束只标记末句且不会重复。"""
        display = ChatDisplay(derive_theme_palette("#5588aa"))
        reply = sample_chat().message_list[0]
        display.append_message(reply, 0, stream=True)
        display.finish_stream_now()
        display.append_tool_status_line("tool", "查询世界书")
        display.append_message(replace(reply, text="本轮最后一句"), 1, stream=True)
        display.finish_stream_now()
        self.assertNotIn("feedback:up", display.toHtml())
        display.complete_feedback_turn()
        display.complete_feedback_turn()
        self.assertEqual(display.toHtml().count("feedback:up?msg="), 1)
        self.assertIn("feedback:up?msg=1", display.toHtml())
        display.append_message(replace(reply, character_name="User"), 2)
        display.append_message(reply, 3, stream=True)
        display.complete_feedback_turn()
        display.finish_stream_now()
        self.assertEqual(display.toHtml().count("feedback:up?msg="), 2)
        self.assertIn("feedback:up?msg=3", display.toHtml())
        display.clear_chat()
        display.append_message(reply, 0)
        self.assertNotIn("feedback:up", display.toHtml())

    def test_current_prompt_matches_request_system_without_retrieval(self) -> None:
        """当前普通请求和反馈系统提示词一致，世界书规则仅在启用时加入。"""
        chat = sample_chat()
        subject = DSLocalAndVoiceGen.__new__(DSLocalAndVoiceGen)
        subject.audio_language_choice = "日英混合"
        subject.if_sakiko = False
        subject.sakiko_state = True
        subject.restr = "角色边界"
        subject.if_generate_audio = True
        subject.current_chat_id = chat.chat_id
        subject.chat_manager = Mock(get_chat_by_id=Mock(return_value=chat))
        for enabled in (False, True):
            chat.meta.worldbook.enabled = enabled
            messages = subject._build_llm_messages_for_chat_turn("missing-character")
            if enabled:
                subject._append_worldbook_runtime_instruction(messages)
            rendered = subject.render_feedback_system_prompt(chat, "missing-character")
            self.assertEqual(rendered, messages[0]["content"])
            self.assertNotIn(subject._build_turn_runtime_controls(), rendered)
            self.assertEqual("# Worldbook Knowledge" in rendered, enabled)


if __name__ == "__main__":
    unittest.main()
