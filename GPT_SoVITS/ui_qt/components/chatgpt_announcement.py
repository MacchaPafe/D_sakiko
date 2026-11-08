"""主程序首次启动时的标准 Qt 订阅登录提示。"""
from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QShowEvent
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from qconfig import d_sakiko_config


class ChatGPTAnnouncement(QFrame):
    """首次实际展示即记为已读，关闭或下次升级不重复提示。"""

    configure_requested = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        """提供可关闭横幅和直达 Fluent 配置的入口。"""
        super().__init__(parent)
        layout = QHBoxLayout(self)
        label = QLabel("现在可以使用 ChatGPT 登录并使用订阅额度", self)
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        configure = QPushButton("前往配置", self)
        configure.clicked.connect(self.configure_requested.emit)
        layout.addWidget(configure)
        dismiss = QPushButton("关闭", self)
        dismiss.clicked.connect(self.hide)
        layout.addWidget(dismiss)
        self.setVisible(not bool(d_sakiko_config.chatgpt_announcement_seen.value))

    def showEvent(self, event: QShowEvent) -> None:
        """只在窗口实际展示时保存一次标记，存储错误不妨碍主窗口启动。"""
        super().showEvent(event)
        if not d_sakiko_config.chatgpt_announcement_seen.value:
            try:
                d_sakiko_config.set(d_sakiko_config.chatgpt_announcement_seen, True)
            except OSError:
                pass
