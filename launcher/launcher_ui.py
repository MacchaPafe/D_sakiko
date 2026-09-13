"""复用数字小祥主程序的角色色板、设置弹窗样式与 Fluent 图标。"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices, QFont, QFontDatabase, QIcon
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)
with contextlib.redirect_stdout(None):
    from qfluentwidgets import FluentIcon

from launcher_actions import ENTRIES, ENTRY_BY_KEY, LauncherProcesses, preferred_font
from ui_main.theme import build_dialog_theme_stylesheet, derive_theme_palette, resolve_character_theme_seed


class LauncherWindow(QDialog):
    def __init__(self, root: Path, processes: LauncherProcesses | None = None):
        super().__init__()
        self.root = root.resolve()
        self.processes = processes or LauncherProcesses(self.root)
        self.buttons: dict[str, QPushButton] = {}
        self.status_labels: dict[str, QLabel] = {}
        self.setWindowTitle("数字小祥 · 启动器")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
        self.setMinimumSize(620, 540)
        self.resize(800, 820)
        try:
            seed_text = (self.root / "reference_audio/sakiko/QT_style.json").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            seed_text = None
        self.palette = derive_theme_palette(resolve_character_theme_seed(seed_text))
        p = self.palette
        self.setStyleSheet(build_dialog_theme_stylesheet(p) + f"""
            QScrollArea, QWidget#launcherContent {{ background: transparent; border: none; }}
            QLabel#heading {{ font-size: 26px; font-weight: 600; color: {p.text_accent}; }}
            QLabel[role="title"] {{ font-size: 16px; font-weight: 600; }}
            QLabel[dialogRole="secondary"] {{ font-size: 13px; }}
            QPushButton[primary="true"] {{ background: {p.accent}; color: {p.on_accent}; border-color: {p.accent}; }}
            QPushButton[primary="true"]:hover {{ background: {p.accent_hover}; }}
            QPushButton[primary="true"]:pressed {{ background: {p.accent_pressed}; }}
            QPushButton[primary="true"]:disabled {{ background: {p.surface_selected}; color: {p.text_secondary}; border-color: {p.border_subtle}; }}
            QPushButton:focus {{ border: 2px solid {p.focus_ring}; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 20, 26, 20)
        layout.setSpacing(14)
        header = QHBoxLayout()
        icon_path = self.root / "live2d_related/sakiko/sakiko_icon.png"
        icon = QIcon(str(icon_path)) if icon_path.is_file() else FluentIcon.CHAT.icon(color=p.text_accent)
        self.setWindowIcon(icon)
        avatar = QLabel()
        avatar.setPixmap(icon.pixmap(52, 52))
        header.addWidget(avatar)
        heading = QVBoxLayout()
        title = QLabel("数字小祥")
        title.setObjectName("heading")
        heading.addWidget(title)
        heading.addWidget(self.secondary("选择运行模式，或打开配置与模型工具。"))
        header.addLayout(heading, 1)
        try:
            version_data = json.loads((self.root / "version.json").read_text(encoding="utf-8"))
            version = str(version_data.get("version", ""))
        except (OSError, ValueError, AttributeError):
            version = ""
        header.addWidget(self.secondary(f"v{version}" if version else "启动器"), 0, Qt.AlignTop)
        layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("launcherContent")
        sections = QVBoxLayout(content)
        sections.setContentsMargins(0, 0, 8, 0)
        sections.setSpacing(14)
        for group in dict.fromkeys(entry.group for entry in ENTRIES):
            box = QGroupBox(group)
            rows = QVBoxLayout(box)
            rows.setContentsMargins(16, 24, 16, 12)
            rows.setSpacing(8)
            for entry in (item for item in ENTRIES if item.group == group):
                row = QHBoxLayout()
                row.setSpacing(14)
                symbol = QLabel()
                symbol.setPixmap(getattr(FluentIcon, entry.icon).icon(color=p.text_accent).pixmap(24, 24))
                row.addWidget(symbol)
                labels = QVBoxLayout()
                labels.setSpacing(0)
                name = QLabel(entry.title)
                name.setProperty("role", "title")
                labels.addWidget(name)
                description = self.secondary(entry.description)
                labels.addWidget(description)
                self.status_labels[entry.key] = description
                row.addLayout(labels, 1)
                button = QPushButton("选择更新包" if entry.key == "update" else "打开" if entry.group == "配置与模型" else "启动")
                button.setMinimumWidth(112)
                button.setMinimumHeight(36)
                button.setAccessibleName(f"打开{entry.title}")
                button.setProperty("primary", entry.group == "聊天与演出")
                button.clicked.connect(lambda checked=False, key=entry.key: self.launch(key))
                self.buttons[entry.key] = button
                row.addWidget(button)
                rows.addLayout(row)
            sections.addWidget(box)
        sections.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.notice = self.secondary("关闭启动器后，已打开的程序会继续运行。")
        self.notice.setTextFormat(Qt.PlainText)
        layout.addWidget(self.notice)
        footer = QHBoxLayout()
        logs = QPushButton("查看启动日志")
        logs.clicked.connect(self.open_logs)
        footer.addWidget(logs)
        footer.addStretch()
        close = QPushButton("关闭启动器")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_processes)
        self.timer.start(750)

    @staticmethod
    def secondary(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("dialogRole", "secondary")
        label.setWordWrap(True)
        return label

    def launch(self, key: str) -> None:
        package = None
        if key == "update":
            if any(item.process.poll() is None for item in self.processes.running.values()):
                QMessageBox.information(self, "请先关闭程序", "请先关闭当前启动器打开的程序，再执行更新。")
                return
            folder = QFileDialog.getExistingDirectory(self, "选择已解压的官方更新包", str(self.root))
            if not folder:
                return
            package = Path(folder)
            if not all((package / name).is_file() for name in ("manifest.json", "patch.hdiff")):
                QMessageBox.warning(self, "更新包不完整", "请选择包含 manifest.json 和 patch.hdiff 的已解压更新包目录。")
                return
            if QMessageBox.question(self, "执行更新", "请确认桌面端、WebUI、小剧场及配置工具均已关闭。\n\n"
                                    "启动器将退出并交给原版更新器处理，成功后重新打开启动器。",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        try:
            self.processes.start(key, package=package)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "启动失败", str(exc))
            return
        self.refresh_buttons()
        self.notice.setText(f"已启动{ENTRY_BY_KEY[key].title}。首次加载可能需要一些时间。")
        if key == "update":
            self.close()

    def poll_processes(self) -> None:
        for key, code, log_path in self.processes.finished():
            self.buttons[key].setEnabled(True)
            self.status_labels[key].setText(ENTRY_BY_KEY[key].description)
            if code:
                self.notice.setText(f"{ENTRY_BY_KEY[key].title}已结束（非零退出码：{code} / "
                                    f"0x{code & 0xFFFFFFFF:08X}），可以再次启动。"
                                    "若非主动结束，请检查程序日志；退出码已记录在启动日志中。")
            else:
                self.notice.setText(f"{ENTRY_BY_KEY[key].title}已关闭，可以再次启动。")
        self.refresh_buttons()

    def refresh_buttons(self) -> None:
        for key in self.buttons:
            reason = self.processes.blocked_reason(key)
            self.buttons[key].setEnabled(reason is None)
            self.buttons[key].setToolTip(reason or "")
            item = self.processes.running.get(key)
            if item and item.process.poll() is None:
                text = "已启动，在独立终端中运行。"
            else:
                text = reason or ENTRY_BY_KEY[key].description
            self.status_labels[key].setText(text)

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
    app.setFont(QFont(families[0] if families else "Microsoft YaHei", 11))
    window = LauncherWindow(root)
    screen = app.primaryScreen()
    if screen:
        area = screen.availableGeometry()
        window.resize(min(800, area.width() - 40), min(820, area.height() - 60))
        window.move(area.center() - window.rect().center())
    window.show()
    return app.exec_()
