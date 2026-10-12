"""支持键盘输入秒数的请求超时设置卡。"""
from __future__ import annotations

from PyQt5.QtCore import QSignalBlocker, Qt
from PyQt5.QtWidgets import QWidget
from qfluentwidgets import ConfigItem, SettingCard, SpinBox, FluentIcon

from llm_request_settings import MAX_REQUEST_TIMEOUT
from qconfig import d_sakiko_config
from .setting_card_layout import configure_wrapping_setting_card


class RequestTimeoutSettingCard(SettingCard):
    """通过数字输入框编辑请求超时，并自适应说明文字的高度。"""

    def __init__(
        self, config_item: ConfigItem, title: str, content: str,
        parent: QWidget | None = None,
    ) -> None:
        """创建秒数输入控件并绑定配置。"""
        super().__init__(FluentIcon.HISTORY, title, content, parent)
        self.config_item = config_item
        self.spin_box = SpinBox(self)
        self.spin_box.setRange(1, MAX_REQUEST_TIMEOUT)
        self.spin_box.setSuffix(" 秒")
        self.spin_box.setMinimumWidth(140)
        self.spin_box.setKeyboardTracking(False)
        self.spin_box.setAccessibleName(title)
        self.hBoxLayout.addWidget(self.spin_box, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)
        configure_wrapping_setting_card(self)
        self._sync(config_item.value)
        config_item.valueChanged.connect(self._sync)
        self.spin_box.valueChanged.connect(self._save)

    def _sync(self, value: int) -> None:
        """同步外部配置，避免触发重复保存。"""
        blocker = QSignalBlocker(self.spin_box)
        self.spin_box.setValue(value)
        del blocker

    def _save(self, value: int) -> None:
        """保存用户输入的超时秒数。"""
        d_sakiko_config.set(self.config_item, value)
