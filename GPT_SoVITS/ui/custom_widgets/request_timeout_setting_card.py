"""支持键盘输入秒数的请求超时设置卡。"""
from PyQt5.QtCore import QSignalBlocker, Qt
from qfluentwidgets import SettingCard, SpinBox, FluentIcon

from llm_request_settings import MAX_REQUEST_TIMEOUT
from qconfig import d_sakiko_config


class RequestTimeoutSettingCard(SettingCard):
    def __init__(self, config_item, title, content, parent=None):
        super().__init__(FluentIcon.HISTORY, title, content, parent)
        self.contentLabel.setWordWrap(True)
        self.setFixedHeight(90)
        self.config_item = config_item
        self.spin_box = SpinBox(self)
        self.spin_box.setRange(1, MAX_REQUEST_TIMEOUT)
        self.spin_box.setSuffix(" 秒")
        self.spin_box.setMinimumWidth(140)
        self.spin_box.setKeyboardTracking(False)
        self.spin_box.setAccessibleName(title)
        self.hBoxLayout.setStretch(2, 1)
        self.hBoxLayout.setStretch(4, 0)
        self.hBoxLayout.addWidget(self.spin_box, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)
        self._sync(config_item.value)
        config_item.valueChanged.connect(self._sync)
        self.spin_box.valueChanged.connect(self._save)

    def _sync(self, value):
        blocker = QSignalBlocker(self.spin_box)
        self.spin_box.setValue(value)
        del blocker

    def _save(self, value):
        d_sakiko_config.set(self.config_item, value)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = max(100, self.width() - self.spin_box.width() - 96)
        self.contentLabel.setFixedWidth(width)
        height = self.contentLabel.heightForWidth(width)
        self.contentLabel.setFixedHeight(height)
        self.setFixedHeight(max(90, self.titleLabel.sizeHint().height() + height + 24))
