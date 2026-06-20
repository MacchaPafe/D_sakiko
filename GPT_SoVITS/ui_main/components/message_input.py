from __future__ import annotations

import dataclasses
import math
import mimetypes
import os
import os.path
from collections.abc import Callable, Sequence

from PyQt5.QtCore import QRect, QSize, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import (
    QDesktopServices,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QInputMethodEvent,
    QKeyEvent,
    QMouseEvent,
    QPixmap,
    QResizeEvent,
    QTextCursor,
    QTextDocument,
)
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chat.attachments import SUPPORTED_IMAGE_MIME_TYPES, detect_image_mime_type
from chat.chat import MessageAttachment


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


@dataclasses.dataclass(frozen=True)
class DraftImageAttachment:
    """输入框草稿中的图片附件。"""

    source_path: str
    mime_type: str
    original_name: str


class _ClickableImageLabel(QLabel):
    """可点击打开图片的缩略图标签。"""

    clicked = pyqtSignal()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """在左键点击时发出 clicked 信号。"""
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class _DraftImagePreviewItem(QFrame):
    """单个草稿图片缩略图及删除按钮。"""

    removeRequested = pyqtSignal(str)
    openRequested = pyqtSignal(str)

    def __init__(self, attachment: DraftImageAttachment, parent: QWidget | None = None) -> None:
        """创建草稿图片预览项。"""
        super().__init__(parent)
        self._attachment = attachment
        self.setObjectName("draftImagePreviewItem")
        self.setFixedSize(78, 78)

        self.image_label = _ClickableImageLabel(self)
        self.image_label.setObjectName("draftImagePreviewImage")
        self.image_label.setFixedSize(70, 70)
        self.image_label.setScaledContents(False)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setToolTip(attachment.original_name)
        self.image_label.clicked.connect(lambda: self.openRequested.emit(self._attachment.source_path))  # noqa
        self._set_thumbnail(attachment.source_path)

        self.remove_button = QToolButton(self)
        self.remove_button.setObjectName("draftImageRemoveButton")
        self.remove_button.setText("x")
        self.remove_button.setToolTip("删除图片")
        self.remove_button.setFixedSize(18, 18)
        self.remove_button.clicked.connect(lambda: self.removeRequested.emit(self._attachment.source_path))  # noqa

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.image_label)
        self.setLayout(layout)
        self.remove_button.move(self.width() - self.remove_button.width(), 0)

    def _set_thumbnail(self, path: str) -> None:
        """加载并设置缩略图。"""
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.image_label.setText("图片")
            return
        thumbnail = pixmap.scaled(
            QSize(70, 70),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image_label.setPixmap(thumbnail)


class MessageTextEdit(QPlainTextEdit):
    """负责实际文本编辑、发送快捷键和拖拽的输入框。"""

    MIN_VISIBLE_LINES = 2
    MAX_VISIBLE_LINES = 6

    def __init__(self, composer: "MessageInput") -> None:
        """初始化文本编辑框的换行、滚动及输入法状态。"""
        super().__init__(composer)
        self._composer = composer
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

        self._composer.sendRequested.emit()
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
        """处理拖入的本地文件路径。"""
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            self._composer.handle_dropped_paths(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class MessageInput(QWidget):
    """支持聊天发送、自动高度、图片预览和文件拖入的复合输入框。"""

    sendRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化复合输入框。"""
        super().__init__(parent)
        self._theme_color = "#7799CC"
        self._vision_support_checker: Callable[[], bool] | None = None
        self._draft_images: list[DraftImageAttachment] = []

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("messageInputErrorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.hide()

        self.preview_area = QScrollArea(self)
        self.preview_area.setObjectName("messageInputPreviewArea")
        self.preview_area.setWidgetResizable(True)
        self.preview_area.setFrameShape(QFrame.NoFrame)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.preview_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview_area.setFixedHeight(84)
        self.preview_area.hide()

        self.preview_content = QWidget()
        self.preview_layout = QHBoxLayout()
        self.preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_layout.setSpacing(6)
        self.preview_layout.addStretch(1)
        self.preview_content.setLayout(self.preview_layout)
        self.preview_area.setWidget(self.preview_content)

        self.text_edit = MessageTextEdit(self)
        self.text_edit.setObjectName("messageTextInput")
        self.text_edit.textChanged.connect(self._handle_text_changed)  # noqa

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.error_label)
        layout.addWidget(self.preview_area)
        layout.addWidget(self.text_edit)
        self.setLayout(layout)
        self.setFocusProxy(self.text_edit)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_theme_color(self._theme_color)

    def set_vision_support_checker(self, checker: Callable[[], bool]) -> None:
        """设置当前模型是否支持视觉输入的同步检查回调。"""
        self._vision_support_checker = checker

    def set_theme_color(self, color: str) -> None:
        """根据主题色刷新输入框内部样式。"""
        self._theme_color = color or "#7799CC"
        self.text_edit.setStyleSheet(f"""
            QPlainTextEdit#messageTextInput {{
                background-color: transparent;
                border: none;
                padding: 4px 2px;
                color: {self._theme_color};
            }}
        """)
        self.error_label.setStyleSheet("""
            QLabel#messageInputErrorLabel {
                color: #B00020;
                font-weight: bold;
            }
        """)
        self.preview_area.setStyleSheet("""
            QScrollArea#messageInputPreviewArea {
                background-color: transparent;
                border: none;
            }
        """)
        self._apply_preview_item_style()
        self.refresh_height()

    def _apply_preview_item_style(self) -> None:
        """刷新图片预览项局部样式。"""
        self.setStyleSheet(f"""
            QFrame#draftImagePreviewItem {{
                background-color: rgba(0, 0, 0, 0.035);
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 6px;
            }}
            QLabel#draftImagePreviewImage {{
                background-color: transparent;
                border: none;
            }}
            QToolButton#draftImageRemoveButton {{
                color: #FFFFFF;
                background-color: rgba(0, 0, 0, 0.55);
                border: none;
                border-radius: 9px;
                font-weight: bold;
            }}
            QToolButton#draftImageRemoveButton:hover {{
                background-color: {self._theme_color};
            }}
        """)

    def _handle_text_changed(self) -> None:
        """用户编辑文本时清除旧错误提示。"""
        self.hide_error()
        self.refresh_height()

    def prepared_message(self) -> str:
        """返回待发送文本，移除首尾纯空白行并保留正文格式。"""
        return trim_surrounding_blank_lines(self.toPlainText())

    def clear_after_send(self) -> None:
        """发送或命令执行成功后清空文本、图片草稿和错误提示。"""
        self.text_edit.clear()
        self.text_edit.document().clearUndoRedoStacks()
        self.clear_draft_images()
        self.hide_error()
        self.refresh_height()

    def replace_draft(self, text: str, image_paths: Sequence[str] = ()) -> list[str]:
        """替换当前草稿内容，并可恢复一组草稿图片。"""
        self.text_edit.setPlainText(text)
        self.text_edit.document().clearUndoRedoStacks()
        self.clear_draft_images()
        failed_paths: list[str] = []
        for image_path in image_paths:
            if not self.add_draft_image_path(image_path, show_error=False):
                failed_paths.append(image_path)
        self.text_edit.moveCursor(QTextCursor.End)
        self.refresh_height()
        self.setFocus()
        return failed_paths

    def insert_text_at_cursor(self, text: str) -> None:
        """在内部文本框光标处插入文本。"""
        self.text_edit.insert_text_at_cursor(text)

    def insert_file_paths(self, paths: Sequence[str]) -> None:
        """在内部文本框光标处插入文件路径。"""
        self.text_edit.insert_file_paths(paths)

    def handle_dropped_paths(self, paths: Sequence[str]) -> None:
        """按当前模型能力处理拖入的本地路径。"""
        paths_to_insert: list[str] = []
        error_message = ""
        for path in paths:
            if not path:
                continue
            if self._is_supported_image_path(path):
                if self._model_supports_vision():
                    self.add_draft_image_path(path)
                else:
                    error_message = "当前模型不支持图片输入，已按普通文件路径插入。"
                    paths_to_insert.append(path)
                continue
            if self._looks_like_image_path(path):
                error_message = "图片无法读取或格式不受支持，已按普通文件路径插入。"
            paths_to_insert.append(path)

        if paths_to_insert:
            self.insert_file_paths(paths_to_insert)
        if error_message:
            self.show_error(error_message)

    def add_draft_image_path(self, path: str, *, show_error: bool = True) -> bool:
        """把本地图片路径添加到草稿附件。"""
        absolute_path = os.path.abspath(path)
        mime_type = detect_image_mime_type(absolute_path)
        if mime_type is None:
            if show_error:
                self.show_error(f"图片不存在、无法读取或格式不受支持：{os.path.basename(path)}")
            return False
        self._draft_images.append(DraftImageAttachment(
            source_path=absolute_path,
            mime_type=mime_type,
            original_name=os.path.basename(path),
        ))
        self.hide_error()
        self._refresh_preview_bar()
        return True

    def clear_draft_images(self) -> None:
        """清空草稿图片。"""
        if not self._draft_images:
            self.preview_area.hide()
            return
        self._draft_images.clear()
        self._refresh_preview_bar()

    def pending_image_source_paths(self) -> list[str]:
        """返回当前草稿图片源路径列表。"""
        return [attachment.source_path for attachment in self._draft_images]

    def optimistic_image_attachments(self) -> list[MessageAttachment]:
        """返回仅用于前端乐观渲染的图片附件列表。"""
        return [
            MessageAttachment(
                type="image",
                path=attachment.source_path,
                mime_type=attachment.mime_type,
                original_name=attachment.original_name,
            )
            for attachment in self._draft_images
        ]

    def validate_pending_images(self) -> list[str]:
        """检查草稿图片源路径是否仍可读取，并返回失败路径。"""
        failed_paths: list[str] = []
        for attachment in self._draft_images:
            if detect_image_mime_type(attachment.source_path) is None:
                failed_paths.append(attachment.source_path)
        return failed_paths

    def show_error(self, message: str) -> None:
        """显示输入框错误提示。"""
        self.error_label.setText(message)
        self.error_label.show()

    def hide_error(self) -> None:
        """隐藏输入框错误提示。"""
        if self.error_label.isVisible():
            self.error_label.clear()
            self.error_label.hide()

    def refresh_height(self) -> None:
        """刷新内部文本框高度。"""
        self.text_edit.refresh_height()
        self.setFixedHeight(self.sizeHint().height())

    def _refresh_preview_bar(self) -> None:
        """根据草稿图片列表刷新预览条。"""
        while self.preview_layout.count() > 0:
            item = self.preview_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for attachment in self._draft_images:
            preview = _DraftImagePreviewItem(attachment, self.preview_content)
            preview.removeRequested.connect(self._remove_draft_image)  # noqa
            preview.openRequested.connect(self._open_image_path)  # noqa
            self.preview_layout.addWidget(preview)
        self.preview_layout.addStretch(1)
        self.preview_area.setVisible(bool(self._draft_images))
        self.refresh_height()

    def _remove_draft_image(self, source_path: str) -> None:
        """从草稿中删除指定图片。"""
        for index, attachment in enumerate(self._draft_images):
            if attachment.source_path == source_path:
                del self._draft_images[index]
                break
        self._refresh_preview_bar()

    def _open_image_path(self, source_path: str) -> None:
        """使用系统默认程序打开图片。"""
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(source_path)):
            self.show_error(f"无法打开图片：{os.path.basename(source_path)}")

    def _model_supports_vision(self) -> bool:
        """调用外部 checker 判断当前模型是否支持视觉输入。"""
        if self._vision_support_checker is None:
            return False
        try:
            return bool(self._vision_support_checker())
        except Exception:
            return False

    @staticmethod
    def _is_supported_image_path(path: str) -> bool:
        """判断路径是否为支持的图片文件。"""
        return detect_image_mime_type(path) is not None

    @staticmethod
    def _looks_like_image_path(path: str) -> bool:
        """根据 MIME 猜测路径是否像图片。"""
        mime_type, _encoding = mimetypes.guess_type(path)
        return isinstance(mime_type, str) and (
            mime_type.startswith("image/")
            or mime_type in SUPPORTED_IMAGE_MIME_TYPES
        )

    def toPlainText(self) -> str:
        """返回内部文本框的纯文本。"""
        return self.text_edit.toPlainText()

    def setPlainText(self, text: str) -> None:
        """设置内部文本框的纯文本。"""
        self.text_edit.setPlainText(text)

    def insertPlainText(self, text: str) -> None:
        """向内部文本框插入纯文本。"""
        self.text_edit.insertPlainText(text)

    def setPlaceholderText(self, text: str) -> None:
        """设置内部文本框占位文本。"""
        self.text_edit.setPlaceholderText(text)

    def moveCursor(self, operation: QTextCursor.MoveOperation) -> None:
        """移动内部文本框光标。"""
        self.text_edit.moveCursor(operation)

    def textCursor(self) -> QTextCursor:
        """返回内部文本框光标。"""
        return self.text_edit.textCursor()

    def setTextCursor(self, cursor: QTextCursor) -> None:
        """设置内部文本框光标。"""
        self.text_edit.setTextCursor(cursor)

    def document(self) -> QTextDocument:
        """返回内部文本框文档对象。"""
        return self.text_edit.document()

    def verticalScrollBar(self) -> QScrollBar:
        """返回内部文本框垂直滚动条。"""
        return self.text_edit.verticalScrollBar()

    def cursorRect(self) -> QRect:
        """返回内部文本框光标矩形。"""
        return self.text_edit.cursorRect()

    def hasFocus(self) -> bool:
        """判断内部文本框是否拥有焦点。"""
        return self.text_edit.hasFocus() or super().hasFocus()
