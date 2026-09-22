"""简洁的反馈弹窗与仅含标题和撤回按钮的历史列表。"""

from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QThread, Qt
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from feedback.client import FeedbackClient, Receipt, ReceiptStore, configured_endpoint, recipient_notice
from ui_main.theme import ThemePalette, build_dialog_theme_stylesheet


class NetworkJob(QThread):
    """后台执行网络操作，只传递已过滤的错误提示。"""

    def __init__(self, operation: Callable[[], object], parent: QWidget) -> None:
        """保存操作，生命周期由弹窗持有。"""
        super().__init__(parent)
        self.operation = operation
        self.result: object = None
        self.error = ""

    def run(self) -> None:
        """执行操作，避免异常中的原始请求出现在界面或日志。"""
        try:
            self.result = self.operation()
        except ValueError as exc:
            self.error = str(exc)
        except Exception:
            self.error = "操作未完成，请检查网络或系统凭据库后重试。"


class FeedbackDialog(QDialog):
    """只展示评价、文字及上传范围，提交后保留原正文供重试。"""

    def __init__(self, *, title: str, disclosure: str, freeze: Callable[[str, str], tuple[str, bytes]],
                 palette: ThemePalette, store: ReceiptStore, parent: QWidget | None = None,
                 rating: str = "none") -> None:
        """建立弹窗；构造和取消都不会上传或生成回执。"""
        super().__init__(parent)
        self.setWindowTitle("对话反馈" if title != "意见建议" else title)
        self.resize(480, 390)
        self.setMinimumWidth(480)
        self.setStyleSheet(build_dialog_theme_stylesheet(palette))
        self.title = title
        self.freeze = freeze
        self.store = store
        self.receipt: Receipt | None = None
        self.body: bytes | None = None
        self.job: NetworkJob | None = None
        layout = QVBoxLayout(self)
        self.rating = QComboBox()
        for label, value in (("不评价", "none"), ("赞", "up"), ("踩", "down")):
            self.rating.addItem(label, value)
        self.rating.setCurrentIndex(max(0, self.rating.findData(rating)))
        layout.addWidget(self.rating)
        self.comment = QPlainTextEdit()
        self.comment.setPlaceholderText("有什么想告诉我们？")
        layout.addWidget(self.comment)
        self.disclosure = QLabel(disclosure)
        self.disclosure.setTextFormat(Qt.PlainText)
        self.disclosure.setWordWrap(True)
        layout.addWidget(self.disclosure)
        self.disclosure.ensurePolished()
        self.disclosure.setMinimumHeight(self.disclosure.heightForWidth(456))
        # privacy_button = QPushButton("上传与隐私说明")
        # privacy_button.setFlat(True)
        # privacy_button.setCheckable(True)
        # layout.addWidget(privacy_button)
        # privacy = QLabel(recipient_notice() + "\nsystem prompt 按提交时的当前设置渲染，可能与历史生成时不同。上传不包含音频、附件或普通日志；"
        #                  "文本中仍可能包含你输入的个人信息。数据用于开发者分析和改进产品，不因提交而授权公开展示或模型训练。"
        #                  "已提交反馈列表和撤回凭据仅保存在本机，删除原对话不会删除回执。开发者手动导出的副本需按同一保留规则清理。")
        # privacy.setTextFormat(Qt.PlainText)
        # privacy.setWordWrap(True)
        # privacy.setVisible(False)
        # privacy_button.toggled.connect(privacy.setVisible)
        # layout.addWidget(privacy)
        # privacy.ensurePolished()
        # privacy.setMinimumHeight(privacy.heightForWidth(456))
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.cancel = QPushButton("取消")
        self.cancel.clicked.connect(self.reject)
        buttons.addWidget(self.cancel)
        self.submit = QPushButton("上传")
        self.submit.clicked.connect(self._submit)
        buttons.addWidget(self.submit)
        layout.addLayout(buttons)

    def _submit(self) -> None:
        """先冻结并保存控制信息，再在后台提交同一份字节。"""
        if self.job is not None and self.job.isRunning():
            return
        try:
            if self.receipt is None:
                endpoint = configured_endpoint()
                request_id, body = self.freeze(str(self.rating.currentData()), self.comment.toPlainText())
                receipt = self.store.add(request_id, self.title, endpoint)
                self.receipt, self.body = receipt, body
            receipt, body = self.receipt, self.body
            assert body is not None
        except ValueError as exc:
            self.status.setText(str(exc))
            self.status.setMinimumHeight(self.status.heightForWidth(456))
            return
        except Exception:
            self.status.setText("无法准备上传或保存撤回凭据，本次没有发送。请检查系统凭据库后重试。")
            self.status.setMinimumHeight(self.status.heightForWidth(456))
            return
        self.rating.setEnabled(False)
        self.comment.setEnabled(False)
        self.submit.setEnabled(False)
        self.cancel.setEnabled(False)
        self.status.setText("正在上传…")
        self.job = NetworkJob(lambda: FeedbackClient(self.store).submit(receipt, body), self)
        self.job.finished.connect(self._finished)
        self.job.start()

    def _finished(self) -> None:
        """失败保留冻结正文，成功释放正文并结束弹窗。"""
        assert self.job is not None
        error = self.job.error
        self.job.deleteLater()
        self.job = None
        self.cancel.setEnabled(True)
        if error:
            self.status.setText(error + "\n重试将发送原来的内容。关闭后仍可从已提交反馈列表撤回。")
            self.status.setMinimumHeight(self.status.heightForWidth(456))
            self.submit.setText("重试原提交")
            self.submit.setEnabled(True)
        else:
            self.body = None
            self.accept()

    def reject(self) -> None:
        """网络运行时保持窗口和线程存活，关闭后释放临时正文。"""
        if self.job is not None and self.job.isRunning():
            return
        self.body = None
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """窗口关闭键遵守同样的后台线程生命周期。"""
        if self.job is not None and self.job.isRunning():
            event.ignore()
        else:
            self.body = None
            super().closeEvent(event)


