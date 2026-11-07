"""轻量角色主题启动菜单；只负责入口与进程状态。"""
from __future__ import annotations

from pathlib import Path
import random

from PyQt5.QtCore import Qt, QTimer, QUrl, QVariantAnimation, QEasingCurve, QRectF, QSettings, QSize
from PyQt5.QtGui import QDesktopServices, QFont, QFontDatabase, QIcon, QPainter, QPixmap, QColor, QPalette, QLinearGradient, QRadialGradient, QRegion, QBitmap
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QVBoxLayout, QWidget, QSizePolicy,
)

from launcher_actions import ENTRY_BY_KEY, LauncherProcesses, preferred_font

MODES = {
    "desktop": ("桌面端&桌宠", "日常小助手", "启动"),
    "webui": ("WebUI", "通过局域网在手机或平板上体验数字小祥", "启动"),
    "theater": ("小剧场模式", "方便且自由地编排两名角色的互动剧情", "启动"),
}

class LogoLabel(QLabel):
    """用逻辑尺寸直接绘制原图，避免切换不同缩放屏幕后重复缩放位图。"""

    def __init__(self, path: Path, text: str, height: int):
        super().__init__()
        self.source_pixmap = QPixmap(str(path))
        if not self.source_pixmap.isNull() and self.source_pixmap.hasAlphaChannel():
            # 只在显示时裁掉透明留白；阈值掩码忽略几乎不可见的残留像素。
            mask = self.source_pixmap.toImage().createAlphaMask(Qt.ThresholdDither)
            bounds = QRegion(QBitmap.fromImage(mask)).boundingRect()
            if not bounds.isEmpty():
                self.source_pixmap = self.source_pixmap.copy(bounds)
        self.setText(text)
        self.setAccessibleName(text)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def sizeHint(self):
        if self.source_pixmap.isNull():
            return super().sizeHint()
        return QSize(round(self.height() * self.source_pixmap.width()
                           / self.source_pixmap.height()), self.height())

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        if self.source_pixmap.isNull():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        aspect = self.source_pixmap.width() / self.source_pixmap.height()
        height = min(self.height(), self.width() / aspect)
        width = height * aspect
        target = QRectF((self.width() - width) / 2, (self.height() - height) / 2,
                        width, height)
        painter.drawPixmap(target, self.source_pixmap, QRectF(self.source_pixmap.rect()))


