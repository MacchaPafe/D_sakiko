"""桌宠输入视图：角色下边缘的悬浮入口与共享草稿面板。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from PyQt5 import sip
from PyQt5.QtCore import (
    QByteArray,
    Qt,
    QEvent,
    QTimer,
    QPoint,
    QRect,
    QSize,
    QObject,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QContextMenuEvent, QIcon, QPainter, QShowEvent
from PyQt5.QtWidgets import (
    QWidget,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QMenu,
    QApplication,
    QGraphicsDropShadowEffect,
    QScrollArea,
)
from ui_main.components.message_input import MessageInput
from ui_main.theme import ThemePalette
from runtime.drafts import DraftBinding
from desktop_pet.renderer import PetRenderer
from desktop_pet.focus import create_pet_focus

if TYPE_CHECKING:
    from multiprocessing.queues import Queue as ProcessQueue
    from multiprocessing.sharedctypes import Synchronized
    from queue import Queue
    from qtUI import ChatGUI


class PetWindow(QWidget):
    openChat = pyqtSignal()
    switchToWindow = pyqtSignal()
    quitRequested = pyqtSignal()

    def __init__(
        self,
        host: ChatGUI,
        commands: Queue[dict[str, object]],
        playback_events: ProcessQueue,
        motion_complete: Synchronized,
    ) -> None:
        super().__init__()
        self.host = host
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("桌宠")
        self.resize(380, 610)
        self._focus = create_pet_focus(self)
        self.renderer = PetRenderer(commands, playback_events, motion_complete, self)
        self.renderer.setGeometry(0, 0, 380, 500)
        self.renderer.clicked.connect(self.play_interaction)
        self.renderer.zoomRequested.connect(self.zoom_by)
        self.renderer.modelReady.connect(self._model_ready)
        self.renderer.failed.connect(self._model_failed)
        self.renderer.subtitleChanged.connect(self._subtitle)
        self.hovered = False
        self.expanded = False
        self.popup_open = False
        self.anchor = 440
        self.bounds = self.renderer.rect()
        self.zoom = 1.0
        self.base_bounds = QRect(self.bounds)
        self.fallback = QPushButton("", self)
        self.fallback.setGeometry(65, 140, 250, 100)
        self.fallback.clicked.connect(self.openChat)
        self.fallback.hide()
        self.subtitle = QLabel("", self)
        self.subtitle.setWordWrap(True)
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setStyleSheet(
            "background:rgba(30,35,45,210);color:white;border-radius:8px;padding:7px;"
        )
        self.subtitle.hide()
        self.notice = QLabel("", self)
        self.notice.setObjectName("petNotice")
        self.notice.setWordWrap(True)
        self.notice.setAlignment(Qt.AlignCenter)
        self.notice.hide()
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self.clear_status)
        self.tools = QFrame(self)
        self.tools.setObjectName("petTools")
        self.tools.setStyleSheet(
            "QFrame#petTools{background:rgba(35,40,50,230);border-radius:12px;}"
            "QPushButton{background:transparent;border:1px solid transparent;border-radius:9px;padding:0;}"
            "QPushButton:hover{background:rgba(255,255,255,28);}"
            "QPushButton:pressed{background:rgba(255,255,255,45);}"
            "QPushButton:focus{border-color:rgba(255,255,255,160);}"
        )
        row = QHBoxLayout(self.tools)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(4)
        self.text_button = QPushButton("", self.tools)
        self.text_button.setToolTip("输入消息")
        self.text_button.setAccessibleName("输入消息")
        self.text_button.setIcon(self._tinted_icon("keyboard.svg", "white"))
        self.voice_button = QPushButton("", self.tools)
        self.voice_button.setToolTip("语音输入")
        for button in (self.text_button, self.voice_button):
            button.setFixedSize(36, 36)
            button.setIconSize(QSize(20, 20))
        row.addWidget(self.text_button)
        row.addWidget(self.voice_button)
        self.text_button.clicked.connect(self.expand)
        self.voice_button.clicked.connect(self.toggle_voice)
        self.tools.hide()
        self.panel = QFrame(self)
        self.panel.setObjectName("petComposer")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(20, 30, 45, 40))
        self.panel.setGraphicsEffect(shadow)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(12, 10, 12, 8)
        panel_layout.setSpacing(6)
        self.input = MessageInput(host._theme_palette, self.panel)
        self.input.set_compact_mode(True)
        self.input.set_managed_attachment_mode(True)
        self.input.setPlaceholderText("输入消息…")
        self.input.set_vision_support_checker(host._current_model_supports_vision)
        self.input.set_image_upload_override_handlers(
            host._current_litellm_model_name,
            host._current_model_can_force_image_upload,
            host._force_allow_current_model_image_upload,
        )
        self.input.set_vision_switch_available_checker(
            host._deepseek_vision_switch_available
        )
        self.input.imageAddRequested.connect(host._add_managed_draft_image)
        self.input.imageRemoveRequested.connect(host._remove_managed_draft_image)
        self.input.imageRetryRequested.connect(host._retry_managed_draft_image)
        self.input.visionSwitchRequested.connect(
            host._handle_deepseek_vision_switch_requested
        )
        self.input.sendRequested.connect(self.send)
        self.binding = DraftBinding(host.drafts, self.input, host.current_chat_id)
        self.input_scroll = QScrollArea(self.panel)
        self.input_scroll.setFrameShape(QFrame.NoFrame)
        self.input_scroll.setWidgetResizable(True)
        self.input_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.input_scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:0;} QScrollArea > QWidget > QWidget{background:transparent;}"
        )
        self.input_scroll.setWidget(self.input)
        panel_layout.addWidget(self.input_scroll)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("+")
        self.record_button = QPushButton("")
        self.record_button.setObjectName("petRecord")
        self.send_button = QPushButton("")
        self.send_button.setObjectName("petSend")
        self.add_button.setFixedSize(30, 30)
        self.add_button.setToolTip("添加图片")
        self.add_button.setAccessibleName("添加图片")
        for button in (self.record_button, self.send_button):
            button.setFixedSize(30, 30)
            button.setIconSize(QSize(18, 18))
        self.dismiss_error_button = QPushButton("×")
        self.dismiss_error_button.setFixedSize(30, 30)
        self.dismiss_error_button.setToolTip("关闭错误提示")
        self.dismiss_error_button.setAccessibleName("关闭错误提示")
        self.dismiss_error_button.clicked.connect(self.input.hide_error)
        self.dismiss_error_button.hide()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.record_button)
        buttons.addStretch()
        buttons.addWidget(self.dismiss_error_button)
        buttons.addWidget(self.send_button)
        panel_layout.addLayout(buttons)
        self.add_button.clicked.connect(self.add_images)
        self.record_button.clicked.connect(self.toggle_voice)
        self.send_button.clicked.connect(self.send)
        self.panel.hide()
        if self._focus.passive_mouse:
            for button in (
                self.text_button,
                self.voice_button,
                self.record_button,
                self.send_button,
                self.add_button,
                self.dismiss_error_button,
            ):
                button.setFocusPolicy(Qt.NoFocus)
        self.host.voice_input.stateChanged.connect(self.refresh_state)
        self.host.voice_input.recognized.connect(self.recognized)
        self.host.voice_input.error.connect(self.show_input_error)
        self.host.themePaletteChanged.connect(self.set_theme_palette)
        self.set_theme_palette(host._theme_palette)
        QApplication.instance().focusChanged.connect(self.focus_changed)
        QApplication.instance().installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_state)
        self.timer.start(100)
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - 36, area.bottom() - self.height() - 24)
        self.layout_controls()

    def showEvent(self, event: QShowEvent) -> None:
        """恢复显示时刷新原生悬浮追踪，不激活应用或修正拖动位置。"""
        super().showEvent(event)
        self._focus.refresh_native()

    def nativeEvent(
        self, event_type: QByteArray, message: sip.voidptr
    ) -> tuple[bool, int]:
        """将平台焦点消息交给对应适配，不影响其他原生事件。"""
        focus = getattr(self, "_focus", None)
        if focus is not None:
            handled, result = focus.native_event(event_type, message)
            if handled:
                return True, result
        return super().nativeEvent(event_type, message)

    @staticmethod
    def _tinted_icon(filename: str, color: str) -> QIcon:
        """复用图标资源并按主题着色，保留高分屏所需的像素密度。"""
        path = Path(__file__).resolve().parent.parent / "icons" / filename
        pixmap = QIcon(str(path)).pixmap(QSize(48, 48))
        if pixmap.isNull():
            return QIcon()
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(color))
        painter.end()
        pixmap.setDevicePixelRatio(2.0)
        return QIcon(pixmap)

    def set_theme_palette(self, palette: ThemePalette) -> None:
        """统一卡片、按钮和提示配色，仅强调发送这一主要操作。"""
        self.input.set_theme_palette(palette)
        self.panel.setStyleSheet(f"""
            QFrame#petComposer {{ background: {palette.surface}; border: 1px solid {palette.border_subtle}; border-radius: 16px; }}
            QFrame#petComposer QPushButton {{ color: {palette.text_secondary}; background: transparent; border: 1px solid transparent; border-radius: 9px; padding: 3px 6px; font-size: 13px; }}
            QFrame#petComposer QPushButton:hover {{ background: {palette.surface_selected}; color: {palette.text_primary}; }}
            QFrame#petComposer QPushButton:focus {{ border-color: {palette.focus_ring}; }}
            QFrame#petComposer QPushButton:disabled {{ color: {palette.text_secondary}; background: {palette.surface_tint}; }}
            QFrame#petComposer QPushButton#petSend {{ background: {palette.accent}; color: {palette.on_accent}; padding: 0; }}
            QFrame#petComposer QPushButton#petRecord {{ padding: 0; }}
            QFrame#petComposer QPushButton#petSend:hover {{ background: {palette.accent_hover}; }}
            QFrame#petComposer QPushButton#petSend:pressed {{ background: {palette.accent_pressed}; }}
            QFrame#petComposer QPushButton#petSend:disabled {{ background: {palette.surface_selected}; color: {palette.text_secondary}; }}
        """)
        self.notice.setStyleSheet(
            f"QLabel#petNotice {{ background: {palette.surface_tint}; color: {palette.text_primary}; border: 1px solid {palette.border_subtle}; border-radius: 10px; padding: 8px; }}"
        )
        self._send_icons = {
            False: self._tinted_icon("send.svg", palette.on_accent),
            True: self._tinted_icon("stop.svg", palette.on_accent),
        }
        # 只在主题变化时生成图标，状态轮询直接复用缓存。
        self._voice_icons = {
            filename: (
                self._tinted_icon(filename, "white"),
                self._tinted_icon(filename, palette.text_accent),
            )
            for filename in ("microphone.png", "stop.svg", "refresh.svg")
        }
        self.refresh_state()

    def layout_controls(self) -> None:
        """将固定字号的输入卡片贴近角色下缘，并限制在当前窗口内。"""
        panel_width = min(360, self.width() - 20)
        self.panel.setFixedWidth(panel_width)
        self.panel.layout().activate()
        self.input.refresh_height()
        # 极长错误或附件仍可滚动查看，操作按钮始终留在卡片内。
        self.input_scroll.setFixedHeight(
            min(self.input.height(), max(48, min(360, self.height() - 16) - 60))
        )
        panel_height = max(96, self.panel.layout().sizeHint().height())
        y = max(8, min(self.anchor - 18, self.height() - panel_height - 8))
        self.tools.setGeometry(
            (self.width() - 84) // 2,
            max(8, min(self.anchor - 18, self.height() - 52)),
            84,
            44,
        )
        self.panel.setGeometry(
            (self.width() - panel_width) // 2, y, panel_width, panel_height
        )
        self.subtitle.setGeometry(
            (self.width() - panel_width) // 2, max(5, y - 82), panel_width, 72
        )
        notice_height = max(40, self.notice.heightForWidth(panel_width))
        notice_y = max(5, y - notice_height - 8)
        if self.subtitle.isVisible():
            notice_y = max(5, self.subtitle.y() - notice_height - 8)
        self.notice.setGeometry(
            (self.width() - panel_width) // 2, notice_y, panel_width, notice_height
        )
        self.tools.raise_()
        self.panel.raise_()
        self.subtitle.raise_()
        self.notice.raise_()

    def play_interaction(self) -> None:
        """仅在当前对话和录音都空闲时响应角色单击。"""
        self.refresh_state()
        self.renderer.request_interaction()

    def zoom_by(self, factor: float) -> None:
        """以角色下边缘中心为锚点调整桌宠大小。"""
        self.set_zoom(self.zoom * factor)

    def reset_zoom(self) -> None:
        """恢复本次运行的默认桌宠大小。"""
        self.set_zoom(1.0)

    def set_zoom(self, requested: float) -> None:
        """同步调整渲染视口和稳定轮廓，避免放大模型后被固定视口裁切。"""
        area = self.screen().availableGeometry()
        maximum = min(1.6, area.width() / 380.0, (area.height() - 110) / 500.0)
        zoom = max(min(0.6, maximum), min(maximum, requested))
        foot = self.mapToGlobal(QPoint(self.width() // 2, self.anchor))
        self.zoom = zoom
        render_width, render_height = round(380 * zoom), round(500 * zoom)
        self.resize(min(area.width(), max(380, render_width)), render_height + 110)
        self.renderer.setGeometry(
            (self.width() - render_width) // 2, 0, render_width, render_height
        )
        self.bounds = QRect(
            round(self.base_bounds.x() * zoom),
            round(self.base_bounds.y() * zoom),
            round(self.base_bounds.width() * zoom),
            round(self.base_bounds.height() * zoom),
        )
        self.renderer.hit_bounds = QRect(self.bounds)
        self.anchor = self.bounds.bottom()
        self.fallback.setGeometry(
            (self.width() - 250) // 2, max(20, render_height // 3), 250, 100
        )
        self.layout_controls()
        self.move(foot - QPoint(self.width() // 2, self.anchor))
        self.renderer.update()

    def _model_failed(self, message: str) -> None:
        self.fallback.setText(message + "\n点击打开聊天")
        self.fallback.show()
        self.fallback.raise_()

    def _model_ready(self) -> None:
        from live2d_support.runtime_adapter import NullLive2DModel

        if not isinstance(self.renderer.model, NullLive2DModel):
            self.fallback.hide()
        self.bounds = self.renderer.fit_and_bounds()
        self.base_bounds = QRect(
            round(self.bounds.x() / self.zoom),
            round(self.bounds.y() / self.zoom),
            round(self.bounds.width() / self.zoom),
            round(self.bounds.height() / self.zoom),
        )
        self.renderer.hit_bounds = QRect(self.bounds)
        self.anchor = self.bounds.bottom()
        self.layout_controls()

    def _subtitle(self, text: str) -> None:
        """更新回复字幕，不与操作通知共享文字或计时器。"""
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text) and not self.expanded)
        self.layout_controls()

    def show_status(self, text: str) -> None:
        """显示独立的短时操作通知，不覆盖回复字幕。"""
        self.notice.setText(text)
        self.notice.setVisible(bool(text))
        self.notice_timer.start(4500)
        self.layout_controls()

    def clear_status(self) -> None:
        """收起操作通知并清除旧文本。"""
        self.notice_timer.stop()
        self.notice.clear()
        self.notice.hide()

    def show_input_error(self, message: str) -> None:
        """保留可处理的输入错误，面板收起时另给出短时提示。"""
        self.input.show_error(message)
        if not self.expanded and not self.host.isVisible():
            self.show_status(message)
        self.layout_controls()

    def expand(self) -> None:
        """展开输入卡片，仅在用户主动输入时获取焦点。"""
        self.expanded = True
        self.tools.hide()
        self.subtitle.hide()
        self.panel.show()
        self.panel.raise_()
        self._focus.request_input()
        self.input.setFocus(Qt.MouseFocusReason)
        self.layout_controls()

    def collapse(self) -> None:
        """收起输入卡片并释放非激活面板的键盘焦点，保留草稿。"""
        self.expanded = False
        self.panel.hide()
        self.subtitle.setVisible(bool(self.subtitle.text()))
        self._focus.release_input()
        self.refresh_state()

    def focus_changed(self, old: QWidget | None, new: QWidget | None) -> None:
        if new is not None and new.window() is not self and not self.popup_open:
            if (
                QApplication.activeModalWidget() is None
                and QApplication.activePopupWidget() is None
            ):
                self.collapse()

    def enterEvent(self, event: QEvent) -> None:
        self.hovered = True
        self.refresh_state()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.hovered = False
        self.refresh_state()
        super().leaveEvent(event)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.renderer and event.type() == QEvent.MouseMove:
            self.hovered = self.bounds.contains(event.pos())
            self.refresh_state()
        if (
            event.type() == QEvent.ApplicationDeactivate
            and not self._focus.nonactivating
            and not self.popup_open
        ):
            self.collapse()
        return False

    def refresh_state(self, *args: object) -> None:
        """刷新回复、录音和输入错误状态，保持面板布局稳定。"""
        if (
            self._focus.nonactivating
            and self.expanded
            and not self.popup_open
            and QApplication.activePopupWidget() is None
            and QApplication.activeModalWidget() is None
            and not self._focus.has_input_focus()
        ):
            self.collapse()
            return
        voice = self.host.voice_input.state
        recording = voice == "recording"
        busy = self.host.is_response_active()
        self.renderer.interaction_blocked = busy or voice in {
            "recording",
            "transcribing",
        }
        self.send_button.setIcon(self._send_icons[busy])
        send_label = "停止回复" if busy else "发送"
        self.send_button.setToolTip(send_label)
        self.send_button.setAccessibleName(send_label)
        self.send_button.setEnabled(
            busy or not bool(self.input.draft_upload_block_reason())
        )
        self.add_button.setEnabled(
            not busy and self.host._current_model_supports_vision()
        )
        voice_label = (
            "结束录音"
            if recording
            else "识别中…"
            if voice == "transcribing"
            else "重试语音"
            if voice == "unavailable"
            else "语音输入"
        )
        voice_icon = (
            "stop.svg"
            if recording
            else "refresh.svg"
            if voice == "unavailable"
            else "microphone.png"
        )
        self.dismiss_error_button.setVisible(not self.input.error_bar.isHidden())
        for button, icon in zip(
            (self.voice_button, self.record_button), self._voice_icons[voice_icon]
        ):
            button.setIcon(icon)
            button.setToolTip(voice_label)
            button.setAccessibleName(voice_label)
            button.setEnabled(voice in {"idle", "recording", "unavailable"})
        self.tools.setVisible(not self.expanded and (self.hovered or recording))
        if self.expanded:
            self.layout_controls()
        self.renderer.tick_hidden()

    def toggle_voice(self) -> None:
        if self.host.voice_input.state in {"idle", "unavailable"}:
            self.input.hide_error()
        self.host.voice_input.toggle(
            self.host.current_chat_id, self.input.text_edit.textCursor().position()
        )

    def recognized(self, chat_id: str) -> None:
        if (
            chat_id == self.host.current_chat_id
            and self._focus.has_input_focus()
            and self.isVisible()
        ):
            self.expand()

    def add_images(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        self.popup_open = True
        try:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)"
            )
            for path in paths:
                self.input.request_add_draft_image_path(path)
        finally:
            self.popup_open = False

    def send(self) -> None:
        if self.host.is_response_active():
            self.host.cancel_active_turn()
        else:
            self.host.handle_user_input(self.input)
        self.refresh_state()

    def create_context_menu(self) -> QMenu:
        """创建明确区分临时打开窗口与退出桌宠形态的菜单。"""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.addAction("打开聊天窗口", self.openChat.emit)
        switch = menu.addAction("切换到窗口对话", self.switchToWindow.emit)
        controller = getattr(self.host, "desktop_controller", None)
        allowed = (
            controller.can_switch_mode()
            if controller is not None
            else not self.host.is_response_active()
        )
        switch.setEnabled(allowed)
        if not allowed:
            switch.setToolTip("请等待当前回复、播放或录音完成后切换")
        menu.addAction("恢复默认大小", self.reset_zoom)
        menu.addSeparator()
        menu.addAction("隐藏桌宠", self.hide)
        menu.addAction("退出程序", self.quitRequested.emit)
        return menu

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """通过右键菜单打开窗口，并在菜单关闭后释放资源。"""
        menu = self.create_context_menu()
        self.popup_open = True
        try:
            menu.exec_(event.globalPos())
        finally:
            self.popup_open = False
            menu.deleteLater()

    def shutdown(self) -> None:
        self.timer.stop()
        self.notice_timer.stop()
        QApplication.instance().removeEventFilter(self)
        try:
            QApplication.instance().focusChanged.disconnect(self.focus_changed)
        except (TypeError, RuntimeError):
            pass
        self.renderer.shutdown()
        self.close()
        self.deleteLater()
