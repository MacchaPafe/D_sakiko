# 此页面包含一个简单的用户卡片，可以快速展示当前用户人设信息，并提供打开设置界面的选项。
from PyQt5 import QtGui
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget, QApplication
from qfluentwidgets import CardWidget, AvatarWidget, BodyLabel, SubtitleLabel, FluentIcon, RoundMenu, Action

import sys

sys.path.insert(0, "../..")

from character import Character


class UserCharacterCard(QWidget):
    """
    一张用户人设信息卡片，展示当前用户的人设头像和名称，并提供打开设置界面的选项。
    """
    def __init__(self, user_character: Character, parent=None):
        """
        创建一个用户信息显示卡片。
        """
        super().__init__(parent)
        self.user_character = user_character

        self.hBoxLayout = QHBoxLayout(self)

        self.avatar_widget = AvatarWidget(self)
        self.avatar_widget.setRadius(24)
        self.hBoxLayout.addWidget(self.avatar_widget)

        self.vBoxLayout = QVBoxLayout()
        self.vBoxLayout.setSpacing(5)

        self.user_name_label = SubtitleLabel(self)
        self.user_description_label = BodyLabel(self)

        self.vBoxLayout.addWidget(self.user_name_label, alignment=Qt.AlignmentFlag.AlignLeft)
        self.vBoxLayout.addWidget(self.user_description_label, alignment=Qt.AlignmentFlag.AlignLeft)

        self.hBoxLayout.addSpacing(20)
        self.hBoxLayout.addLayout(self.vBoxLayout, stretch=1)

        self.setFixedHeight(82)
        self.setMinimumWidth(302)

        # 加载当前用户人设信息
        self.load_character()

    def load_character(self, character: Character = None):
        """
        加载并显示用户人设信息。
        """
        if character is None:
            character = self.user_character

        # 如果存在头像路径则加载头像，否则使用默认头像
        if character.icon_path:
            q_image = QImage(character.icon_path)
            if not q_image.isNull():
                self.avatar_widget.setImage(q_image)
            else:
                self.avatar_widget.setImage(FluentIcon.PEOPLE.path())
        else:
            self.avatar_widget.setImage(FluentIcon.PEOPLE.path())

        self.user_name_label.setText(character.character_name)
        # 如果描述过长则截断到 50 个字符（实际上大概率长于 50 字符）
        description = character.character_description
        if len(description) > 50:
            description = description[:50] + "..."
        if not description:
            description = self.tr("尚无个人描述...")
        self.user_description_label.setText(description)


class UserCharacterWidget(QWidget):
    """
    一个主页上的小组件，显示当前用户的人设头像，点击后可以打开人设卡片和菜单
    """
    # 要求打开用户人设设置界面的信号
    setting_requested = pyqtSignal()

    def __init__(self, character: Character, radius=16, parent=None):
        """
        创建一个用户人设头像组件。
        """
        super().__init__(parent)
        self.character = character

        self.vBoxLayout = QVBoxLayout(self)

        self.avatar_widget = AvatarWidget(self)
        self.avatar_widget.setRadius(radius)

        self.vBoxLayout.addWidget(self.avatar_widget)

        self.open_setting_action = Action(FluentIcon.SETTING, self.tr("设置用户人设..."), self)
        self.open_setting_action.triggered.connect(self.open_setting)

        self.load_character()

    @pyqtSlot()
    def open_setting(self):
        """
        打开用户人设设置界面
        """
        self.setting_requested.emit()

    def load_character(self, character: Character = None):
        if character is None:
            character = self.character

        # 加载当前用户人设头像
        if character.icon_path:
            q_image = QImage(character.icon_path)
            if not q_image.isNull():
                self.avatar_widget.setImage(q_image)
            else:
                self.avatar_widget.setImage(FluentIcon.PEOPLE.path())
        else:
            self.avatar_widget.setImage(FluentIcon.PEOPLE.path())

    def mouseReleaseEvent(self, a0: QtGui.QMouseEvent):
        self.open_menu(a0)

    def open_menu(self, e: QtGui.QMouseEvent):
        """
        在点击时，打开用户人设卡片和设置菜单
        """
        menu = RoundMenu(parent=self)

        card = UserCharacterCard(self.character, parent=menu)
        menu.addWidget(card, selectable=False)
        menu.addSeparator()
        menu.addAction(self.open_setting_action)
        menu.exec(e.globalPos())


if __name__ == "__main__":

    app = QApplication(sys.argv)
    widget = UserCharacterWidget(Character.create_user())
    widget.show()
    sys.exit(app.exec_())