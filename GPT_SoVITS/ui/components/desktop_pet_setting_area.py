"""桌宠启动与后台资源设置。"""

from __future__ import annotations

from PyQt5.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, SettingCardGroup

from qconfig import d_sakiko_config
from ..custom_widgets.custom_switch_setting_card import SwitchSettingCard
from ..custom_widgets.transparent_scroll_area import TransparentScrollArea


class DesktopPetSettingArea(TransparentScrollArea):
    """以独立设置页管理桌宠偏好，沿用现有卡片的持久化行为。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建启动形态和空闲释放开关。"""
        super().__init__(parent)
        self.v_box_layout = QVBoxLayout(self.view)
        self.pet_setting_group = SettingCardGroup(self.tr("桌宠设置"), self.view)
        self.pet_setting_group.titleLabel.setVisible(False)

        self.start_in_pet_mode_card = SwitchSettingCard(
            FluentIcon.HOME,
            self.tr("启动时进入桌宠形态"),
            self.tr("下次启动时直接显示桌宠"),
            d_sakiko_config.start_in_pet_mode,
            parent=self.pet_setting_group,
        )
        self.idle_voice_unload_card = SwitchSettingCard(
            FluentIcon.SYNC,
            self.tr("空闲时释放语音模型"),
            self.tr("长时间未对话时，释放语音模型以节约资源占用"),
            d_sakiko_config.unload_voice_models_when_pet_idle,
            parent=self.pet_setting_group,
        )
        self.pet_setting_group.addSettingCard(self.start_in_pet_mode_card)
        self.pet_setting_group.addSettingCard(self.idle_voice_unload_card)
        self.v_box_layout.addWidget(self.pet_setting_group)
        self.v_box_layout.addStretch(1)
        self.setMinimumWidth(500)

    def load_config_to_ui(self) -> None:
        """配置卡片通过配置项的变更信号自动同步显示。"""

    def save_ui_to_config(self) -> bool:
        """开关修改已即时保存，保持与设置容器一致的保存接口。"""
        return True
