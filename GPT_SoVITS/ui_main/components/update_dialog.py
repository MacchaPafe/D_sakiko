from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from update.update_models import UpdatePlan


def format_size(size: int) -> str:
    """将字节数格式化为易读文本。"""

    value = float(size)
    units = ["B", "KB", "MB", "GB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def format_speed(speed: float) -> str:
    """将下载速度格式化为易读文本。"""

    if speed <= 0:
        return ""
    return f"{format_size(int(speed))}/s"


class UpdateDialog(QDialog):
    """展示更新公告、下载进度和安装入口。"""

    installRequested = pyqtSignal()
    downloadRequested = pyqtSignal()
    cancelRequested = pyqtSignal()

    def __init__(self, update_plan: UpdatePlan, parent: object | None = None) -> None:
        """根据更新计划初始化弹窗。"""

        super().__init__(parent)
        self.update_plan = update_plan
        self.setWindowTitle("发现新版本")
        self.resize(560, 520)

        layout = QVBoxLayout(self)
        title_text = f"发现新版本 {update_plan.target_version}"
        if update_plan.critical:
            title_text += "（重要更新）"
        self.title_label = QLabel(title_text)
        self.title_label.setAlignment(Qt.AlignLeft)
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(self.title_label)

        self.meta_label = QLabel(
            f"当前版本 {update_plan.current_version}，补丁 {len(update_plan.patches)} 个，"
            f"约 {format_size(update_plan.total_size)}"
        )
        layout.addWidget(self.meta_label)

        self.notes_browser = QTextBrowser()
        self.notes_browser.setOpenExternalLinks(True)
        self.notes_browser.setText(self._build_initial_notes())
        layout.addWidget(self.notes_browser, 1)

        self.status_label = QLabel("准备下载")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, max(update_plan.total_size, 1))
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        button_layout = QHBoxLayout()
        self.download_button = QPushButton("下载")
        self.install_button = QPushButton("立即更新")
        self.later_button = QPushButton("稍后")
        self.cancel_button = QPushButton("取消")
        self.install_button.setEnabled(False)
        self.download_button.clicked.connect(self.downloadRequested.emit)  # noqa
        self.install_button.clicked.connect(self.installRequested.emit)  # noqa
        self.later_button.clicked.connect(self.reject)  # noqa
        self.cancel_button.clicked.connect(self.cancelRequested.emit)  # noqa
        button_layout.addStretch(1)
        button_layout.addWidget(self.download_button)
        button_layout.addWidget(self.install_button)
        button_layout.addWidget(self.later_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

    def _build_initial_notes(self) -> str:
        """构造打开弹窗时的摘要文本。"""

        lines: list[str] = []
        for release in self.update_plan.releases:
            prefix = "【重要】" if release.critical else ""
            lines.append(f"{prefix}{release.title}")
            if release.summary:
                lines.append(release.summary)
            lines.append("")
        return "\n".join(lines).strip() or "暂无更新说明。"

    def set_release_notes(self, markdown: str, source_name: str) -> None:
        """展示完整 release note。"""

        if hasattr(self.notes_browser, "setMarkdown"):
            self.notes_browser.setMarkdown(markdown)
        else:
            self.notes_browser.setPlainText(markdown)
        self.status_label.setText(f"已加载更新公告：{source_name}")

    def set_download_progress(self, downloaded: int, total: int, speed: float) -> None:
        """更新下载进度。"""

        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(min(downloaded, max(total, 1)))
        speed_text = format_speed(speed)
        suffix = f"，{speed_text}" if speed_text else ""
        self.status_label.setText(f"已下载 {format_size(downloaded)} / {format_size(total)}{suffix}")

    def set_status(self, text: str) -> None:
        """更新状态文本。"""

        self.status_label.setText(text)

    def set_error(self, message: str) -> None:
        """展示错误状态并恢复下载按钮。"""

        self.status_label.setText(message)
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def set_ready_to_install(self) -> None:
        """切换到可安装状态。"""

        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.install_button.setEnabled(True)
        self.status_label.setText("补丁已准备完成，可以开始更新。")
