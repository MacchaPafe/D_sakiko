"""验证提交同意、冻结重试、纯文本 UI 及当前提示词来源。"""

from __future__ import annotations

import os
import copy
import sys
import tempfile
import unittest
from threading import Event, get_ident
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QTimer, QUrl
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QPlainTextEdit, QPushButton

from dp_local2 import DSLocalAndVoiceGen
from feedback.client import FeedbackClient, Receipt, ReceiptStore
from feedback.dialogs import FeedbackDialog, FeedbackHistoryDialog
from feedback.protocol import freeze_payload
from feedback.admin import FeedbackAdminDialog, AdminClient
from feedback.viewer import FeedbackDetailView
from feedback.importing import import_feedback, source_of
from chat.chat import ChatManager
from chat.system_prompt import compose_system_prompt
from test.test_feedback import MemorySecrets, sample_chat, sample_character
from test import test_feedback as feedback_test_cases
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

    def wait_for_dialog(self, dialog: FeedbackDialog | FeedbackAdminDialog) -> None:
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
                return freeze_payload(app_version="test", rating=rating, comment=comment, chat=chat, character=sample_character(), base_system_prompt="now")

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
            base, runtime = subject.render_feedback_prompt_parts(chat, "missing-character")
            self.assertEqual(base, "original")
            self.assertEqual(compose_system_prompt(base, runtime), rendered)
            self.assertEqual(rendered, messages[0]["content"])
            self.assertNotIn(subject._build_turn_runtime_controls(), rendered)
            self.assertEqual("# Worldbook Knowledge" in rendered, enabled)

    def test_detail_renders_rows_and_target_without_interpreting_uploaded_html(self) -> None:
        detail = feedback_test_cases.FeedbackImportTests().detail()
        detail["payload"]["comment"] = "<img src='https://attacker'>"
        detail["payload"]["conversation"]["messages"][1]["text"] = "<a href='https://attacker'>text</a>"
        view = FeedbackDetailView()
        view.show_detail(detail)
        self.assertEqual(view.comment.toPlainText(), "<img src='https://attacker'>")
        self.assertIn("【反馈目标】 2 · missing-character", view.messages.toPlainText())
        self.assertIn("<a href='https://attacker'>text</a>", view.messages.toPlainText())
        self.assertNotIn('href="https://attacker"', view.messages.toHtml())
        self.assertTrue(view.metadata.isHidden())
        self.assertIn("没有诊断记录", view.diagnostics.toPlainText())
        view.raw_toggle.setChecked(True)
        view._locate()
        self.assertEqual(view.stack.currentIndex(), 0)
        self.assertNotIn("do not import", view.raw.toPlainText())
        self.assertIn("do not import", view.prompts.toPlainText())

    def admin_dialog(self, manager=None, importer=None, opener=None):
        with patch.object(FeedbackAdminDialog, "_refresh"), patch("feedback.admin.ManagedExports"):
            return FeedbackAdminDialog(derive_theme_palette("#5588aa"), chat_manager=manager,
                                       import_chat=importer, open_chat=opener)

    def test_admin_import_refetches_and_reuses_local_chat_without_auto_processing(self) -> None:
        manager = ChatManager()
        detail = feedback_test_cases.FeedbackImportTests().detail()
        def importer(fresh, endpoint):
            with patch.object(manager, "save"):
                return import_feedback(manager, fresh, endpoint, sample_character()).chat_id
        imported = Mock(side_effect=importer)
        opener = Mock(return_value=True)
        dialog = self.admin_dialog(manager, imported, opener)
        dialog._show_detail(detail)
        changed = copy.deepcopy(detail)
        changed["payload"]["conversation"]["messages"][1]["text"] = "fresh"
        client = Mock(request=Mock(return_value=changed))
        with patch("feedback.admin.AdminClient", return_value=client):
            dialog._import()
            self.assertFalse(dialog.import_button.isEnabled())
            self.wait_for_dialog(dialog)
        client.request.assert_called_once_with("GET", "/" + detail["feedback_id"])
        self.assertEqual(manager.chat_list[0].message_list[1].text, "fresh")
        self.assertFalse(dialog.detail["processed"])
        self.assertEqual(dialog.import_button.text(), "打开已导入对话")
        dialog._import()
        imported.assert_called_once()
        opener.assert_called_once_with(manager.chat_list[0].chat_id)

    def test_admin_missing_feedback_never_imported_and_plain_advice_disabled(self) -> None:
        importer = Mock()
        dialog = self.admin_dialog(importer=importer)
        detail = feedback_test_cases.FeedbackImportTests().detail()
        dialog._show_detail(detail)
        with patch("feedback.admin.AdminClient", return_value=Mock(request=Mock(return_value={"missing": True}))):
            dialog._import()
            self.wait_for_dialog(dialog)
        importer.assert_not_called()
        self.assertIsNone(dialog.detail)
        self.assertFalse(dialog.import_button.isEnabled())
        detail["payload"].update(conversation=None, prompt_context=None, target=None, worldbook_enabled=False)
        dialog._show_detail(detail)
        self.assertFalse(dialog.import_button.isEnabled())
        self.assertFalse(dialog.export_backup_action.isEnabled())
        self.assertIn("未附带对话", dialog.viewer.messages.toPlainText())
        dialog.reject()

    def test_source_status_checks_preserve_local_conversation_on_missing_or_failure(self) -> None:
        manager = ChatManager()
        detail = feedback_test_cases.FeedbackImportTests().detail()
        with patch.object(manager, "save"):
            chat = import_feedback(manager, detail, "", sample_character())
        dialog = self.admin_dialog(manager)
        source = source_of(chat)
        for response, expected in [({"missing": True}, "unavailable"), (ValueError("offline"), "unknown")]:
            source["status"] = "available"
            client = Mock()
            if isinstance(response, Exception):
                client.request.side_effect = response
            else:
                client.request.return_value = response
            statuses = dialog._check_sources(client, dialog._source_snapshots())
            with patch.object(manager, "save"):
                dialog._apply_source_statuses(statuses)
            self.assertEqual(source["status"], expected)
            self.assertIs(manager.get_chat_by_id(chat.chat_id), chat)
        dialog.lists.setCurrentIndex(1)
        dialog.local_list.setCurrentRow(0)
        self.assertIn("暂时无法检查", dialog.source_status.text())
        dialog.reject()

    def test_admin_pending_request_keeps_local_browsing_and_cached_detail_available(self) -> None:
        """慢请求期间事件循环继续运行，本地页不会被迟到的云端详情覆盖。"""
        manager = ChatManager()
        detail = feedback_test_cases.FeedbackImportTests().detail()
        with patch.object(manager, "save"):
            chat = import_feedback(manager, detail, "", sample_character())
        dialog = self.admin_dialog(manager, opener=Mock(return_value=True))
        dialog._show_detail(detail)
        dialog.show()
        release = Event()
        worker_threads: list[int] = []
        ticks: list[int] = []
        timer = QTimer(dialog)
        timer.setInterval(5)
        timer.timeout.connect(lambda: ticks.append(1))

        def operation() -> object:
            """模拟被网络延迟阻塞的后台详情请求。"""
            worker_threads.append(get_ident())
            if not release.wait(5):
                raise ValueError("测试请求超时")
            return detail

        try:
            dialog._start(operation, dialog._show_detail, message="正在加载反馈详情…")
            timer.start()
            QTest.qWait(40)
            self.assertTrue(ticks)
            self.assertTrue(worker_threads)
            self.assertNotEqual(worker_threads[0], get_ident())
            self.assertTrue(dialog.loading.isVisible())
            self.assertEqual(dialog.progress.maximum(), 0)
            self.assertFalse(dialog.refresh_button.isEnabled())
            self.assertFalse(dialog.list.isEnabled())
            self.assertFalse(dialog.mark_button.isEnabled())
            self.assertFalse(dialog.export_json_action.isEnabled())
            self.assertTrue(dialog.viewer.isEnabled())
            dialog.viewer.raw_toggle.setChecked(True)
            self.assertEqual(dialog.viewer.stack.currentIndex(), 1)
            dialog.lists.setCurrentIndex(1)
            dialog.local_list.setCurrentRow(0)
            self.assertTrue(dialog.local_list.isEnabled())
            self.assertTrue(dialog.import_button.isEnabled())
            self.assertEqual(dialog.viewer.heading.text(), chat.name)
            with patch("feedback.admin.AdminClient") as client:
                dialog.lists.setCurrentIndex(0)
                self.assertEqual(dialog.detail, detail)
                self.assertIn("针对第", dialog.viewer.summary.text())
                dialog.lists.setCurrentIndex(1)
                client.assert_not_called()
            release.set()
            self.wait_for_dialog(dialog)
            self.assertIn("本地导入对话", dialog.viewer.summary.text())
            self.assertFalse(dialog.loading.isVisible())
            with patch("feedback.admin.AdminClient") as client:
                dialog.lists.setCurrentIndex(0)
                client.assert_not_called()
            self.assertEqual(dialog.detail, detail)
            self.assertTrue(dialog.mark_button.isEnabled())
        finally:
            timer.stop()
            release.set()
            self.wait_for_dialog(dialog)
            dialog.reject()

    def test_admin_opens_local_chat_while_request_finishes_without_late_callback(self) -> None:
        """打开本地副本不等待网络，隐藏窗口保留线程至结束并忽略迟到回调。"""
        manager = ChatManager()
        detail = feedback_test_cases.FeedbackImportTests().detail()
        with patch.object(manager, "save"):
            chat = import_feedback(manager, detail, "", sample_character())
        opener = Mock(return_value=True)
        dialog = self.admin_dialog(manager, opener=opener)
        dialog.lists.setCurrentIndex(1)
        dialog.local_list.setCurrentRow(0)
        dialog.show()
        release = Event()
        completed = Mock()

        def operation() -> object:
            """让网络操作持续到本地对话打开之后。"""
            release.wait(5)
            return detail

        try:
            dialog._start(operation, completed)
            self.assertTrue(dialog.import_button.isEnabled())
            dialog._import()
            opener.assert_called_once_with(chat.chat_id)
            self.assertFalse(dialog.isVisible())
            self.assertIsNotNone(dialog.job)
            self.assertIn(dialog, FeedbackAdminDialog._closing_dialogs)
            release.set()
            self.wait_for_dialog(dialog)
            completed.assert_not_called()
            self.assertEqual(dialog.result(), QDialog.Accepted)
            self.assertNotIn(dialog, FeedbackAdminDialog._closing_dialogs)
        finally:
            release.set()
            self.wait_for_dialog(dialog)

    def test_admin_close_during_request_skips_completion_and_releases_window(self) -> None:
        """关闭键立即隐藏窗口，后台结束后释放保活引用且不执行迟到操作。"""
        dialog = self.admin_dialog()
        dialog.show()
        release = Event()
        completed = Mock()

        def operation() -> object:
            """让后台结果在用户关闭窗口后才返回。"""
            release.wait(5)
            return None

        try:
            dialog._start(operation, completed)
            dialog.close()
            self.assertFalse(dialog.isVisible())
            self.assertIn(dialog, FeedbackAdminDialog._closing_dialogs)
            release.set()
            self.wait_for_dialog(dialog)
            completed.assert_not_called()
            self.assertEqual(dialog.result(), QDialog.Rejected)
            self.assertNotIn(dialog, FeedbackAdminDialog._closing_dialogs)
        finally:
            release.set()
            self.wait_for_dialog(dialog)

    def test_admin_error_restores_network_actions_and_hides_loading(self) -> None:
        """请求失败后显示原因，恢复操作，不留下永久加载状态。"""
        dialog = self.admin_dialog()
        dialog._show_detail(feedback_test_cases.FeedbackImportTests().detail())

        def operation() -> object:
            """模拟可展示的网络失败。"""
            raise ValueError("测试网络不可用")

        completed = Mock()
        dialog._start(operation, completed)
        self.wait_for_dialog(dialog)
        completed.assert_not_called()
        self.assertTrue(dialog.loading.isHidden())
        self.assertEqual(dialog.status.text(), "测试网络不可用")
        self.assertTrue(dialog.refresh_button.isEnabled())
        self.assertTrue(dialog.mark_button.isEnabled())
        dialog.reject()

    def test_admin_chained_refresh_keeps_loading_and_request_exclusion(self) -> None:
        """处理状态提交后的连续刷新仍显示加载，并阻止重复网络操作。"""
        dialog = self.admin_dialog()
        dialog._show_detail(feedback_test_cases.FeedbackImportTests().detail())
        release = Event()
        refreshing = Event()

        def request(method: str, suffix: str = "", body: object = None) -> object:
            """先完成状态更新，再阻塞后续列表刷新。"""
            if method == "PATCH":
                return {"ok": True}
            refreshing.set()
            release.wait(5)
            return {"items": [], "cursor": None}

        client = Mock(request=Mock(side_effect=request))
        try:
            with patch("feedback.admin.AdminClient", return_value=client):
                dialog._mark()
                for _ in range(100):
                    QTest.qWait(5)
                    if refreshing.is_set():
                        break
                self.assertTrue(refreshing.is_set())
                self.assertFalse(dialog.loading.isHidden())
                self.assertIn("刷新", dialog.loading_text.text())
                self.assertFalse(dialog.refresh_button.isEnabled())
                dialog._mark()
                self.assertEqual(client.request.call_count, 2)
                release.set()
                self.wait_for_dialog(dialog)
            self.assertTrue(dialog.loading.isHidden())
            self.assertTrue(dialog.refresh_button.isEnabled())
        finally:
            release.set()
            self.wait_for_dialog(dialog)
            dialog.reject()

    def test_main_window_uses_loaded_character_and_cancel_does_not_create_chat(self) -> None:
        from qtUI import ChatGUI
        detail = feedback_test_cases.FeedbackImportTests().detail()
        manager = ChatManager()
        character = sample_character()
        subject = SimpleNamespace(chat_manager=manager, character_list=[character], current_character=character,
                                  _ensure_chat_history_operation_allowed=Mock(return_value=True), refresh_chat_list=Mock())
        with patch.object(manager, "save"), patch("qtUI.QInputDialog") as choose:
            chat_id = ChatGUI._import_feedback_chat(subject, detail, "https://admin.example")
            choose.assert_not_called()
        self.assertEqual(manager.get_chat_by_id(chat_id).get_character_name(), character.character_name)
        manager.delete_chat(chat_id)
        character.character_name = "local"
        selector = Mock(findChildren=Mock(return_value=[]), exec_=Mock(return_value=QDialog.Rejected))
        with patch("qtUI.QInputDialog", return_value=selector):
            self.assertIsNone(ChatGUI._import_feedback_chat(subject, detail, "https://admin.example"))
        self.assertEqual(manager.chat_list, [])
        selector.exec_.return_value = QDialog.Accepted
        selector.textValue.return_value = "local（missing-character）"
        with patch.object(manager, "save"), patch("qtUI.QInputDialog", return_value=selector):
            chat_id = ChatGUI._import_feedback_chat(subject, detail, "https://admin.example")
        chat = manager.get_chat_by_id(chat_id)
        self.assertEqual(chat.get_character_name(), "local")
        self.assertEqual(chat.message_list[1].character_name, "local")
        subject._ensure_chat_history_operation_allowed.return_value = False
        with patch("qtUI.QInputDialog") as choose:
            self.assertIsNone(ChatGUI._import_feedback_chat(subject, detail, "https://other.example"))
            choose.assert_not_called()
        self.assertEqual(len(manager.chat_list), 1)


if __name__ == "__main__":
    unittest.main()