class WatermarkOverlay(QWidget):
    """位于按钮之上的水印层；不接收鼠标、键盘焦点。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, event):
        window = self.parentWidget()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        for key, weight in ((window.previous_mode, 1.0 - window.fade), (window.selected_mode, window.fade)):
            pixmap = window.watermarks[key]
            if pixmap.isNull() or weight <= 0:
                continue
            height = min(self.height() * 0.53, 390)
            width = height * pixmap.width() / pixmap.height()
            target = QRectF(self.width() - width - 22, self.height() - height + 24, width, height)
            painter.setOpacity(0.28 * weight)
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        painter.end()


class LauncherWindow(QDialog):
    def __init__(self, root: Path, processes: LauncherProcesses | None = None):
        super().__init__()
        self.root = root.resolve()
        self.easter_egg_player = None
        self.processes = processes or LauncherProcesses(self.root)
        self.settings = QSettings("DSakiko", "Launcher")
        saved_mode = self.settings.value("lastMode", "desktop")
        self.selected_mode = saved_mode if saved_mode in MODES else "desktop"
        self.watermark_files = {
            "desktop": ["desktop.png"],
            "webui": ["webui-phone-skirt.png"],
            "theater": ["theater_1.png", "theater_2.png", "theater_3.png", "theater_4.png"]
        }
        self.watermarks = {
            key: QPixmap(str(self.root / "launcher/assets" / random.choice(filename_list)))
            for key, filename_list in self.watermark_files.items()
        }
        self.previous_mode = self.selected_mode
        self.fade = 1.0
        self.transition = QVariantAnimation(self)
        self.transition.setDuration(220)
        self.transition.setStartValue(0.0)
        self.transition.setEndValue(1.0)
        self.transition.setEasingCurve(QEasingCurve.InOutQuad)
        self.transition.valueChanged.connect(self.animate_watermark)
        self.buttons = {}
        self.mode_buttons = {}

        self.setWindowTitle("数字小祥启动器")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
        self.setMinimumSize(680, 560)
        self.resize(760, 600)
        self.setWindowIcon(QIcon(str(self.root / "launcher/assets/logo_thumb.png")))

        # 固定启动器的浅色调色板，包括禁用和失焦状态；不改变主程序主题。
        palette = self.palette()
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            for role, color in (
                (QPalette.Window, "#F2F8FD"), (QPalette.Base, "#FFFFFF"),
                (QPalette.AlternateBase, "#EEF1F7"), (QPalette.Button, "#F0F3F9"),
                (QPalette.WindowText, "#000000"), (QPalette.Text, "#000000"),
                (QPalette.ButtonText, "#26324A"), (QPalette.Highlight, "#DCE5F5"),
                (QPalette.HighlightedText, "#000000"), (QPalette.Link, "#365A91"),
                (QPalette.LinkVisited, "#64518A"), (QPalette.ToolTipBase, "#F2F8FD"),
                (QPalette.ToolTipText, "#26324A"),
            ):
                palette.setColor(group, role, QColor(color))
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            palette.setColor(QPalette.Disabled, role, QColor("#667085"))
        self.setPalette(palette)

        self.setStyleSheet("""
            QDialog {
                background: #F2F8FD;
                color: #000000;
            }
            QLabel {
                background: transparent;
                color: #000000;
            }
            QTextBrowser, QListWidget {
                background: #FFFFFF;
                color: #000000;
                selection-background-color: #DCE5F5;
                selection-color: #000000;
            }
            QProgressBar {
                background: #EEF1F7;
                color: #000000;
                border: 1px solid #CBD3E1;
                border-radius: 4px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #BCCAE3;
            }
            QToolTip {
                background: #F2F8FD;
                color: #26324A;
                border: 1px solid #CBD3E1;
            }
            QPushButton {
                background: #F0F3F9;
                color: #26324A;
                border: 1px solid #CBD3E1;
                border-radius: 6px;
                padding: 7px 14px;
            }
            QPushButton:hover {
                background: #E5EBF5;
                color: #26324A;
            }
            QPushButton:pressed {
                background: #D9E2F1;
                color: #26324A;
            }
            QPushButton:default, QPushButton:focus {
                border-color: #6775A8;
            }

            QFrame#card {
                background: transparent;
                border: none;
            }
            QFrame#accent {
                background: #6FB4E3;
                border: none;
                border-radius: 1px;
            }
            QLabel#heading {
                font-size: 30px;
                font-weight: 700;
                color: #384260;
                letter-spacing: 2px;
            }
            QLabel#latin {
                font-size: 12.5px;
                font-weight: 600;
                letter-spacing: 6px;
                color: #8A9BB5;
            }

            /* 当前选中的模式标题与描述 */
            QLabel#modeTitle {
                font-size: 24px;
                font-weight: 700;
                color: #2F374E;
                letter-spacing: 1px;
            }
            QLabel#description {
                font-size: 13.5px;
                color: #7A859E;
            }
            QLabel#status {
                font-size: 12px;
                color: #6C789E;
                font-weight: 500;
            }

            QPushButton[mode="true"] {
                min-width: 105px;
                padding: 8px 18px;
                font-size: 13.5px;
                font-weight: 500;
                color: #75819B;
                background: rgba(235, 240, 248, 0.7);
                border: 1px solid rgba(220, 228, 240, 0.8);
                border-radius: 12px;
            }
            QPushButton[mode="true"]:hover {
                background: #FFFFFF;
                color: #4C567A;
                border-color: #CBD7E8;
            }
            QPushButton[mode="true"]:checked {
                background: #FFFFFF;
                color: #434F78;
                font-weight: 700;
                border: 1.5px solid #6FB4E3;
            }

            QPushButton#launch {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #6FB4E3, stop:1 #4C93C9);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.4);
                border-radius: 16px;
                font-size: 16.5px;
                font-weight: 700;
                letter-spacing: 1.5px;
                padding: 15px 36px;
            }
            QPushButton#launch:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #82C0EA, stop:1 #5BA0D4);
                border: 1px solid rgba(255, 255, 255, 0.8);
            }
            QPushButton#launch:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4C93C9, stop:1 #3F7FB2);
                padding-top: 17px;
                padding-bottom: 13px;
            }
            QPushButton#launch:disabled {
                background: #E2E6EF;
                color: #596579;
                border: none;
            }

            QPushButton[tool="true"] {
                background: rgba(255, 255, 255, 0.9);
                border: 1.5px solid #E2E7F0;
                border-radius: 12px;
                color: #4F5A76;
                font-size: 13px;
                font-weight: 500;
                padding: 10px 18px;
            }
            QPushButton[tool="true"]:hover {
                background: #FFFFFF;
                border-color: #6775A8;
                color: #525E8C;
            }
            QPushButton[tool="true"]:pressed {
                background: #F1F4FA;
            }

            /* 彩蛋与状态通知 */
            QLabel#notice {
                font-size: 11.5px;
                color: #8C96AC;
            }

            /* 最下方安静操作按钮 */
            QPushButton[quiet="true"] {
                background: transparent;
                border: none;
                font-size: 11.5px;
                color: #8C96AC;
                padding: 6px 10px;
                border-radius: 6px;
            }
            QPushButton[quiet="true"]:hover {
                background: rgba(0, 0, 0, 0.04);
                color: #525E8C;
            }
            QPushButton:disabled,
            QPushButton[mode="true"]:disabled,
            QPushButton[tool="true"]:disabled,
            QPushButton[quiet="true"]:disabled {
                background: #E7EBF2;
                color: #667085;
                border-color: #D1D8E3;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 12)
        layout.setSpacing(8)

        brand = QVBoxLayout()
        brand.setSpacing(10)
        self.logo = LogoLabel(self.root / "launcher/assets/logo.png", "DSakiko", 58)
        self.logo.setObjectName("logo")
        if self.logo.source_pixmap.isNull():
            self.logo.setObjectName("heading")
        brand.addWidget(self.logo)
        self.chinese_logo = LogoLabel(self.root / "launcher/assets/logo_zh.png",
                                      "数字小祥", 28)
        self.chinese_logo.setObjectName("chineseLogo")
        brand.addWidget(self.chinese_logo)
        layout.addLayout(brand)
        layout.addSpacing(18)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        mode_row.addStretch()
        for key, (name, _, _) in MODES.items():
            button = QPushButton(name.replace("&", "&&"))
            button.setCheckable(True)
            button.setProperty("mode", True)
            button.setAccessibleName(f"选择{name}")
            button.clicked.connect(lambda checked=False, key=key: self.select_mode(key))
            self.mode_buttons[key] = button
            mode_row.addWidget(button)
        mode_row.addStretch()
        layout.addLayout(mode_row)
        layout.addSpacing(6)

        card = self.card = QFrame()
        card.setObjectName("card")
        card.setMaximumWidth(460)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 14, 28, 10)
        card_layout.setSpacing(8)

        self.mode_title = QLabel()
        self.mode_title.setObjectName("modeTitle")
        self.mode_title.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.mode_title)

        accent = QFrame()
        accent.setObjectName("accent")
        accent.setFixedSize(36, 3)
        card_layout.addSpacing(4)
        card_layout.addWidget(accent, 0, Qt.AlignHCenter)
        card_layout.addSpacing(2)

        self.description = QLabel()
        self.description.setObjectName("description")
        self.description.setAlignment(Qt.AlignCenter)
        self.description.setWordWrap(True)
        card_layout.addWidget(self.description)
        card_layout.addSpacing(8)

        self.launch_button = QPushButton()
        self.launch_button.setObjectName("launch")
        self.launch_button.setMinimumWidth(200)
        self.launch_button.setCursor(Qt.PointingHandCursor)
        self.launch_button.clicked.connect(lambda: self.launch(self.selected_mode))
        card_layout.addWidget(self.launch_button, 0, Qt.AlignHCenter)

        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(34)
        card_layout.addWidget(self.status)

        card_row = QHBoxLayout()
        card_row.addStretch()
        card_row.addWidget(card, 1)
        card_row.addStretch()
        layout.addLayout(card_row)
        layout.addStretch(1)

        tools = QHBoxLayout()
        tools.setSpacing(12)
        tools.addStretch()
        for key, name in (("config", "全局设置"), ("downloader", "Live2D模型下载"), ("editor", "动作组编辑")):
            button = QPushButton(name)
            button.setProperty("tool", True)
            button.setMinimumWidth(136)
            button.clicked.connect(lambda checked=False, key=key: self.launch(key))
            self.buttons[key] = button
            tools.addWidget(button)
        tools.addStretch()
        layout.addLayout(tools)

        bottom_notice: str = "" if random.random() < 0.6 else "夢はパワー (≧▽≦)"
        self.notice = self.secondary(bottom_notice)
        self.notice.setObjectName("notice")
        self.notice.setAlignment(Qt.AlignCenter)
        self.notice.setTextFormat(Qt.PlainText)
        self.notice.setMinimumHeight(34)
        self.notice.setOpenExternalLinks(False)
        self.notice.linkActivated.connect(self.play_easter_egg)
        self.set_notice(bottom_notice)
        layout.addWidget(self.notice)

        footer = QHBoxLayout()
        footer.addStretch()
        for text, action in (("检查更新", lambda: self.launch("update")),
                             ("修复程序", lambda: self.launch("repair")), ("查看日志", self.open_logs)):
            button = QPushButton(text)
            button.setProperty("quiet", True)
            button.clicked.connect(action)
            footer.addWidget(button)
        layout.addLayout(footer)

        self.refresh_buttons()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_processes)
        self.timer.start(750)

        self.watermark_overlay = WatermarkOverlay(self)
        self.watermark_overlay.setGeometry(self.rect())
        self.watermark_overlay.raise_()
        self.watermark_overlay.show()

    def animate_watermark(self, value) -> None:
        self.fade = float(value)
        self.watermark_overlay.update()

    def select_mode(self, key: str) -> None:
        if key != self.selected_mode:
            self.transition.stop()
            self.watermarks[key] = QPixmap(str(self.root / "launcher/assets" / random.choice(self.watermark_files[key])))
            self.previous_mode = self.selected_mode
            self.selected_mode = key
            self.settings.setValue("lastMode", key)
            self.transition.start()
        self.refresh_buttons()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor("#E9F4FD"))
        gradient.setColorAt(0.55, QColor("#F4F8FD"))
        gradient.setColorAt(1.0, QColor("#FBF7FA"))
        painter.fillRect(self.rect(), gradient)
        # 内容区背后的柔光：中心略亮、边缘渐隐为全透明，没有任何可见边界。
        card = getattr(self, "card", None)
        if card is not None and card.isVisible():
            area = card.geometry()
            cx, cy = area.center().x(), area.center().y()
            radius = max(area.width(), 1) * 0.72
            glow = QRadialGradient(0, 0, radius)
            glow.setColorAt(0.0, QColor(255, 255, 255, 170))
            glow.setColorAt(0.6, QColor(255, 255, 255, 90))
            glow.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.save()
            painter.translate(cx, cy)
            painter.scale(1.0, max(area.height(), 1) * 0.9 / radius)
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))
            painter.restore()
        painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "watermark_overlay"):
            self.watermark_overlay.setGeometry(self.rect())

    @staticmethod
    def secondary(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("dialogRole", "secondary")
        label.setWordWrap(True)
        return label

    def set_notice(self, text: str) -> None:
        """只有彩蛋提示是链接，进程状态等普通提示不可点击。"""
        self.notice_is_easter_egg = text == "夢はパワー (≧▽≦)"
        self.notice.setTextFormat(Qt.RichText if self.notice_is_easter_egg else Qt.PlainText)
        self.notice.setTextInteractionFlags(
            Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard
            if self.notice_is_easter_egg else Qt.NoTextInteraction
        )
        self.notice.setCursor(Qt.PointingHandCursor if self.notice_is_easter_egg else Qt.ArrowCursor)
        self.notice.setToolTip("播放阿拉蕾音频" if self.notice_is_easter_egg else "")
        self.notice.setText(
            f'<a href="easter-egg" style="color: #8C96AC; text-decoration: none;">{text}</a>'
            if self.notice_is_easter_egg else text
        )

    def play_easter_egg(self, link: str) -> None:
        if link != "easter-egg" or not self.notice_is_easter_egg:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("彩蛋")
        dialog.setText("即将播放阿拉蕾小动静，注意外放")
        dialog.setIcon(QMessageBox.Information)
        accept = dialog.addButton("好", QMessageBox.AcceptRole)
        cancel = dialog.addButton("取消", QMessageBox.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec_()
        if dialog.clickedButton() is not accept:
            return
        audio_path = self.root / "launcher/assets/夢はパワー_arl.mp3"
        if not audio_path.is_file():
            QMessageBox.warning(self, "彩蛋播放失败", f"找不到音频文件：{audio_path}")
            return
        try:
            from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer

            if self.easter_egg_player is None:
                self.easter_egg_player = QMediaPlayer(self)
                self.easter_egg_player.error.connect(self.easter_egg_error)
            player = self.easter_egg_player
            player.stop()
            player.setMedia(QMediaContent(QUrl.fromLocalFile(str(audio_path))))
            player.play()
        except Exception as exc:
            QMessageBox.warning(self, "彩蛋播放失败", str(exc))

    def easter_egg_error(self, error) -> None:
        if error and self.easter_egg_player is not None:
            QMessageBox.warning(self, "彩蛋播放失败", self.easter_egg_player.errorString() or "音频无法播放。")

    def launch(self, key: str) -> None:
        if key in ("update", "repair"):
            import sys
            path = str(self.root / "GPT_SoVITS")
            if path not in sys.path:
                sys.path.insert(0, path)
            try:
                if key == "repair":
                    from launcher_repair import LauncherRepairDialog
                    dialog = LauncherRepairDialog(self.root, self.processes, self)
                else:
                    from launcher_update import LauncherUpdateDialog
                    dialog = LauncherUpdateDialog(self.root, self.processes, self)
            except Exception as exc:
                QMessageBox.warning(self, "修复模块无法打开" if key == "repair" else "更新模块无法打开", str(exc))
                return
            dialog.exec_()
            return
        try:
            self.processes.start(key)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "启动失败", str(exc))
            return
        self.refresh_buttons()
        self.set_notice(f"已启动{ENTRY_BY_KEY[key].title}。首次加载可能需要一些时间。")
        self.showMinimized()

    def activate_on_startup(self) -> None:
        """首次显示后将启动器带到前台，不保留置顶状态或持续抢焦点。"""
        self.raise_()
        self.activateWindow()
        import sys
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, wintypes.UINT]
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            hwnd = int(self.winId())
            # 批处理的终端可能仍拥有前台权；临时调整 Z 序后立即取消置顶。
            flags = 0x0001 | 0x0002  # SWP_NOSIZE | SWP_NOMOVE
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)
            user32.SetForegroundWindow(hwnd)

    def poll_processes(self) -> None:
        finished = self.processes.finished()
        for key, code, log_path in finished:
            if code:
                self.set_notice(f"{ENTRY_BY_KEY[key].title}已结束（非零退出码：{code} / "
                                f"0x{code & 0xFFFFFFFF:08X}），可以再次启动。"
                                "若非主动结束，请检查程序日志；退出码已记录在启动日志中。")
            else:
                self.set_notice(f"{ENTRY_BY_KEY[key].title}已关闭")
        self.refresh_buttons()
        if finished:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def refresh_buttons(self) -> None:
        for key, button in self.mode_buttons.items():
            item = self.processes.running.get(key)
            running = item is not None and item.process.poll() is None
            button.setText(MODES[key][0].replace("&", "&&") + (" · 运行中" if running else ""))
            button.setChecked(key == self.selected_mode)
            button.setToolTip(self.processes.blocked_reason(key) or MODES[key][1])
        name, description, launch_text = MODES[self.selected_mode]
        self.mode_title.setText(name)
        self.description.setText(description)
        reason = self.processes.blocked_reason(self.selected_mode)
        item = self.processes.running.get(self.selected_mode)
        running = item is not None and item.process.poll() is None
        self.launch_button.setEnabled(reason is None)
        self.launch_button.setText("进程已启动" if running else launch_text)
        self.launch_button.setToolTip(reason or "在独立终端中启动")
        self.status.setText("进程运行中 · 首次加载可能需要一些时间" if running else reason or "")
        for key, button in self.buttons.items():
            reason = self.processes.blocked_reason(key)
            button.setEnabled(reason is None)
            button.setToolTip(reason or ENTRY_BY_KEY[key].description)

    def open_logs(self) -> None:
        path = self.root / "logs/launcher"
        try:
            path.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise OSError(f"请在文件管理器中打开：{path}")
        except OSError as exc:
            QMessageBox.warning(self, "打开日志目录失败", str(exc))


def run(root: Path) -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication([])
    app.setWindowIcon(QIcon(str(root / "launcher/assets/logo_thumb.png")))
    font_id = QFontDatabase.addApplicationFont(str(preferred_font(root)))
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    app.setFont(QFont(families[0] if families else "Microsoft YaHei", 10))
    window = LauncherWindow(root)
    screen = app.primaryScreen()
    if screen:
        area = screen.availableGeometry()
        window.resize(min(760, area.width() - 40), min(600, area.height() - 60))
        window.move(area.center() - window.rect().center())
    window.show()
    QTimer.singleShot(0, window.activate_on_startup)
    return app.exec_()
