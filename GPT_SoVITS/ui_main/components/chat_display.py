from __future__ import annotations

import html
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote
from dataclasses import dataclass

from PyQt5.QtCore import QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QContextMenuEvent, QDesktopServices, QImageReader, QTextCursor
from PyQt5.QtWidgets import QAction, QTextBrowser, QWidget

from chat.attachments import resolve_attachment_path
from chat.chat import Chat, Message, SingleCharacterPromptGenerator


@dataclass(frozen=True)
class _MessageDisplayMeta:
    """记录一条已渲染消息在显示层需要使用的元数据。"""

    is_user_message: bool
    can_edit_message: bool
    can_edit_and_resend: bool
    can_rollback: bool
    can_regenerate_turn_reply: bool
    emotion_label: str = "happiness"


class ChatDisplay(QTextBrowser):
    """集成的聊天记录渲染模块，封装聊天记录的渲染、流式打印和交互事件。"""

    deleteMessageRequested = pyqtSignal(int)
    deleteTurnRequested = pyqtSignal(int)
    editMessageRequested = pyqtSignal(int)
    editAndResendRequested = pyqtSignal(int)
    rollbackRequested = pyqtSignal(int)
    regenerateTurnReplyRequested = pyqtSignal(int)
    regenerateAudioRequested = pyqtSignal(int)
    toolCallClicked = pyqtSignal(str)
    audioLinkClicked = pyqtSignal(QUrl)
    streamFinished = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化聊天显示控件及其内部流式渲染状态。"""
        super().__init__(parent)
        self._theme_color = "#7799CC"
        self._user_persona_name: str | None = None
        # 每条消息的缓存信息：用于记录这条消息是否是用户消息
        # 消息索引 -> 消息显示元数据
        # 这个信息会用于决定右键菜单长什么样（是否有重新生成音频的选项）以及流式打印时是否先渲染消息头
        self._message_meta_by_index: dict[int, _MessageDisplayMeta] = {}
        self._stream_text = ""
        self._stream_translation = ""
        self._stream_index = 0
        self._stream_timer = QTimer(self)
        self._stream_timer.timeout.connect(self._stream_next_character)  # noqa

        self.setPlaceholderText("这里显示聊天记录...")
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._on_anchor_clicked)  # noqa

    def set_theme_color(self, color: str) -> None:
        """设置后续消息渲染使用的主题色。"""
        self._theme_color = color or "#7799CC"

    def render_chat(self, chat: Chat, preserve_scroll: bool = True) -> None:
        """根据完整 Chat 数据重新渲染聊天记录。"""
        self._stop_stream(clear_buffer=True)
        self._user_persona_name = self._user_persona_name_from_chat(chat)
        # 记录当前滚动条状态，以便在刷新后尽可能保持不变
        scroll_bar = self.verticalScrollBar()
        saved_position = scroll_bar.value()
        was_at_bottom = saved_position == scroll_bar.maximum()

        self._message_meta_by_index.clear()
        html_parts: list[str] = []
        records_by_index = self._tool_records_by_message_index(chat)

        for index, message in enumerate(chat.message_list):
            message_block: list[str] = []
            for record in records_by_index.get(index, []):
                message_block.append(self._render_tool_record_html(record))
            message_block.append(self._render_message_html(message, index))
            html_parts.append("".join(message_block))
            self._remember_message_meta(index, message)
        self._mark_regenerable_turn_reply(chat)

        self.setHtml("<br><br>".join(html_parts))

        if not preserve_scroll:
            QTimer.singleShot(10, lambda: scroll_bar.setValue(scroll_bar.maximum()))
            return
        if was_at_bottom:
            QTimer.singleShot(10, lambda: scroll_bar.setValue(scroll_bar.maximum()))
        else:
            QTimer.singleShot(10, lambda: scroll_bar.setValue(saved_position))

    def clear_chat(self) -> None:
        """清空聊天显示内容及所有显示层缓存状态。"""
        self._stop_stream(clear_buffer=True)
        self._message_meta_by_index.clear()
        self.clear()

    def append_message(
        self,
        message: Message,
        msg_index: int,
        *,
        stream: bool = False,
        interval_ms: int = 30,
    ) -> None:
        """追加显示一条消息，可选择使用逐字流式打印正文。"""
        self.finish_stream_now()
        self._remember_message_meta(msg_index, message)
        if Chat.is_real_user_message(message):
            self._set_regenerable_turn_reply_index(msg_index)
        if stream and not message.attachments:
            self._append_message_streaming(message, msg_index, interval_ms)
            return
        self.append(self._render_message_html(message, msg_index))

    def append_tool_status_line(
        self,
        tool_call_id: str,
        text: str | None = None,
        *,
        status: str = "running",
        duration_sec: float = 0.0,
    ) -> None:
        """追加一条工具调用状态行。"""
        self.finish_stream_now()
        self.append(self._render_tool_status_html(
            tool_call_id,
            text if text is not None else self._tool_status_text(status, duration_sec),
        ))

    def is_streaming(self) -> bool:
        """返回当前是否仍在逐字打印消息正文。"""
        return self._stream_timer.isActive()

    def finish_stream_now(self) -> None:
        """立即补完当前流式消息的剩余正文和翻译。"""
        if not self._stream_timer.isActive():
            return
        self._stream_timer.stop()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        if self._stream_index < len(self._stream_text):
            cursor.insertText(self._stream_text[self._stream_index:])
            self._stream_index = len(self._stream_text)
        self._insert_stream_translation(cursor)
        self.setTextCursor(cursor)
        self.moveCursor(QTextCursor.End)
        self._clear_stream_buffer()
        self.streamFinished.emit()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """根据消息锚点扩展标准右键菜单。"""
        url = self.anchorAt(event.pos())
        msg_index = self._message_index_from_url(url)
        menu = self.createStandardContextMenu(event.pos())

        if msg_index is not None:
            meta = self._message_meta_by_index.get(msg_index)
            last_message_index = max(self._message_meta_by_index.keys(), default=-1)
            can_rollback_to_here = (
                meta is not None
                and meta.can_rollback
                and msg_index < last_message_index
            )
            if (
                meta is not None
                and (
                    meta.can_regenerate_turn_reply
                    or meta.can_edit_message
                    or meta.can_edit_and_resend
                    or can_rollback_to_here
                )
            ):
                menu.addSeparator()
                if meta.can_regenerate_turn_reply:
                    regenerate_turn_action = QAction("重新生成本轮回复", self)
                    regenerate_turn_action.triggered.connect(
                        lambda: self.regenerateTurnReplyRequested.emit(msg_index)
                    )
                    menu.addAction(regenerate_turn_action)
                if meta.can_edit_message and meta.is_user_message:
                    edit_message_action = QAction("编辑消息", self)
                    edit_message_action.triggered.connect(lambda: self.editMessageRequested.emit(msg_index))
                    menu.addAction(edit_message_action)
                if meta.can_edit_and_resend:
                    edit_and_resend_action = QAction("编辑并重发", self)
                    edit_and_resend_action.triggered.connect(
                        lambda: self.editAndResendRequested.emit(msg_index)
                    )
                    menu.addAction(edit_and_resend_action)
                if can_rollback_to_here:
                    rollback_action = QAction("回溯到此处", self)
                    rollback_action.triggered.connect(lambda: self.rollbackRequested.emit(msg_index))
                    menu.addAction(rollback_action)
                menu.addSeparator()
            else:
                menu.addSeparator()

            delete_action = QAction("删除此消息", self)
            delete_action.triggered.connect(lambda: self.deleteMessageRequested.emit(msg_index))
            menu.addAction(delete_action)

            delete_turn_text = "删除此轮对话" if meta is not None and meta.is_user_message else "删除所属轮次"
            delete_turn_action = QAction(delete_turn_text, self)
            delete_turn_action.triggered.connect(lambda: self.deleteTurnRequested.emit(msg_index))
            menu.addAction(delete_turn_action)

            if meta is not None and not meta.is_user_message:
                menu.addSeparator()
                regen_action = QAction(f"重新生成音频：情绪{meta.emotion_label}", self)
                regen_action.triggered.connect(lambda: self.regenerateAudioRequested.emit(msg_index))
                menu.addAction(regen_action)

        menu.exec_(event.globalPos())

    def _append_message_streaming(self, message: Message, msg_index: int, interval_ms: int) -> None:
        """追加消息标题，并启动正文的逐字打印。"""
        self.append(self._render_message_header_html(message, msg_index))
        self._stream_text = f"{message.text}\n"
        self._stream_translation = message.translation or ""
        self._stream_index = 0
        self._stream_timer.start(max(1, interval_ms))

    def _stream_next_character(self) -> None:
        """打印流式正文的下一个字符，或在末尾插入翻译。"""
        cursor = self.textCursor()
        # 如果在打印外文原文：逐个字符打印
        if self._stream_index < len(self._stream_text):
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(self._stream_text[self._stream_index])
            self.setTextCursor(cursor)
            self._stream_index += 1
            return

        self._stream_timer.stop()
        # 如果在打印翻译：一次性插入
        cursor.movePosition(QTextCursor.End)
        self._insert_stream_translation(cursor)
        self.moveCursor(QTextCursor.End)
        self._clear_stream_buffer()
        self.streamFinished.emit()

    def _insert_stream_translation(self, cursor: QTextCursor) -> None:
        """在流式消息末尾插入翻译文本。"""
        if not self._stream_translation:
            return
        safe_translation = html.escape(self._stream_translation)
        cursor.insertHtml(
            f'<span style="color: #B3D1F2; font-style: italic;">{safe_translation}</span><br>'
        )

    def _stop_stream(self, *, clear_buffer: bool) -> None:
        """停止当前流式打印，并按需清空缓冲区。"""
        if self._stream_timer.isActive():
            self._stream_timer.stop()
            self.streamFinished.emit()
        if clear_buffer:
            self._clear_stream_buffer()

    def _clear_stream_buffer(self) -> None:
        """清空当前流式打印的内部缓冲。"""
        self._stream_text = ""
        self._stream_translation = ""
        self._stream_index = 0

    def _remember_message_meta(self, msg_index: int, message: Message) -> None:
        """记录消息索引对应的右键菜单元数据。"""
        self._message_meta_by_index[msg_index] = _MessageDisplayMeta(
            is_user_message=self._is_user_message(message),
            can_edit_message=Chat.can_edit_message(message),
            can_edit_and_resend=Chat.can_edit_and_resend_user_message(message),
            can_rollback=Chat.can_rollback_to_message(message),
            can_regenerate_turn_reply=False,
            emotion_label=self._message_emotion_label(message),
        )

    def _mark_regenerable_turn_reply(self, chat: Chat) -> None:
        """标记最后一条真实用户消息是否允许重新生成本轮回复。"""
        message_index = chat.find_last_real_user_message_index()
        self._set_regenerable_turn_reply_index(message_index)

    def _set_regenerable_turn_reply_index(self, message_index: int | None) -> None:
        """将可重新生成本轮回复的标记移动到指定消息。"""
        for index, meta in list(self._message_meta_by_index.items()):
            self._message_meta_by_index[index] = _MessageDisplayMeta(
                is_user_message=meta.is_user_message,
                can_edit_message=meta.can_edit_message,
                can_edit_and_resend=meta.can_edit_and_resend,
                can_rollback=meta.can_rollback,
                can_regenerate_turn_reply=index == message_index,
                emotion_label=meta.emotion_label,
            )
        if message_index is None:
            return

    @staticmethod
    def _message_emotion_label(message: Message) -> str:
        try:
            return message.emotion.as_string()
        except Exception:
            return "happiness"

    def _render_message_html(self, message: Message, msg_index: int) -> str:
        """将一条消息渲染成完整 HTML 片段。"""
        display_message = self._message_for_display(message)
        safe_text = html.escape(display_message.text)
        header = self._render_message_header_html(message, msg_index)
        attachments_html = self._render_attachments_html(display_message)
        if self._is_user_message(display_message):
            return header.replace("</a>", f"{safe_text}</a>{attachments_html}", 1)
        body = f"{safe_text}</a>"
        if display_message.translation:
            safe_translation = html.escape(display_message.translation)
            body += f'<br><span style="color: #B3D1F2; font-style: italic;">{safe_translation}</span>'
        body += attachments_html
        return header.replace("</a>", body, 1)

    def _render_message_header_html(self, message: Message, msg_index: int) -> str:
        """将一条消息的可点击标题渲染成 HTML。"""
        msg_param = f"?msg={msg_index}"
        if self._is_user_message(message):
            display_name = "你"
            if self._user_persona_name is not None:
                display_name = f"你（{self._user_persona_name}）"
            return (
                f'<a href="user:{msg_param}" '
                f'style="text-decoration: none; color: {self._theme_color};">'
                f'{html.escape(display_name)}：</a>'
            )

        if message.audio_path and message.audio_path != "NO_AUDIO":
            abs_path = os.path.abspath(message.audio_path).replace("\\", "/")
            return (
                f'<a href="{abs_path}[{message.emotion.as_label()}]{msg_param}" '
                f'style="text-decoration: none; color: {self._theme_color};">'
                f'★{html.escape(message.character_name)}：</a>'
            )
        return (
            f'<a href="no_audio:{msg_param}" '
            f'style="text-decoration: none; color: {self._theme_color};">'
            f'{html.escape(message.character_name)}：</a>'
        )

    def _render_tool_record_html(self, record: dict[str, object]) -> str:
        """将持久化的工具调用记录渲染成 HTML。"""
        status = str(record.get("status") or "running")
        duration_sec = float(record.get("duration_sec") or 0.0)
        return self._render_tool_status_html(
            str(record.get("tool_call_id") or ""),
            self._tool_status_text(status, duration_sec),
        )

    @staticmethod
    def _render_attachments_html(message: Message) -> str:
        """渲染消息图片附件缩略图。"""
        image_blocks: list[str] = []
        for attachment in message.attachments:
            if not attachment.is_image():
                continue
            image_path = resolve_attachment_path(attachment.path)
            if not image_path.exists() or not image_path.is_file():
                image_blocks.append(
                    '<span style="display: inline-block; margin-top: 6px; '
                    'padding: 4px 8px; color: #B00020; '
                    'background-color: rgba(176, 0, 32, 0.08); '
                    'border-radius: 4px;">[图片已丢失]</span>'
                )
                continue
            abs_path = str(image_path.resolve()).replace("\\", "/")
            safe_src = html.escape(abs_path, quote=True)
            safe_title = html.escape(attachment.original_name or os.path.basename(abs_path), quote=True)
            href = f"image:{quote(abs_path, safe='')}"
            size_attributes = ChatDisplay._image_thumbnail_size_attributes(image_path)
            image_blocks.append(
                f'<a href="{href}" style="text-decoration: none;">'
                f'<img src="{safe_src}" title="{safe_title}" {size_attributes} '
                f'style="margin-top: 6px; margin-right: 6px; '
                f'border-radius: 6px; vertical-align: top;" />'
                f'</a>'
            )
        if not image_blocks:
            return ""
        return '<br><span style="display: inline-block;">' + "".join(image_blocks) + "</span>"

    @staticmethod
    def _image_thumbnail_size_attributes(image_path: Path, max_size: int = 120) -> str:
        """根据原图尺寸生成 QTextBrowser 可识别的缩略图宽高属性。"""
        image_size = QImageReader(str(image_path)).size()
        if not image_size.isValid() or image_size.width() <= 0 or image_size.height() <= 0:
            return f'width="{max_size}" height="{max_size}"'

        scale = min(
            1.0,
            max_size / image_size.width(),
            max_size / image_size.height(),
        )
        width = max(1, int(image_size.width() * scale))
        height = max(1, int(image_size.height() * scale))
        return f'width="{width}" height="{height}"'

    @staticmethod
    def _render_tool_status_html(tool_call_id: str, text: str) -> str:
        """根据工具调用 ID 和展示文本生成状态行 HTML。"""
        safe_text = html.escape(text)
        style = "color: #B3D1F2; font-size: normal; font-style: normal;"
        safe_tool_call_id = html.escape(tool_call_id, quote=True)
        return (
            f'<div style="margin-top: 0px; margin-bottom: 0px;">'
            f'<a href="toolcall:{safe_tool_call_id}" style="text-decoration: none;">'
            f'<span style="{style}">{safe_text}</span>'
            f'</a>'
            f'</div>'
        )

    @staticmethod
    def _tool_status_text(status: str, duration_sec: float = 0.0) -> str:
        """根据工具调用状态生成用户可读的状态文案。"""
        if status == "completed":
            return f"<工具调用完成 {duration_sec:.2f}秒>"
        return "<正在调用工具...>"

    @staticmethod
    def _tool_records_by_message_index(chat: Chat) -> dict[int, list[dict[str, object]]]:
        """按消息索引分组当前对话中的工具调用记录。"""
        records_by_index: dict[int, list[dict[str, object]]] = {}
        for record in chat.get_tool_call_record_dicts():
            msg_index = record.get("message_index")
            if isinstance(msg_index, int):
                records_by_index.setdefault(msg_index, []).append(record)
        return records_by_index

    @staticmethod
    def _user_persona_name_from_chat(chat: Chat) -> str | None:
        """读取单角色对话中冻结的用户人设名称。"""
        prompt_generator = chat.prompt_generator
        if not isinstance(prompt_generator, SingleCharacterPromptGenerator):
            return None
        if prompt_generator.user_persona is None:
            return None
        return prompt_generator.user_persona.name

    @staticmethod
    def _message_for_display(message: Message) -> Message:
        """返回仅用于显示的消息对象，必要时美化内部事件文案。"""
        if message.character_name != "User":
            return message
        display_text = ChatDisplay._format_internal_event_user_text_for_display(message.text)
        if display_text == message.text:
            return message
        return Message(
            character_name=message.character_name,
            text=display_text,
            translation=message.translation,
            emotion=message.emotion,
            audio_path=message.audio_path,
            attachments=message.attachments,
        )

    @staticmethod
    def _format_internal_event_user_text_for_display(raw_text: str) -> str:
        """将内部事件注入消息转换为更适合聊天框展示的文本。"""
        text = str(raw_text or "").strip()
        if not text.startswith("【系统内部事件触发"):
            return text

        event_name_match = re.search(r"^【系统内部事件触发：([^】]+)】", text)
        event_name = event_name_match.group(1).strip() if event_name_match else "系统事件"

        reminder_match = re.search(r"事件内容：【([^】]+)】", text)
        if reminder_match:
            return f"（自动触发定时器事件：{reminder_match.group(1).strip()}）"

        lottery_theme_match = re.search(r"抽签主题：(.+?)。", text)
        lottery_result_match = re.search(r"结果是：(.+?)。", text)
        if lottery_theme_match and lottery_result_match:
            theme = lottery_theme_match.group(1).strip()
            winner = lottery_result_match.group(1).strip()
            return f"（自动触发抽签结果事件：{theme} -> {winner}）"

        return f"（自动触发事件：{event_name}）"

    @staticmethod
    def _is_user_message(message: Message) -> bool:
        """判断消息是否来自用户。"""
        return message.character_name == "User"

    @staticmethod
    def _message_index_from_url(url: str) -> int | None:
        """从消息锚点 URL 中解析数据模型消息索引。"""
        if not url:
            return None
        match = re.search(r"\?msg=(\d+)", url)
        if not match:
            return None
        return int(match.group(1))

    def _on_anchor_clicked(self, anchor_url: QUrl) -> None:
        """把聊天框中的链接点击转换成语义化信号。"""
        url_text = anchor_url.toString()
        if url_text.startswith("toolcall:"):
            self.toolCallClicked.emit(url_text[len("toolcall:"):])
            return
        if url_text.startswith("image:"):
            image_path = unquote(url_text[len("image:"):])
            QDesktopServices.openUrl(QUrl.fromLocalFile(image_path))
            return
        self.audioLinkClicked.emit(anchor_url)