class FeedbackHistoryDialog(QDialog):
    """展示本机记录的对话名称，不提供正文查看。"""

    def __init__(self, store: ReceiptStore, palette: ThemePalette, parent: QWidget | None = None) -> None:
        """创建全局历史入口，独立于原对话是否存在。"""
        super().__init__(parent)
        self.store = store
        self.jobs: list[NetworkJob] = []
        self.setWindowTitle("已提交反馈")
        self.resize(460, 360)
        self.setStyleSheet(build_dialog_theme_stylesheet(palette))
        layout = QVBoxLayout(self)
        self.status = QLabel("你可以随时撤回提交的反馈；不过，我们可能已经浏览过你的反馈。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.rows = QVBoxLayout(content)
        receipts = store.all()
        if not receipts:
            self.rows.addWidget(QLabel("暂无提交记录。"))
        for receipt in receipts:
            self._add_row(receipt)
        self.rows.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

    def _add_row(self, receipt: Receipt) -> None:
        """每行只显示提交时名称和撤回按钮。"""
        row = QWidget()
        layout = QHBoxLayout(row)
        title = QLabel(receipt.title)
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title, 1)
        button = QPushButton("撤回")
        button.clicked.connect(lambda: self._withdraw(receipt, row, button))
        layout.addWidget(button)
        self.rows.addWidget(row)

    def _withdraw(self, receipt: Receipt, row: QWidget, button: QPushButton) -> None:
        """撤回失败保留凭据与按钮，确认后才移除本机记录。"""
        button.setEnabled(False)
        job = NetworkJob(lambda: FeedbackClient(self.store).withdraw(receipt), self)
        self.jobs.append(job)
        job.finished.connect(lambda: self._withdraw_finished(job, row, button))
        job.start()

    def _withdraw_finished(self, job: NetworkJob, row: QWidget, button: QPushButton) -> None:
        """回到主线程更新撤回结果。"""
        if job.error:
            self.status.setText(job.error)
            button.setEnabled(True)
        else:
            row.deleteLater()
            self.status.setText("已撤回。")
        self.jobs.remove(job)
        job.deleteLater()

    def reject(self) -> None:
        """等待撤回线程结束后允许关闭。"""
        if not self.jobs:
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """防止关闭窗口销毁仍在运行的线程。"""
        if self.jobs:
            event.ignore()
        else:
            super().closeEvent(event)
