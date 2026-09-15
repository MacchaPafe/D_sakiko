"""轻量角色主题启动菜单；只负责入口与进程状态。"""
from __future__ import annotations

from pathlib import Path
import random

from PyQt5.QtCore import Qt, QTimer, QUrl, QVariantAnimation, QEasingCurve, QRectF, QSettings
from PyQt5.QtGui import QDesktopServices, QFont, QFontDatabase, QIcon, QPainter, QPixmap, QColor
from PyQt5.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from launcher_actions import ENTRY_BY_KEY, LauncherProcesses, preferred_font

MODES = {
    "desktop": ("桌面端", "日常小助手", "启动桌面端"),
    "webui": ("WebUI", "全新界面 · 通过局域网在手机或平板上运行数字小祥", "启动 WebUI"),
    "theater": ("小剧场模式", "自由编排两名角色的互动剧情", "启动小剧场"),
}

EASTER_EGG_NOTICE = "夢はパワー (≧▽≦)"


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
        self.setMinimumSize(680, 520)
        self.resize(760, 560)
        self.setWindowIcon(QIcon(str(self.root / "live2d_related/sakiko/sakiko_icon.png")))

        # 二次元客户端专属质感 QSS
        self.setStyleSheet("""
            QDialog {
                background: #F7F8FC;
            }
            QLabel {
                background: transparent;
            }

            /* 主标题：精致日系衬线体风韵，深冷紫星夜色彩 */
            QLabel#heading {
                font-size: 30px;
                font-weight: 700;
                color: #384260;
                letter-spacing: 2px;
            }
            QLabel#latin {
                font-size: 13px;
                font-weight: 600;
                letter-spacing: 4px;
                color: #8C96AE;
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

            /* 顶部模式切换按钮：日系微光胶囊 */
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
                border: 1.5px solid #5B6B9E;
            }

            /* 核心启动大按钮：冷夜紫金微光 */
            QPushButton#launch {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #6775A8, stop:1 #525E8C);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.4);
                border-radius: 16px;
                font-size: 16.5px;
                font-weight: 700;
                letter-spacing: 1.5px;
                padding: 15px 36px;
            }
            QPushButton#launch:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #7483BA, stop:1 #5C699C);
                border: 1px solid rgba(255, 255, 255, 0.8);
            }
            QPushButton#launch:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4A5580, stop:1 #3F486E);
                padding-top: 17px;
                padding-bottom: 13px;
            }
            QPushButton#launch:disabled {
                background: #E2E6EF;
                color: #9BA4B8;
                border: none;
            }

            /* 底部工具操作按钮：轻质感亚克力卡片 */
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
        """)

        # 完全保留原本的布局顺序与间距参数
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 22, 32, 12)
        layout.setSpacing(8)

        title = QLabel("数字小祥启动器")
        title.setObjectName("heading")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        latin = QLabel("DSakiko")
        latin.setObjectName("latin")
        latin.setAlignment(Qt.AlignCenter)
        layout.addWidget(latin)
        layout.addSpacing(24)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        mode_row.addStretch()
        for key, (name, _, _) in MODES.items():
            button = QPushButton(name)
            button.setCheckable(True)
            button.setProperty("mode", True)
            button.setAccessibleName(f"选择{name}")
            button.clicked.connect(lambda checked=False, key=key: self.select_mode(key))
            self.mode_buttons[key] = button
            mode_row.addWidget(button)
        mode_row.addStretch()
        layout.addLayout(mode_row)
        layout.addSpacing(6)

        self.mode_title = QLabel()
        self.mode_title.setObjectName("modeTitle")
        self.mode_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.mode_title)

        self.description = QLabel()
        self.description.setObjectName("description")
        self.description.setAlignment(Qt.AlignCenter)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        layout.addSpacing(8)

        self.launch_button = QPushButton()
        self.launch_button.setObjectName("launch")
        self.launch_button.setMinimumWidth(260)
        self.launch_button.clicked.connect(lambda: self.launch(self.selected_mode))
        layout.addWidget(self.launch_button, 0, Qt.AlignHCenter)

        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(42)
        layout.addWidget(self.status)
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

        bottom_notice: str = "" if random.random() < 0.6 else EASTER_EGG_NOTICE
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
        painter.fillRect(self.rect(), QColor("#F7F8FC"))
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
        self.notice_is_easter_egg = text == EASTER_EGG_NOTICE
        self.notice.setTextFormat(Qt.RichText if self.notice_is_easter_egg else Qt.PlainText)
        self.notice.setTextInteractionFlags(
            Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard
            if self.notice_is_easter_egg else Qt.NoTextInteraction
        )
        self.notice.setCursor(Qt.PointingHandCursor if self.notice_is_easter_egg else Qt.ArrowCursor)
        self.notice.setToolTip("点击播放彩蛋音频" if self.notice_is_easter_egg else "")
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
            button.setText(MODES[key][0] + (" · 运行中" if running else ""))
            button.setChecked(key == self.selected_mode)
            button.setToolTip(self.processes.blocked_reason(key) or MODES[key][1])
        name, description, launch_text = MODES[self.selected_mode]
        self.mode_title.setText(name)
        self.description.setText(description)
        reason = self.processes.blocked_reason(self.selected_mode)
        item = self.processes.running.get(self.selected_mode)
        running = item is not None and item.process.poll() is None
        self.launch_button.setEnabled(reason is None)
        self.launch_button.setText("进程已启动" if running else launch_text + "  →")
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
    font_id = QFontDatabase.addApplicationFont(str(preferred_font(root)))
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    app.setFont(QFont(families[0] if families else "Microsoft YaHei", 10))
    window = LauncherWindow(root)
    screen = app.primaryScreen()
    if screen:
        area = screen.availableGeometry()
        window.resize(min(760, area.width() - 40), min(560, area.height() - 60))
        window.move(area.center() - window.rect().center())
    window.show()
    return app.exec_()
