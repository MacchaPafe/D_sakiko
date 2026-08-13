"""提供聊天输入区可复用的紧凑选项控件。"""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QToolButton, QWidget

from ui_main.theme import ThemePalette


def _check_state_icon(color: str, size: int = 14) -> QIcon:
    """创建保留固定图标占位、仅在选中状态绘制勾号的图标。"""

    transparent = QPixmap(size, size)
    transparent.fill(Qt.transparent)
    checked = QPixmap(size, size)
    checked.fill(Qt.transparent)
    painter = QPainter(checked)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(
            QPen(QColor(color), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        )
        painter.drawLine(2, 7, 6, 11)
        painter.drawLine(6, 11, 12, 3)
    finally:
        painter.end()

    icon = QIcon()
    icon.addPixmap(transparent, QIcon.Normal, QIcon.Off)
    icon.addPixmap(checked, QIcon.Normal, QIcon.On)
    icon.addPixmap(transparent, QIcon.Disabled, QIcon.Off)
    icon.addPixmap(checked, QIcon.Disabled, QIcon.On)
    return icon


class ToggleChip(QToolButton):
    """用固定标签、勾号和填充色表达二元选中状态。"""

    def __init__(
        self,
        label: str,
        *,
        accessible_name: str,
        height: int,
        parent: QWidget | None = None,
    ) -> None:
        """创建一个可由鼠标或键盘直接切换的紧凑按钮。"""

        super().__init__(parent)
        self.setText(label)
        self.setAccessibleName(accessible_name)
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(14, 14))
        self.setFixedHeight(height)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_theme_palette(self, palette: ThemePalette) -> None:
        """根据语义色板刷新二元选项的全部交互状态。"""

        self.setIcon(_check_state_icon(palette.on_accent))
        self.setStyleSheet(
            f"""
            QToolButton {{
                color: {palette.text_accent};
                background-color: {palette.surface_tint};
                border: 1px solid {palette.border_subtle};
                border-radius: 8px;
                padding: 0px 9px 0px 6px;
            }}
            QToolButton:hover {{
                background-color: {palette.surface_selected};
            }}
            QToolButton:pressed {{
                background-color: {palette.border_subtle};
            }}
            QToolButton:checked {{
                color: {palette.on_accent};
                background-color: {palette.accent};
                border-color: {palette.accent};
            }}
            QToolButton:checked:hover {{
                background-color: {palette.accent_hover};
                border-color: {palette.accent_hover};
            }}
            QToolButton:checked:pressed {{
                background-color: {palette.accent_pressed};
                border-color: {palette.accent_pressed};
            }}
            QToolButton:focus {{
                border: 2px solid {palette.focus_ring};
            }}
            QToolButton:disabled {{
                color: {palette.text_secondary};
                background-color: {palette.surface_tint};
            }}
            """
        )


class ChoiceChip(QToolButton):
    """用当前值和下拉箭头表达可从菜单选择的单一模式。"""

    def __init__(
        self,
        *,
        accessible_name: str,
        height: int,
        parent: QWidget | None = None,
    ) -> None:
        """创建整块点击都会打开菜单的选项按钮。"""

        super().__init__(parent)
        self.setAccessibleName(accessible_name)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setFixedHeight(height)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_theme_palette(self, palette: ThemePalette) -> None:
        """根据语义色板刷新菜单型选项的交互状态。"""

        self.setStyleSheet(
            f"""
            QToolButton {{
                color: {palette.text_accent};
                background-color: {palette.surface_tint};
                border: 1px solid {palette.border_subtle};
                border-radius: 8px;
                padding: 0px 22px 0px 9px;
            }}
            QToolButton:hover {{
                background-color: {palette.surface_selected};
            }}
            QToolButton:pressed {{
                background-color: {palette.border_subtle};
            }}
            QToolButton:focus {{
                border: 2px solid {palette.focus_ring};
            }}
            QToolButton::menu-indicator {{
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 14px;
                right: 4px;
            }}
            """
        )


class SplitToggleChip(ToggleChip):
    """将主区切换和右侧配置菜单分开的二元选项按钮。"""

    def __init__(
        self,
        label: str,
        *,
        accessible_name: str,
        height: int,
        parent: QWidget | None = None,
    ) -> None:
        """创建主区切换、箭头区打开菜单的分段按钮。"""

        super().__init__(
            label,
            accessible_name=accessible_name,
            height=height,
            parent=parent,
        )
        self.setPopupMode(QToolButton.MenuButtonPopup)

    def set_theme_palette(self, palette: ThemePalette) -> None:
        """刷新分段按钮，并为箭头区保留清晰的点击范围。"""

        super().set_theme_palette(palette)
        base_style = self.styleSheet()
        self.setStyleSheet(
            base_style
            + f"""
            QToolButton {{
                padding-right: 25px;
            }}
            QToolButton::menu-button {{
                width: 24px;
                border-left: 1px solid {palette.border_subtle};
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
            }}
            QToolButton:checked::menu-button {{
                border-left-color: {palette.on_accent};
            }}
            QToolButton::menu-indicator {{
                subcontrol-origin: padding;
                subcontrol-position: center right;
                right: 7px;
            }}
            """
        )
