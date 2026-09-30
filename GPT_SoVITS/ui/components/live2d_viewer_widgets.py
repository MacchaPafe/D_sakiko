"""演出编辑器的样式、角色选择和首次使用说明。"""

from __future__ import annotations

import sys

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QVBoxLayout, QWidget

from character import CharacterAttributes
from qconfig import DSakikoConfig

# 文案集中在此，调整说明不需要修改配置或行为测试。
V3_INTRO_TITLE = "V3 模型可以分别选择动作和表情"
V3_INTRO_TEXT = (
    "动作组去哪里了？\n\n"
    "Live2D V3 模型不再需要动作组：你可以自由搭配动作与表情。"
)

VIEWER_STYLE = """
QWidget { background: #F3F5F9; color: #263449; font-size: 13px; }
QLabel { background: transparent; }
QLabel#pageTitle { font-size: 23px; font-weight: 600; }
QLabel#sectionTitle { font-size: 15px; font-weight: 600; }
QLabel#muted, QLabel#source { color: #63758B; font-size: 12px; }
QFrame#resourcePanel { background: #FFFFFF; border: 1px solid #DCE3EC; border-radius: 10px; }
QPushButton, QToolButton { background: #FFFFFF; border: 1px solid #DCE3EC; border-radius: 6px; padding: 7px 12px; }
QPushButton:hover, QToolButton:hover { background: #EAF0FA; border-color: #A8BDD9; }
QPushButton:pressed { background: #DCE8F8; }
QPushButton:disabled, QToolButton:disabled { color: #98A4B5; background: #F3F5F9; }
QPushButton#primary { background: #426BAA; color: white; border: none; font-size: 15px; font-weight: 600; padding: 12px 22px; }
QPushButton#primary:hover { background: #355D99; }
QPushButton#primary:disabled { background: #DCE3EC; color: #98A4B5; }
QToolButton#quiet { background: transparent; border: none; padding: 4px 7px; }
QToolButton#quiet::menu-indicator { image: none; width: 0px; height: 0px; }
QListWidget, QTextBrowser, QTextEdit, QLineEdit, QComboBox { background: #FFFFFF; border: 1px solid #DCE3EC; border-radius: 6px; padding: 6px; selection-background-color: #DFE9F8; selection-color: #263449; }
QListWidget { outline: none; }
QListWidget::item { padding: 7px 6px; border-radius: 4px; }
QListWidget::item:selected { background: #DFE9F8; color: #294E87; }
QListWidget::item:hover { background: #F0F4FB; }
QLineEdit:focus, QTextEdit:focus, QListWidget:focus, QPushButton:focus { border: 1px solid #426BAA; }
QMenu { background: #FFFFFF; border: 1px solid #DCE3EC; padding: 5px; }
QMenu::item { padding: 7px 22px; }
QMenu::item:selected { background: #DFE9F8; }
QSplitter::handle { background: transparent; }
"""


def configure_viewer_dpi() -> None:
    """在创建 QApplication 前启用逻辑像素与高分辨率图标，保留分数缩放。"""
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)


def place_viewer_window(window: QWidget, preferred_size: QSize | None = None) -> None:
    """按逻辑工作区放置编辑器，缩放后仍留出标题栏和任务栏空间。"""
    screen = window.parentWidget().screen() if window.parentWidget() is not None else window.screen()
    area = screen.availableGeometry().adjusted(12, 12, -12, -40)
    preferred_size = preferred_size or QSize(800, 760)
    width, height = min(preferred_size.width(), area.width()), min(preferred_size.height(), area.height())
    window.setMinimumSize(min(620, width), min(600, height))
    window.resize(width, height)
    # 优先占右半边；空间不足时向左收回，避免右侧按钮落在屏幕外。
    x = min(area.center().x(), area.right() - window.width() + 1)
    y = area.top() + max(0, (area.height() - window.height()) // 2)
    window.move(max(area.left(), x), y)


def preview_desktop_size(screen) -> QSize:
    """Windows 预览子进程按物理像素建窗，不复用 Qt 的逻辑桌面尺寸。"""
    size = screen.geometry().size()
    scale = screen.devicePixelRatio() if sys.platform == "win32" else 1.0
    return QSize(round(size.width() * scale), round(size.height() * scale))


def show_v3_intro_once(parent: QWidget, version: str | None, config: DSakikoConfig) -> bool:
    """首次展示 V3 说明后保存已读状态；V2 和已读用户不弹出。"""
    if version != "v3" or config.live2d_viewer_v3_intro_seen.value:
        return False
    QMessageBox.information(parent, V3_INTRO_TITLE, V3_INTRO_TEXT, QMessageBox.Ok)
    config.set(config.live2d_viewer_v3_intro_seen, True)
    return True


class CharacterPicker(QDialog):
    """用可搜索列表直接选择角色，不要求按顺序循环切换。"""

    def __init__(self, characters: list[CharacterAttributes], current: int, parent: QWidget) -> None:
        """展示头像、名称和搜索框，确认后返回原列表索引。"""
        super().__init__(parent)
        self.setWindowTitle("选择角色")
        self.resize(360, 440)
        layout = QVBoxLayout(self)
        search = QLineEdit()
        search.setPlaceholderText("搜索角色")
        layout.addWidget(search)
        self.items = QListWidget()
        for index, character in enumerate(characters):
            item = QListWidgetItem(character.character_name)
            icon = getattr(character, "icon_path", None)
            if icon:
                item.setIcon(QIcon(icon))
            item.setData(Qt.UserRole, index)
            self.items.addItem(item)
        self.items.setCurrentRow(current)
        layout.addWidget(self.items)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("选择角色")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.items.itemDoubleClicked.connect(lambda _item: self.accept())
        search.textChanged.connect(self.filter_characters)
        layout.addWidget(buttons)

    def filter_characters(self, text: str) -> None:
        """按名称筛选并避免确认被隐藏的旧选择。"""
        for index in range(self.items.count()):
            item = self.items.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())
        current = self.items.currentItem()
        if current is None or current.isHidden():
            self.items.setCurrentItem(next((self.items.item(i) for i in range(self.items.count())
                                           if not self.items.item(i).isHidden()), None))

    def selected_index(self) -> int | None:
        """返回选中的角色索引，空搜索结果不改变角色。"""
        item = self.items.currentItem()
        return int(item.data(Qt.UserRole)) if item is not None and not item.isHidden() else None
