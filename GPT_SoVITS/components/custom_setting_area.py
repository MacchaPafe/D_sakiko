# 配置自定义的个性化设置参数的相关 UI
import contextlib

from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QVBoxLayout, QAbstractItemView, QHBoxLayout, QSizePolicy

from .. import character
from ..qconfig import d_sakiko_config

with contextlib.redirect_stdout(None):
    from qfluentwidgets import BodyLabel, ListWidget, PushButton, SettingCardGroup, ComboBoxSettingCard, FluentIcon

from ..custom_widgets.transparent_scroll_area import TransparentScrollArea
from ..custom_widgets.custom_color_setting_card import CustomColorSettingCard


class CustomSettingArea(TransparentScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self.view)

        # 设置角色登场顺序
        self.characterOrderLabel = BodyLabel(self.tr("调整角色登场顺序：（拖拽调整位置）"), self)
        characters = character.GetCharacterAttributes()
        character_list = characters.character_class_list
        # 读取角色列表
        self.character_names = [char.character_name for char in character_list]
        # 将角色列表放在 listview 中，允许拖拽排序
        self.character_list_widget = ListWidget()
        self.character_list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        self.character_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.character_list_widget.addItems(self.character_names)
        self.vBoxLayout.addWidget(self.characterOrderLabel)
        self.vBoxLayout.addWidget(self.character_list_widget)

        # 自定义字体功能
        self.fontLayout = QHBoxLayout()
        # 设置控件之间的间距，数字越小挨得越近
        self.fontLayout.setSpacing(10)
        self.fontLabel = BodyLabel(self.tr("可更改字体："), self)
        # 让标签也自适应大小，不抢空间
        self.fontLabel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.fontSelectLabel = PushButton(self.tr("选择字体文件"), self)
        # self.fontSelectLabel.clicked.connect(self.user_select_font_file)
        # 【关键点1】设置按钮的大小策略为 Fixed
        # 意思就是：按钮的大小完全由它的内容（文字）决定，绝不拉伸
        self.fontSelectLabel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        # 显示设置状态的标签
        self.fontInfoLabel = BodyLabel(self)
        self.fontLayout.addWidget(self.fontLabel)
        self.fontLayout.addWidget(self.fontSelectLabel)
        self.fontLayout.addWidget(self.fontInfoLabel)
        # 在最后添加一个弹簧
        # 这个弹簧会占据这一行所有剩下的空白区域，把前面三个控件挤到最左边
        self.fontLayout.addStretch(1)
        self.vBoxLayout.addLayout(self.fontLayout)

        # 自定义主题颜色
        self.personalGroup = SettingCardGroup(self.tr("个性化"), self.view)
        self.themeCard = ComboBoxSettingCard(d_sakiko_config.themeMode, FluentIcon.BRUSH, self.tr("应用主题"),
                                             self.tr("调整应用程序的外观"),
                                             texts=[self.tr("浅色"), self.tr("深色"), self.tr("自动")],
                                             parent=self.personalGroup)
        self.themeColorCard = CustomColorSettingCard(
            d_sakiko_config.themeColor,
            FluentIcon.PALETTE,
            self.tr('主题颜色'),
            self.tr('选择应用的主题色'),
            self.personalGroup,
            default_color=QColor("#ff5d74a2")
        )
        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.themeColorCard)

        self.vBoxLayout.addWidget(self.personalGroup)
