# 配置自定义的个性化设置参数的相关 UI
import contextlib
import glob
import os
import shutil
import time

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QVBoxLayout, QFileDialog

import character
from qconfig import d_sakiko_config

from ..custom_widgets.character_setting_card import CharacterSettingCard

with contextlib.redirect_stdout(None):
    from qfluentwidgets import SettingCardGroup, ComboBoxSettingCard, FluentIcon, \
    InfoBarIcon, PushSettingCard, setTheme

from ..custom_widgets.transparent_scroll_area import TransparentScrollArea
from ..custom_widgets.custom_color_setting_card import CustomColorSettingCard


class CustomSettingArea(TransparentScrollArea):
    # 发送通知信息的信号
    status_signal = pyqtSignal(InfoBarIcon, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.v_box_layout = QVBoxLayout(self.view)

        self.personal_group = SettingCardGroup(self.tr("个性化"), self.view)
        self.personal_group.titleLabel.setVisible(False)

        # 设置角色登场顺序
        self.character_setting_card = CharacterSettingCard(icon=FluentIcon.PEOPLE,
                                                           title=self.tr("角色登场顺序"),
                                                           content=self.tr("拖动修改角色登场顺序"),
                                                           parent=self.personal_group)
        # 自定义主题颜色
        self.font_card = PushSettingCard(
            self.tr("选择..."),
            FluentIcon.FONT,
            self.tr("自定义字体"),
            self.tr("在应用中选择自定义字体文件"),
            parent=self.personal_group
        )
        self.theme_card = ComboBoxSettingCard(
            d_sakiko_config.themeMode,
            FluentIcon.BRUSH,
            self.tr("应用主题"),
            self.tr("调整应用程序的外观"),
            texts=[self.tr("浅色"), self.tr("深色"), self.tr("自动")],
            parent=self.personal_group,
        )
        self.theme_color_card = CustomColorSettingCard(
            FluentIcon.PALETTE,
            self.tr('主题颜色'),
            self.tr('选择设置界面的主题色'),
            self.personal_group,
        )

        self.font_card.clicked.connect(self.user_select_font_file)

        self.personal_group.addSettingCard(self.character_setting_card)
        self.personal_group.addSettingCard(self.font_card)
        self.personal_group.addSettingCard(self.theme_card)
        self.personal_group.addSettingCard(self.theme_color_card)

        self.v_box_layout.addWidget(self.personal_group)

        self.theme_card.comboBox.currentIndexChanged.connect(lambda: setTheme(d_sakiko_config.get(d_sakiko_config.themeMode), lazy=True))

    def load_config_to_ui(self):
        """
        从 d_sakiko_config 中加载设置到 UI 上。
        """
        self.character_setting_card.load_config_to_ui()

    def save_ui_to_config(self) -> bool:
        """
        将 UI 上的设置保存到 d_sakiko_config 中。
        """
        return self.character_setting_card.save_ui_to_config()

    def user_select_font_file(self):
        file_path, file_type = QFileDialog.getOpenFileName(
            self,
            "选择字体文件（.ttf/.otf/.ttc）",
            "",
            "字体类型文件 (*.ttf *.otf *.ttc)"
        )
        if not file_path:
            return

        try:
            # 1. 生成带时间戳的唯一新文件名
            timestamp = int(time.time())
            file_ext = os.path.splitext(file_path)[1].lower()
            new_filename = f"custom_font_{timestamp}{file_ext}"
            dest_path = os.path.join('../font/', new_filename)

            # 2. 先尝试清理旧文件（尽力而为，删不掉也不报错）
            # 查找所有名字是 custom_font_ 开头的文件
            old_files = glob.glob(os.path.join('../font/', 'custom_font_*'))
            for old_file in old_files:
                try:
                    os.remove(old_file)
                    print(f"已清理旧文件: {old_file}")
                except OSError:
                    # 关键点：如果旧文件被锁，直接跳过，不要抛出异常打断流程
                    print(f"旧文件被占用，本次跳过删除: {old_file}")

            # 3. 复制新文件 (因为名字是唯一的，绝对不会冲突)
            shutil.copy(file_path, dest_path)
            self.status_signal.emit(InfoBarIcon.SUCCESS, self.tr("成功应用字体"))

        except Exception as e:
            self.status_signal.emit(InfoBarIcon.ERROR, "字体应用失败")
            print('错误信息：', e)
