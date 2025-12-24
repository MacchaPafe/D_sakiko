# coding:utf-8
from typing import Union
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QIcon, QColor, QPainter, QBrush
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QButtonGroup

from qfluentwidgets import (RadioButton, LineEdit, ToolButton, PushButton,
                            FluentIcon as FIF, ColorDialog, setThemeColor,
                            FluentIconBase, SimpleExpandGroupSettingCard, ToolTipFilter)

from qconfig import d_sakiko_config


class ColorIndicator(QWidget):
    """
    显示一个颜色，用于预览各个主题色
    """
    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.color = color

    def setColor(self, color: QColor):
        self.color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(self.color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, 24, 24)


class ColorItemWidget(QWidget):
    """
    颜色主题项控件
    由单选按钮、颜色预览、名称编辑框、编辑按钮和删除按钮组成
    """
    colorChanged = pyqtSignal(str, str) # name, color_str
    nameChanged = pyqtSignal(str, str) # old_name, new_name
    itemDeleted = pyqtSignal(str) # name
    itemSelected = pyqtSignal(str, str) # name, color_str

    def __init__(self, name: str, color: str, group: QButtonGroup, parent=None):
        super().__init__(parent)
        self.name = name
        self.colorStr = color
        self.color = QColor(color)

        self.h_box_layout = QHBoxLayout(self)
        self.h_box_layout.setContentsMargins(48, 12, 48, 12)
        self.h_box_layout.setSpacing(16)

        self.radioButton = RadioButton(self)
        group.addButton(self.radioButton)

        self.colorIndicator = ColorIndicator(self.color, self)

        self.nameLineEdit = LineEdit(self)
        self.nameLineEdit.setText(name)
        self.nameLineEdit.setFixedWidth(150)
        self.nameLineEdit.setPlaceholderText("主题名称")

        self.editButton = ToolButton(FIF.EDIT, self)
        self.editButton.setToolTip("编辑颜色")
        self.editButton.installEventFilter(ToolTipFilter(self.editButton))

        self.deleteButton = ToolButton(FIF.DELETE, self)
        self.deleteButton.setToolTip("删除")
        self.deleteButton.installEventFilter(ToolTipFilter(self.deleteButton))

        self.h_box_layout.addWidget(self.radioButton)
        self.h_box_layout.addWidget(self.colorIndicator)
        self.h_box_layout.addWidget(self.nameLineEdit)
        self.h_box_layout.addStretch(1)
        self.h_box_layout.addWidget(self.editButton)
        self.h_box_layout.addWidget(self.deleteButton)

        self.editButton.clicked.connect(self._showColorDialog)
        self.deleteButton.clicked.connect(lambda: self.itemDeleted.emit(self.name))
        self.nameLineEdit.editingFinished.connect(self._onNameChanged)
        self.radioButton.toggled.connect(self._onToggled)

    def _showColorDialog(self):
        dlg = ColorDialog(self.color, self.tr("选择颜色"), self.window())
        # 强行汉化
        dlg.yesButton.setText(self.tr('确定'))
        dlg.cancelButton.setText(self.tr('取消'))
        dlg.editLabel.setText(self.tr('编辑颜色'))
        dlg.redLabel.setText(self.tr('红'))
        dlg.greenLabel.setText(self.tr('绿'))
        dlg.blueLabel.setText(self.tr('蓝'))
        dlg.opacityLabel.setText(self.tr('透明度'))
        if dlg.exec():
            new_color = dlg.color
            self.color = new_color
            self.colorStr = new_color.name(QColor.HexArgb)
            self.colorIndicator.setColor(new_color)
            self.colorChanged.emit(self.name, self.colorStr)
            if self.radioButton.isChecked():
                self.itemSelected.emit(self.name, self.colorStr)

    def _onNameChanged(self):
        new_name = self.nameLineEdit.text()
        if new_name != self.name:
            old_name = self.name
            self.name = new_name
            self.nameChanged.emit(old_name, new_name)

    def _onToggled(self, checked):
        if checked:
            self.itemSelected.emit(self.name, self.colorStr)

    def setChecked(self, checked):
        self.radioButton.setChecked(checked)


