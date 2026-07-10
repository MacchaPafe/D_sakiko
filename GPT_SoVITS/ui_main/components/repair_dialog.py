from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from repair.repair_checker import RepairCheckResult
from ui_main.components.update_dialog import format_size


class RepairDialog(QDialog):
    """展示偏离文件、覆盖警告、下载进度和修复确认。"""

    prepareRequested = pyqtSignal()
    cancelRequested = pyqtSignal()

    def __init__(self, check_result: RepairCheckResult, parent: object | None = None) -> None:
        """根据完整性检查结果构造确认弹窗。"""

        super().__init__(parent)
        self.check_result = check_result
        self._preparing = False
        self.setWindowTitle("发现需要修复的程序文件")
        self.resize(600, 430)

        layout = QVBoxLayout(self)
        summary = QLabel(
            f"发现 {len(check_result.candidates)} 个文件缺失或内容不一致，"
            f"需要下载约 {format_size(check_result.total_download_size)}。"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        warning = QLabel(
            "将恢复为当前版本的官方内容。如果你手动修改过这些文件，修改内容将丢失。"
            "修复前会把当前文件备份到本地。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #9B3A30; font-weight: 600;")
        layout.addWidget(warning)

        self.details_button = QPushButton("展开详情")
        self.details_button.clicked.connect(self._toggle_details)  # noqa
        layout.addWidget(self.details_button, 0, Qt.AlignLeft)

        self.file_list = QListWidget()
        self.file_list.setVisible(False)
        for candidate in check_result.candidates:
            self.file_list.addItem(candidate.entry.path)
        layout.addWidget(self.file_list, 1)

        self.status_label = QLabel("准备修复")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, max(check_result.total_download_size, 1))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.primary_button = QPushButton("备份并修复")
        self.later_button = QPushButton("暂不修复")
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setVisible(False)
        self.primary_button.clicked.connect(self.prepareRequested.emit)  # noqa
        self.later_button.clicked.connect(self.reject)  # noqa
        self.cancel_button.clicked.connect(self.cancelRequested.emit)  # noqa
        buttons.addWidget(self.primary_button)
        buttons.addWidget(self.later_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def _toggle_details(self) -> None:
        """展开或收起不分类的文件路径列表。"""

        visible = not self.file_list.isVisible()
        self.file_list.setVisible(visible)
        self.details_button.setText("收起详情" if visible else "展开详情")

    def set_preparing(self) -> None:
        """切换到下载与校验状态。"""

        self._preparing = True
        self.primary_button.setEnabled(False)
        self.primary_button.setText("正在准备...")
        self.later_button.setVisible(False)
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.status_label.setText("正在下载并校验修复文件，此阶段可以取消。")

    def set_progress(self, completed: int, total: int, path: str) -> None:
        """刷新下载总进度和当前文件。"""

        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(min(completed, max(total, 1)))
        self.status_label.setText(
            f"已准备 {format_size(completed)} / {format_size(total)}\n{path}"
        )

    def set_status(self, message: str) -> None:
        """更新当前准备阶段状态文本。"""

        self.status_label.setText(message)

    def set_error(self, message: str) -> None:
        """显示准备失败并允许用户重试或关闭。"""

        self._preparing = False
        self.status_label.setText(message)
        self.primary_button.setText("重试备份并修复")
        self.primary_button.setEnabled(True)
        self.later_button.setVisible(True)
        self.cancel_button.setVisible(False)

    def set_cancelled(self) -> None:
        """显示已取消状态并恢复确认按钮。"""

        self._preparing = False
        self.status_label.setText("已取消，尚未修改任何程序文件。")
        self.primary_button.setText("备份并修复")
        self.primary_button.setEnabled(True)
        self.later_button.setVisible(True)
        self.cancel_button.setVisible(False)

    def set_launching(self) -> None:
        """切换到不可取消的独立修复启动状态。"""

        self.primary_button.setEnabled(False)
        self.primary_button.setText("正在启动修复...")
        self.cancel_button.setEnabled(False)
        self.status_label.setText("修复文件已全部校验，正在关闭主程序并应用修复。")

    def reject(self) -> None:
        """准备阶段关闭窗口时先请求取消，不隐藏仍在工作的流程。"""

        if self._preparing:
            self.cancelRequested.emit()
            return
        super().reject()
