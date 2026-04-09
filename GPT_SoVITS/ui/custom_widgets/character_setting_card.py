from PyQt5.QtWidgets import QAbstractItemView
from qfluentwidgets import ListWidget, SimpleExpandGroupSettingCard, ExpandGroupSettingCard

import character
from qconfig import d_sakiko_config


class CharacterSettingCard(ExpandGroupSettingCard):
    def __init__(self, icon, title, content=None, parent=None):
        super().__init__(icon, title, content, parent)

        # 将角色列表放在 listview 中，允许拖拽排序
        self.character_list_widget = ListWidget()
        self.character_list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        self.character_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)

        self.load_config_to_ui()

        self.addGroupWidget(self.character_list_widget)

    def load_config_to_ui(self):
        # 加载角色顺序
        order_data = d_sakiko_config.character_order.value
        if (
                isinstance(order_data, dict)
                and "character_num" in order_data
                and "character_names" in order_data
        ):
            character_names = order_data["character_names"]
            self.character_list_widget.clear()
            self.character_list_widget.addItems(character_names)
            self._adjust_height()

    def _adjust_height(self):
        """ 调整列表高度以适应内容 """
        count = self.character_list_widget.count()
        if count > 0:
            # 38px 是经验值，加上边框
            height = count * 38 + 2 * self.character_list_widget.frameWidth()
            self.character_list_widget.setFixedHeight(height)
        else:
            self.character_list_widget.setFixedHeight(0)

    def save_ui_to_config(self) -> bool:
        # 保存角色顺序
        ordered_names = []
        count = self.character_list_widget.count()
        for i in range(count):
            item = self.character_list_widget.item(i)
            ordered_names.append(item.text())
        order_data_to_save = {
            "character_num": len(ordered_names),
            "character_names": ordered_names,
        }
        d_sakiko_config.character_order.value = order_data_to_save
        return True