class CustomColorSettingCard(SimpleExpandGroupSettingCard):
    def __init__(self, icon: Union[str, QIcon, FluentIconBase], title: str,
                 content=None, parent=None):
        super().__init__(icon, title, content, parent=parent)

        self.buttonGroup = QButtonGroup(self)

        # Add Item Button Widget
        self.addItemWidget = QWidget(self.view)
        self.addItemLayout = QHBoxLayout(self.addItemWidget)
        self.addItemLayout.setContentsMargins(48, 12, 48, 12)

        self.addButton = PushButton(self.tr("添加新主题"), self.addItemWidget)
        self.addButton.setIcon(FIF.ADD)
        self.addButton.clicked.connect(self._addNewItem)

        self.addItemLayout.addWidget(self.addButton)
        self.addItemLayout.addStretch(1)

        self.viewLayout.addWidget(self.addItemWidget)

        self.colorWidgets = []
        self._loadItems()

        # Connect to config changes
        d_sakiko_config.themeColor.valueChanged.connect(self._onThemeColorChanged)

    def _adjustViewSize(self):
        """
        手动修改当前 view 的高度以适应内容
        在每次添加/删除主题后，组件的高度就应当发生变化，通过调用此函数改变组件的高度。
        由于添加组件后 self.viewLayout.sizeHint() 可能不会立刻更新，我们改为遍历 viewLayout 的子组件，计算高度和。
        """
        h = 0
        spacing = self.viewLayout.spacing()
        margins = self.viewLayout.contentsMargins()

        for i in range(self.viewLayout.count()):
            item = self.viewLayout.itemAt(i)
            widget = item.widget()
            if widget:
                h += widget.sizeHint().height()

        if self.viewLayout.count() > 1:
            h += spacing * (self.viewLayout.count() - 1)

        h += margins.top() + margins.bottom()

        self.spaceWidget.setFixedHeight(h)

        if self.isExpand:
            self.setFixedHeight(self.card.height() + h)

    def _loadItems(self):
        # Clear existing items
        for w in self.colorWidgets:
            self.viewLayout.removeWidget(w)
            w.deleteLater()

        self.colorWidgets = []

        theme_colors = d_sakiko_config.theme_color.value
        current_color = d_sakiko_config.themeColor.value

        for i, info in enumerate(theme_colors):
            name = info["name"]
            color = info["color"]

            w = ColorItemWidget(name, color, self.buttonGroup, self.view)
            w.colorChanged.connect(self._updateColor)
            w.nameChanged.connect(self._updateName)
            w.itemDeleted.connect(self._deleteItem)
            w.itemSelected.connect(self._selectItem)

            # Check if this is the current color
            if QColor(color).name(QColor.HexArgb) == current_color.name(QColor.HexArgb):
                w.setChecked(True)

            self.viewLayout.insertWidget(i, w)
            self.colorWidgets.append(w)
        
        self._adjustViewSize()

    def _updateColor(self, name, new_color_str):
        colors = d_sakiko_config.theme_color.value
        new_colors = []
        for c in colors:
            if c["name"] == name:
                new_colors.append({"name": name, "color": new_color_str})
            else:
                new_colors.append(c)
        d_sakiko_config.theme_color.value = new_colors

    def _updateName(self, old_name, new_name):
        colors = d_sakiko_config.theme_color.value
        new_colors = []
        for c in colors:
            if c["name"] == old_name:
                new_colors.append({"name": new_name, "color": c["color"]})
            else:
                new_colors.append(c)
        d_sakiko_config.theme_color.value = new_colors

    def _deleteItem(self, name):
        colors = d_sakiko_config.theme_color.value
        new_colors = [c for c in colors if c["name"] != name]
        d_sakiko_config.theme_color.value = new_colors
        self._loadItems()

    def _selectItem(self, name, color_str):
        d_sakiko_config.themeColor.value = QColor(color_str)

    def _addNewItem(self):
        new_name = "新主题"
        new_color = "#009faa"

        existing_names = [c["name"] for c in d_sakiko_config.theme_color.value]
        i = 1
        while new_name in existing_names:
            new_name = f"新主题 {i}"
            i += 1

        colors = d_sakiko_config.theme_color.value
        new_colors = list(colors)
        new_colors.append({"name": new_name, "color": new_color})
        d_sakiko_config.theme_color.value = new_colors
        self._loadItems()

    def _onThemeColorChanged(self, color):
        setThemeColor(color, lazy=True)
        for w in self.colorWidgets:
            if w.color.name(QColor.HexArgb) == color.name(QColor.HexArgb):
                w.setChecked(True)
