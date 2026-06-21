from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt5.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt5.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QInputMethodEvent,
    QKeyEvent,
    QTextCursor,
    QImage,
)
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget

from chat.chat import Message
from emotion_enum import EmotionEnum
from input_commands import InputCommandMatcher, InputCommandPalette, build_default_input_command_specs
from qtUI import MessageEditDialog
from ui_main.components.message_input import MessageInput, trim_surrounding_blank_lines


class MessageInputTestCase(unittest.TestCase):
    """验证多行消息输入框的编辑、发送与布局行为。"""

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

    def setUp(self) -> None:
        """创建并展示固定宽度的消息输入框。"""
        self.temp_paths: list[str] = []
        self.input = MessageInput()
        self.input.resize(240, self.input.height())
        self.input.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        """销毁当前测试创建的输入框。"""
        self.input.close()
        self.input.deleteLater()
        for path in self.temp_paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        self.app.processEvents()

    def test_trim_surrounding_blank_lines_preserves_body(self) -> None:
        """首尾空白行应被移除，正文缩进和内部空行应原样保留。"""
        text = "\n \t\n  first  \n\n\tsecond\t\n \n"

        self.assertEqual(
            trim_surrounding_blank_lines(text),
            "  first  \n\n\tsecond\t",
        )
        self.assertEqual(trim_surrounding_blank_lines(" \n\t"), "")

    def test_plain_enter_sends_without_inserting_newline(self) -> None:
        """普通 Enter 应发送且不修改输入内容。"""
        send_count = 0

        def record_send() -> None:
            """记录发送信号触发次数。"""
            nonlocal send_count
            send_count += 1

        self.input.setPlainText("hello")
        self.input.sendRequested.connect(record_send)

        QTest.keyClick(self.input.text_edit, Qt.Key_Return)

        self.assertEqual(send_count, 1)
        self.assertEqual(self.input.toPlainText(), "hello")

    def test_modified_enter_inserts_newline(self) -> None:
        """功能修饰键配合 Enter 时均应换行而不是发送。"""
        send_count = 0

        def record_send() -> None:
            """记录发送信号触发次数。"""
            nonlocal send_count
            send_count += 1

        self.input.sendRequested.connect(record_send)
        modifier_cases = (
            Qt.ShiftModifier,
            Qt.ControlModifier,
            Qt.AltModifier,
            Qt.MetaModifier,
        )

        for modifier in modifier_cases:
            self.input.setPlainText("hello")
            self.input.moveCursor(QTextCursor.End)
            QTest.keyClick(self.input.text_edit, Qt.Key_Return, modifier)
            self.assertEqual(self.input.toPlainText(), "hello\n")

        self.assertEqual(send_count, 0)

    def test_keypad_enter_sends(self) -> None:
        """数字小键盘 Enter 应被视为发送键。"""
        send_count = 0

        def record_send() -> None:
            """记录发送信号触发次数。"""
            nonlocal send_count
            send_count += 1

        self.input.sendRequested.connect(record_send)

        QTest.keyClick(self.input.text_edit, Qt.Key_Enter, Qt.KeypadModifier)

        self.assertEqual(send_count, 1)

    def test_auto_repeat_enter_is_ignored(self) -> None:
        """长按产生的自动重复 Enter 不应发送或插入换行。"""
        send_count = 0

        def record_send() -> None:
            """记录发送信号触发次数。"""
            nonlocal send_count
            send_count += 1

        self.input.setPlainText("hello")
        self.input.sendRequested.connect(record_send)
        event = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_Return,
            Qt.NoModifier,
            "",
            True,
            2,
        )

        QApplication.sendEvent(self.input.text_edit, event)

        self.assertEqual(send_count, 0)
        self.assertEqual(self.input.toPlainText(), "hello")

    def test_input_method_preedit_blocks_send(self) -> None:
        """输入法存在预编辑文本时，Enter 不应触发发送。"""
        send_count = 0

        def record_send() -> None:
            """记录发送信号触发次数。"""
            nonlocal send_count
            send_count += 1

        self.input.sendRequested.connect(record_send)
        QApplication.sendEvent(self.input.text_edit, QInputMethodEvent("拼", []))

        QTest.keyClick(self.input.text_edit, Qt.Key_Return)

        self.assertEqual(send_count, 0)

        QApplication.sendEvent(self.input.text_edit, QInputMethodEvent("", []))
        QTest.keyClick(self.input.text_edit, Qt.Key_Return)
        self.assertEqual(send_count, 1)

    def test_height_grows_and_stops_at_six_lines(self) -> None:
        """输入框应按视觉行增高，并在六行后改用内部滚动。"""
        minimum_height = self.input.height()

        self.input.setPlainText("one\ntwo\nthree")
        self.app.processEvents()
        three_line_height = self.input.height()

        self.input.setPlainText("\n".join(str(index) for index in range(7)))
        self.app.processEvents()
        seven_line_height = self.input.height()

        self.input.setPlainText("\n".join(str(index) for index in range(20)))
        self.app.processEvents()

        self.assertGreater(three_line_height, minimum_height)
        self.assertGreater(seven_line_height, three_line_height)
        self.assertEqual(self.input.height(), seven_line_height)
        self.assertGreater(self.input.verticalScrollBar().maximum(), 0)

    def test_wrapped_text_contributes_to_height(self) -> None:
        """没有手动换行的长文本也应按视觉折行增加高度。"""
        minimum_height = self.input.height()

        self.input.setPlainText("wrapped text " * 20)
        self.app.processEvents()

        self.assertGreater(self.input.height(), minimum_height)

    def test_clear_after_send_resets_content_height_and_undo(self) -> None:
        """成功发送后的清理应恢复最小高度并清除撤销历史。"""
        minimum_height = self.input.height()
        self.input.insertPlainText("one\ntwo\nthree")
        self.app.processEvents()
        self.assertTrue(self.input.document().isUndoAvailable())

        self.input.clear_after_send()

        self.assertEqual(self.input.toPlainText(), "")
        self.assertEqual(self.input.height(), minimum_height)
        self.assertFalse(self.input.document().isUndoAvailable())

    def test_replace_draft_replaces_content_and_moves_cursor_to_end(self) -> None:
        """替换草稿时应清空旧内容、撤销历史，并把光标放到末尾。"""
        self.input.setPlainText("旧草稿")
        self.input.moveCursor(QTextCursor.End)
        self.input.insertPlainText("x")
        self.assertTrue(self.input.document().isUndoAvailable())

        self.input.replace_draft("新的消息")
        self.app.processEvents()

        self.assertEqual(self.input.toPlainText(), "新的消息")
        self.assertEqual(self.input.textCursor().position(), len("新的消息"))
        self.assertFalse(self.input.document().isUndoAvailable())
        self.assertTrue(self.input.hasFocus())

    def test_message_edit_dialog_rejects_empty_text_without_closing(self) -> None:
        """编辑消息时正文为空应展示提示，并保持对话框打开。"""
        message = Message(
            character_name="User",
            text="原消息",
            translation="",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="",
        )
        dialog = MessageEditDialog(message)
        dialog.show()
        self.app.processEvents()

        dialog.text_edit.setPlainText("  \n")
        dialog._accept_if_valid()

        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.validation_label.text(), "正文不能为空。")
        dialog.close()
        dialog.deleteLater()

    def test_message_edit_dialog_returns_role_text_and_translation(self) -> None:
        """角色消息编辑框应返回正文和可为空的翻译。"""
        message = Message(
            character_name="祥子",
            text="原回复",
            translation="原翻译",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="NO_AUDIO",
        )
        dialog = MessageEditDialog(message)

        dialog.text_edit.setPlainText(" 新回复 ")
        self.assertIsNotNone(dialog.translation_edit)
        assert dialog.translation_edit is not None
        dialog.translation_edit.setPlainText(" 新翻译 ")

        self.assertEqual(dialog.edited_text(), "新回复")
        self.assertEqual(dialog.edited_translation(), "新翻译")
        dialog.deleteLater()

    def test_file_paths_replace_selection_at_cursor(self) -> None:
        """文件路径应在当前选区插入，并与相邻文字保留间隔。"""
        self.input.setPlainText("before TARGET after")
        cursor = self.input.textCursor()
        cursor.setPosition(len("before "))
        cursor.setPosition(len("before TARGET"), QTextCursor.KeepAnchor)
        self.input.setTextCursor(cursor)

        self.input.insert_file_paths(("/tmp/first file.txt", "/tmp/second.txt"))

        self.assertEqual(
            self.input.toPlainText(),
            'before "/tmp/first file.txt" "/tmp/second.txt" after',
        )

    def test_file_drop_does_not_leave_stale_visual_cursor(self) -> None:
        """完成文件拖放后不应在投放位置残留静止的视觉光标。"""
        self.input.resize(520, self.input.height())
        self.input.setStyleSheet("QPlainTextEdit { background: white; color: black; }")
        self.input.setPlainText("alpha beta gamma delta")
        self.input.moveCursor(QTextCursor.End)
        self.app.processEvents()

        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile("/tmp/example.txt")])
        drop_position = QPoint(90, 20)
        drag_enter_event = QDragEnterEvent(
            drop_position,
            Qt.CopyAction,
            mime_data,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        drag_move_event = QDragMoveEvent(
            drop_position,
            Qt.CopyAction,
            mime_data,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        drop_event = QDropEvent(
            QPointF(drop_position),
            Qt.CopyAction,
            mime_data,
            Qt.LeftButton,
            Qt.NoModifier,
        )

        QApplication.sendEvent(self.input.text_edit.viewport(), drag_enter_event)
        QApplication.sendEvent(self.input.text_edit.viewport(), drag_move_event)
        QApplication.sendEvent(self.input.text_edit.viewport(), drop_event)
        QTest.mouseClick(
            self.input.text_edit.viewport(),
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(8, 16),
        )
        self.app.processEvents()

        dropped_image = self.input.grab().toImage()
        reference_input = MessageInput()
        try:
            reference_input.resize(520, reference_input.height())
            reference_input.setStyleSheet(
                "QPlainTextEdit { background: white; color: black; }"
            )
            reference_input.setPlainText(self.input.toPlainText())
            reference_input.show()
            reference_input.setFocus()
            self.app.processEvents()
            QTest.mouseClick(
                reference_input.text_edit.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                QPoint(8, 16),
            )
            self.app.processEvents()
            reference_image = reference_input.grab().toImage()

            differing_pixels = [
                (x, y)
                for x in range(drop_position.x() - 4, drop_position.x() + 5)
                for y in range(2, 28)
                if dropped_image.pixelColor(x, y) != reference_image.pixelColor(x, y)
            ]

            self.assertEqual(differing_pixels, [])
            self.assertLess(self.input.cursorRect().x(), 30)
        finally:
            reference_input.close()
            reference_input.deleteLater()

    def test_image_drop_adds_preview_without_inserting_path(self) -> None:
        """视觉模型可用时，拖入图片应添加草稿附件而不是插入路径。"""
        image_path = self._create_temp_png()
        self.input.set_vision_support_checker(lambda: True)

        self._drop_paths([image_path])

        self.assertEqual(self.input.toPlainText(), "")
        self.assertEqual(self.input.pending_image_source_paths(), [image_path])
        self.assertTrue(self.input.preview_area.isVisible())

    def test_image_drop_without_vision_inserts_path_and_shows_error(self) -> None:
        """视觉模型不可用时，可手动把图片退化为普通路径插入。"""
        image_path = self._create_temp_png()
        self.input.set_vision_support_checker(lambda: False)
        self.input.set_image_upload_override_handlers(
            lambda: "dashscope/qwen3.6-plus",
            lambda: True,
            lambda model: True,
        )

        self._drop_paths([image_path])

        self.assertEqual(self.input.pending_image_source_paths(), [])
        self.assertEqual(self.input.toPlainText(), "")
        self.assertTrue(self.input.error_label.isVisible())
        self.assertTrue(self.input.force_send_button.isVisible())
        self.assertTrue(self.input.insert_filename_button.isVisible())

        self.input.insert_filename_button.click()

        self.assertIn(f'"{image_path}"', self.input.toPlainText())
        self.assertFalse(self.input.error_bar.isVisible())

    def test_image_drop_without_force_option_only_shows_insert_filename(self) -> None:
        """不能强制开启图片上传时，不应显示仍然发送按钮。"""
        image_path = self._create_temp_png()
        self.input.set_vision_support_checker(lambda: False)
        self.input.set_image_upload_override_handlers(
            lambda: "deepseek/deepseek-v4-flash",
            lambda: False,
            lambda model: True,
        )

        self._drop_paths([image_path])

        self.assertTrue(self.input.error_label.isVisible())
        self.assertFalse(self.input.force_send_button.isVisible())
        self.assertTrue(self.input.insert_filename_button.isVisible())

    def test_force_image_upload_confirmation_adds_preview(self) -> None:
        """确认强制图片上传后，应保存模型并把暂存图片加入预览。"""
        image_path = self._create_temp_png()
        allowed_models: list[str] = []
        self.input.set_vision_support_checker(lambda: False)
        self.input.set_image_upload_override_handlers(
            lambda: "dashscope/qwen3.6-plus",
            lambda: True,
            lambda model: not allowed_models.append(model),
        )

        self._drop_paths([image_path])

        with mock.patch.object(
            self.input,
            "_confirm_force_image_upload",
            return_value=True,
        ):
            self.input.force_send_button.click()

        self.assertEqual(allowed_models, ["dashscope/qwen3.6-plus"])
        self.assertEqual(self.input.pending_image_source_paths(), [image_path])
        self.assertEqual(self.input.toPlainText(), "")
        self.assertTrue(self.input.preview_area.isVisible())

    def test_backspace_dismisses_pending_unsupported_image_when_empty(self) -> None:
        """输入框为空时，Backspace 应取消待选择的不支持模型图片。"""
        image_path = self._create_temp_png()
        self.input.set_vision_support_checker(lambda: False)
        self.input.set_image_upload_override_handlers(
            lambda: "dashscope/qwen3.6-plus",
            lambda: True,
            lambda model: True,
        )
        self._drop_paths([image_path])

        QTest.keyClick(self.input.text_edit, Qt.Key_Backspace)

        self.assertEqual(self.input.toPlainText(), "")
        self.assertEqual(self.input.pending_image_source_paths(), [])
        self.assertFalse(self.input.error_bar.isVisible())
        self.assertEqual(self.input._pending_unsupported_image_paths, [])

    def test_backspace_keeps_pending_unsupported_image_when_text_not_empty(self) -> None:
        """输入框不是空字符串时，Backspace 应保持普通删除行为。"""
        image_path = self._create_temp_png()
        self.input.set_vision_support_checker(lambda: False)
        self.input.set_image_upload_override_handlers(
            lambda: "dashscope/qwen3.6-plus",
            lambda: True,
            lambda model: True,
        )
        self.input.setPlainText("\n")
        self.input.moveCursor(QTextCursor.End)
        self._drop_paths([image_path])
        self.assertTrue(self.input.error_bar.isVisible())

        QTest.keyClick(self.input.text_edit, Qt.Key_Backspace)

        self.assertEqual(self.input.toPlainText(), "")
        self.assertEqual(self.input.pending_image_source_paths(), [])

    def _create_temp_png(self) -> str:
        """创建测试用 PNG 图片并返回路径。"""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        image = QImage(16, 16, QImage.Format_RGB32)
        image.fill(Qt.red)
        image.save(path)
        self.temp_paths.append(path)
        return path

    def _drop_paths(self, paths: list[str]) -> None:
        """向输入框模拟拖入一组本地路径。"""
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(path) for path in paths])
        drop_position = QPoint(20, 20)
        drag_enter_event = QDragEnterEvent(
            drop_position,
            Qt.CopyAction,
            mime_data,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        drop_event = QDropEvent(
            QPointF(drop_position),
            Qt.CopyAction,
            mime_data,
            Qt.LeftButton,
            Qt.NoModifier,
        )

        QApplication.sendEvent(self.input.text_edit.viewport(), drag_enter_event)
        QApplication.sendEvent(self.input.text_edit.viewport(), drop_event)
        self.app.processEvents()


class InputCommandPaletteMultilineTestCase(unittest.TestCase):
    """验证命令候选栏与多行输入框的协同行为。"""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        """复用或创建测试所需的 Qt 应用实例。"""
        existing_application = QApplication.instance()
        cls.app = (
            existing_application
            if isinstance(existing_application, QApplication)
            else QApplication([])
        )

    def setUp(self) -> None:
        """创建输入框、锚点和命令候选栏。"""
        self.window = QWidget()
        self.window.resize(360, 240)
        self.input = MessageInput(self.window)
        self.input.setGeometry(10, 180, 340, 52)
        matcher = InputCommandMatcher(build_default_input_command_specs())
        self.palette = InputCommandPalette(matcher, self.window)
        self.palette.attach_to_input(self.input.text_edit, self.input)
        self.window.show()
        self.input.setFocus()
        self.app.processEvents()

    def tearDown(self) -> None:
        """销毁命令候选栏测试窗口。"""
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_modified_enter_closes_palette_and_inserts_newline(self) -> None:
        """命令栏显示时，修饰键 Enter 应关闭命令栏并输入换行。"""
        self.input.setPlainText("/save")
        self.input.moveCursor(QTextCursor.End)
        self.app.processEvents()
        self.assertTrue(self.palette.isVisible())

        QTest.keyClick(self.input.text_edit, Qt.Key_Return, Qt.ControlModifier)
        self.app.processEvents()

        self.assertFalse(self.palette.isVisible())
        self.assertEqual(self.input.toPlainText(), "/save\n")

    def test_multiline_text_never_shows_palette(self) -> None:
        """原始输入包含换行时不应显示命令候选栏。"""
        self.input.setPlainText("/save\n")
        self.app.processEvents()

        self.assertFalse(self.palette.isVisible())

    def test_plain_enter_confirms_selected_command(self) -> None:
        """命令栏显示时，普通 Enter 应确认命令而不是发送输入框。"""
        selected_commands: list[str] = []
        send_count = 0

        def record_selection(spec: object) -> None:
            """记录命令候选栏选中的命令。"""
            command = getattr(spec, "command", None)
            if isinstance(command, str):
                selected_commands.append(command)

        def record_send() -> None:
            """记录输入框发送信号触发次数。"""
            nonlocal send_count
            send_count += 1

        self.palette.commandSelected.connect(record_selection)
        self.input.sendRequested.connect(record_send)
        self.input.setPlainText("/save")
        self.app.processEvents()

        QTest.keyClick(self.input.text_edit, Qt.Key_Return)

        self.assertEqual(selected_commands, ["save"])
        self.assertEqual(send_count, 0)


if __name__ == "__main__":
    unittest.main()
