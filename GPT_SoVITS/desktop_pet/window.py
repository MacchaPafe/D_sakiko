"""桌宠输入视图：角色下边缘的悬浮入口与共享草稿面板。"""

from PyQt5.QtCore import Qt, QEvent, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QMenu,
    QApplication,
)
from ui_main.components.message_input import MessageInput
from runtime.drafts import DraftBinding
from desktop_pet.renderer import PetRenderer


class PetWindow(QWidget):
    openChat = pyqtSignal()
    quitRequested = pyqtSignal()

    def __init__(self, host, commands, playback_events, motion_complete):
        super().__init__()
        self.host = host
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("桌宠")
        self.resize(380, 610)
        self.renderer = PetRenderer(commands, playback_events, motion_complete, self)
        self.renderer.setGeometry(0, 0, 380, 500)
        self.renderer.clicked.connect(self.openChat)
        self.renderer.modelReady.connect(self._model_ready)
        self.renderer.failed.connect(self._model_failed)
        self.renderer.subtitleChanged.connect(self._subtitle)
        self.hovered = False
        self.expanded = False
        self.popup_open = False
        self.anchor = 440
        self.bounds = self.renderer.rect()
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
        self.tools = QFrame(self)
        self.tools.setStyleSheet(
            "QFrame{background:rgba(35,40,50,230);border-radius:12px;} QPushButton{color:white;border:0;padding:9px;font-size:14px;}"
        )
        row = QHBoxLayout(self.tools)
        row.setContentsMargins(4, 0, 4, 0)
        self.text_button = QPushButton("输入", self.tools)
        self.text_button.setToolTip("输入消息")
        self.voice_button = QPushButton("语音", self.tools)
        self.voice_button.setToolTip("语音输入")
        row.addWidget(self.text_button)
        row.addWidget(self.voice_button)
        self.text_button.clicked.connect(self.expand)
        self.voice_button.clicked.connect(self.toggle_voice)
        self.tools.hide()
        self.panel = QFrame(self)
        self.panel.setStyleSheet(
            "QFrame{background:#fafbff;border:1px solid #b9c4d8;border-radius:12px;} QPushButton{color:#233650;background:#e5ebf5;border:1px solid #b8c6dc;padding:6px;border-radius:8px;} QPushButton:hover{background:#d3e0f3;} QPushButton:disabled{color:#79869a;background:#edf0f5;}"
        )
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(8, 8, 8, 6)
        self.input = MessageInput(host._theme_palette, self.panel)
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
        panel_layout.addWidget(self.input)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("+")
        self.record_button = QPushButton("语音")
        self.send_button = QPushButton("发送")
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.record_button)
        buttons.addStretch()
        buttons.addWidget(self.send_button)
        panel_layout.addLayout(buttons)
        self.add_button.clicked.connect(self.add_images)
        self.record_button.clicked.connect(self.toggle_voice)
        self.send_button.clicked.connect(self.send)
        self.panel.hide()
        self.host.voice_input.stateChanged.connect(self.refresh_state)
        self.host.voice_input.recognized.connect(self.recognized)
        self.host.voice_input.error.connect(self.input.show_error)
        self.host.themePaletteChanged.connect(self.input.set_theme_palette)
        QApplication.instance().focusChanged.connect(self.focus_changed)
        QApplication.instance().installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_state)
        self.timer.start(100)
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - 36, area.bottom() - self.height() - 24)
        self.layout_controls()

    def layout_controls(self):
        panel_height = max(158, self.panel.layout().sizeHint().height())
        y = max(8, min(self.anchor - 18, self.height() - panel_height - 8))
        self.tools.setGeometry(
            126, max(8, min(self.anchor - 18, self.height() - 52)), 128, 44
        )
        self.panel.setGeometry(10, y, 360, panel_height)
        self.subtitle.setGeometry(20, max(5, y - 82), 340, 72)
        self.tools.raise_()
        self.panel.raise_()
        self.subtitle.raise_()

    def _model_failed(self, message):
        self.fallback.setText(message + "\n点击打开聊天")
        self.fallback.show()
        self.fallback.raise_()

    def _model_ready(self):
        from live2d_support.runtime_adapter import NullLive2DModel

        if not isinstance(self.renderer.model, NullLive2DModel):
            self.fallback.hide()
        self.bounds = self.renderer.fit_and_bounds()
        self.anchor = self.bounds.bottom()
        self.layout_controls()

    def _subtitle(self, text):
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text) and not self.expanded)

    def show_status(self, text):
        if self.expanded:
            self.input.show_error(text)
        else:
            self._subtitle(text)

    def expand(self):
        self.expanded = True
        self.tools.hide()
        self.subtitle.hide()
        self.panel.show()
        self.panel.raise_()
        self.activateWindow()
        self.input.setFocus()
        self.ensure_on_screen()

    def collapse(self):
        self.expanded = False
        self.panel.hide()
        self.subtitle.setVisible(bool(self.subtitle.text()))
        self.refresh_state()

    def focus_changed(self, old, new):
        if new is not None and new.window() is not self and not self.popup_open:
            if (
                QApplication.activeModalWidget() is None
                and QApplication.activePopupWidget() is None
            ):
                self.collapse()

    def enterEvent(self, event):
        self.hovered = True
        self.refresh_state()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.refresh_state()
        super().leaveEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.renderer and event.type() == QEvent.MouseMove:
            self.hovered = self.bounds.contains(event.pos())
            self.refresh_state()
        if event.type() == QEvent.ApplicationDeactivate and not self.popup_open:
            self.collapse()
        return False

    def refresh_state(self, *args):
        voice = self.host.voice_input.state
        recording = voice == "recording"
        busy = self.host.is_response_active()
        self.send_button.setText("停止回复" if busy else "发送")
        self.send_button.setEnabled(
            busy or not bool(self.input.draft_upload_block_reason())
        )
        self.add_button.setEnabled(
            not busy and self.host._current_model_supports_vision()
        )
        self.record_button.setText(
            "结束录音"
            if recording
            else "识别中…"
            if voice == "transcribing"
            else "重试语音"
            if voice == "unavailable"
            else "语音"
        )
        self.voice_button.setText("结束" if recording else "语音")
        for button in (self.voice_button, self.record_button):
            button.setEnabled(voice in {"idle", "recording", "unavailable"})
        self.tools.setVisible(not self.expanded and (self.hovered or recording))
        if self.expanded:
            self.layout_controls()
        self.renderer.tick_hidden()

    def toggle_voice(self):
        self.host.voice_input.toggle(
            self.host.current_chat_id, self.input.text_edit.textCursor().position()
        )

    def recognized(self, chat_id):
        if (
            chat_id == self.host.current_chat_id
            and self.isActiveWindow()
            and self.isVisible()
        ):
            self.expand()

    def add_images(self):
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

    def send(self):
        if self.host.is_response_active():
            self.host.cancel_active_turn()
        else:
            self.host.handle_user_input(self.input)
        self.refresh_state()

    def ensure_on_screen(self):
        area = self.screen().availableGeometry()
        self.move(
            max(area.left(), min(self.x(), area.right() - self.width() + 1)),
            max(area.top(), min(self.y(), area.bottom() - self.height() + 1)),
        )

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("隐藏桌宠", self.hide)
        menu.addAction("退出程序", self.quitRequested.emit)
        self.popup_open = True
        try:
            menu.exec_(event.globalPos())
        finally:
            self.popup_open = False

    def shutdown(self):
        self.timer.stop()
        QApplication.instance().removeEventFilter(self)
        try:
            QApplication.instance().focusChanged.disconnect(self.focus_changed)
        except (TypeError, RuntimeError):
            pass
        self.renderer.shutdown()
        self.close()
        self.deleteLater()
