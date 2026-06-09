from __future__ import annotations

import math
import os.path
from collections.abc import Sequence

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QInputMethodEvent,
    QKeyEvent,
    QResizeEvent,
    QTextCursor,
)
from PyQt5.QtWidgets import QPlainTextEdit, QSizePolicy, QWidget


def trim_surrounding_blank_lines(text: str) -> str:
    """移除文本首尾的纯空白行，同时保留正文内部格式。"""
    lines = text.split("\n")
    first_content_line = 0
    last_content_line = len(lines)

    while first_content_line < last_content_line and not lines[first_content_line].strip():
        first_content_line += 1
    while last_content_line > first_content_line and not lines[last_content_line - 1].strip():
        last_content_line -= 1

    return "\n".join(lines[first_content_line:last_content_line])


class MessageInput(QPlainTextEdit):
    """支持聊天发送快捷键、自动高度和文件拖入的多行消息输入框。"""

    sendRequested = pyqtSignal()

    MIN_VISIBLE_LINES = 2
    MAX_VISIBLE_LINES = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化消息输入框的换行、滚动及输入法状态。"""
        super().__init__(parent)
        self._input_method_composing = False

        self.setAcceptDrops(True)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setTabChangesFocus(False)
        self.setProperty("inputMethodComposing", False)

        self.textChanged.connect(self.refresh_height)  # noqa
        self.refresh_height()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """将无功能修饰键的 Enter 转为发送，其余 Enter 保持换行。"""
        if event.key() not in (Qt.Key_Return, Qt.Key_Enter):
            super().keyPressEvent(event)
            return

        if event.isAutoRepeat():
            event.accept()
            return

        if self._input_method_composing:
            super().keyPressEvent(event)
            return

        functional_modifiers = event.modifiers() & (
            Qt.ShiftModifier
            | Qt.ControlModifier
            | Qt.AltModifier
            | Qt.MetaModifier
            | Qt.GroupSwitchModifier
        )
        if functional_modifiers:
            self.insert_text_at_cursor("\n")
            event.accept()
            return

        self.sendRequested.emit()
        event.accept()

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        """记录输入法预编辑状态，并交由 Qt 完成组合文本处理。"""
        self._input_method_composing = bool(event.preeditString())
        self.setProperty("inputMethodComposing", self._input_method_composing)
        super().inputMethodEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """在输入框宽度变化后按新的视觉折行结果刷新高度。"""
        super().resizeEvent(event)
        self.refresh_height()

    def refresh_height(self) -> None:
        """根据视觉折行高度，将输入框限制在二至六行之间。"""
        visual_line_count = self._visual_line_count()
        line_height = self.fontMetrics().lineSpacing()
        document_margin = math.ceil(self.document().documentMargin() * 2)
        widget_margins = self.contentsMargins()
        widget_chrome = (
            self.frameWidth() * 2
            + widget_margins.top()
            + widget_margins.bottom()
        )
        minimum_height = (
            line_height * self.MIN_VISIBLE_LINES
            + document_margin
            + widget_chrome
        )
        maximum_height = (
            line_height * self.MAX_VISIBLE_LINES
            + document_margin
            + widget_chrome
        )
        target_height = max(
            minimum_height,
            min(
                visual_line_count * line_height + document_margin + widget_chrome,
                maximum_height,
            ),
        )

        if self.height() != target_height:
            self.setFixedHeight(target_height)

    def _visual_line_count(self) -> int:
        """统计包含自动折行在内的当前视觉行数。"""
        document = self.document()
        document.documentLayout().documentSize()
        line_count = 0
        block = document.begin()
        while block.isValid():
            line_count += max(1, block.layout().lineCount())
            block = block.next()
        return max(1, line_count)

    def prepared_message(self) -> str:
        """返回待发送文本，移除首尾纯空白行并保留正文格式。"""
        return trim_surrounding_blank_lines(self.toPlainText())

    def clear_after_send(self) -> None:
        """发送成功后清空内容、撤销历史并恢复最小高度。"""
        self.clear()
        # 删除撤销历史，避免通过撤销把发送的内容再次显示回来
        self.document().clearUndoRedoStacks()
        self.refresh_height()

    def replace_draft(self, text: str) -> None:
        """替换当前草稿内容，并将光标移动到文本末尾。"""
        self.setPlainText(text)
        self.document().clearUndoRedoStacks()
        self.moveCursor(QTextCursor.End)
        self.refresh_height()
        self.setFocus()

    def insert_text_at_cursor(self, text: str) -> None:
        """使用一次可撤销编辑替换当前选区或在光标处插入文本。"""
        if not text:
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.insertText(text)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def insert_file_paths(self, paths: Sequence[str]) -> None:
        """在当前光标处插入带引号的文件路径，并避免与相邻文字粘连。"""
        normalized_paths = [
            os.path.normpath(path)
            for path in paths
            if path
        ]
        if not normalized_paths:
            return

        cursor = self.textCursor()
        selection_start = cursor.selectionStart()
        selection_end = cursor.selectionEnd()
        current_text = self.toPlainText()
        previous_character = (
            current_text[selection_start - 1]
            if selection_start > 0
            else ""
        )
        next_character = (
            current_text[selection_end]
            if selection_end < len(current_text)
            else ""
        )
        prefix = " " if previous_character and not previous_character.isspace() else ""
        suffix = " " if next_character and not next_character.isspace() else ""
        quoted_paths = " ".join(f'"{path}"' for path in normalized_paths)
        self.insert_text_at_cursor(f"{prefix}{quoted_paths}{suffix}")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """接受包含本地文件 URL 的拖入操作。"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """接受文件拖动，并避免基类遗留投放位置的临时光标。"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        """把拖入的本地文件路径插入当前光标位置。"""
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            self.insert_file_paths(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
