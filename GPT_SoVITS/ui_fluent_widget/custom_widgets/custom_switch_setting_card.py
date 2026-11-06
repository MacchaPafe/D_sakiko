# 总之就是重写了 qfluentwidgets 里面的 SwitchSettingCard，把 UI 文字默认改为中文了

from typing import Union

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtGui import QIcon
from qfluentwidgets import SettingCard, FluentIconBase, ConfigItem, SwitchButton, IndicatorPosition, qconfig

from qconfig import d_sakiko_config


class SwitchSettingCard(SettingCard):
    """ Setting card with switch button """

    checkedChanged = pyqtSignal(bool)

    def __init__(self, icon: Union[str, QIcon, FluentIconBase], title, content=None,
                 configItem: ConfigItem = None, parent=None):
        """
        Parameters
        ----------
        icon: str | QIcon | FluentIconBase
            the icon to be drawn

        title: str
            the title of card

        content: str
            the content of card

        configItem: ConfigItem
            configuration item operated by the card

        parent: QWidget
            parent widget
        """
        super().__init__(icon, title, content, parent)
        self.configItem = configItem
        self.switchButton = SwitchButton(
            self.tr('关'), self, IndicatorPosition.RIGHT)

        if configItem:
            self.setValue(qconfig.get(configItem))
            configItem.valueChanged.connect(self.setValue)

        # add switch button to layout
        self.hBoxLayout.addWidget(self.switchButton, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

        self.switchButton.checkedChanged.connect(self.__onCheckedChanged)

    def __onCheckedChanged(self, isChecked: bool):
        """ switch button checked state changed slot """
        self.setValue(isChecked)
        self.checkedChanged.emit(isChecked)

    def setValue(self, isChecked: bool):
        if self.configItem:
            d_sakiko_config.set(self.configItem, isChecked)

        self.switchButton.setChecked(isChecked)
        self.switchButton.setText(
            self.tr('开') if isChecked else self.tr('关'))
    
    def switchIsEnabled(self):
        """
        获得自身的开关按钮是否启用
        """
        return self.switchButton.isEnabled()
    
    def setSwitchEnabled(self, enabled: bool):
        """
        设置自身开关按钮的启用/禁用状态。禁用整个卡片会影响 eventFilter，导致无法弹出 tooltip。因此，有时可能需要只禁用开关按钮。
        """
        self.switchButton.setEnabled(enabled)

    def setChecked(self, isChecked: bool):
        self.setValue(isChecked)

    def isChecked(self):
        return self.switchButton.isChecked()
