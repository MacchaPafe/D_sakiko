"""为纵向设置页提供可折叠的 Fluent 标题卡片。"""
from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QWidget
from qfluentwidgets import FluentIcon, HeaderCardWidget, TransparentToolButton


class CollapsibleSettingsCard(HeaderCardWidget):
    """保留设置标题，将不常用的表单收起。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        """创建默认折叠、同时支持鼠标与键盘操作的卡片。"""
        super().__init__(parent)
        self.setTitle(title)
        self.expanded = False
        self.toggle_button = TransparentToolButton(FluentIcon.CARE_DOWN_SOLID, self)
        self.toggle_button.setFixedSize(32, 32)
        self.toggle_button.setAccessibleName(title)
        self.toggle_button.clicked.connect(self.toggle)
        self.headerLayout.addWidget(self.toggle_button)
        for widget in (self.headerView, self.headerLabel):
            widget.setCursor(Qt.PointingHandCursor)
            widget.installEventFilter(self)
        self.set_expanded(False)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """点击标题时切换内容，不拦截子控件的输入。"""
        header_view = getattr(self, "headerView", None)
        header_label = getattr(self, "headerLabel", None)
        if watched in (header_view, header_label) and isinstance(event, QMouseEvent):
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self.toggle()
                return True
        return super().eventFilter(watched, event)

    def set_expanded(self, expanded: bool) -> None:
        """同步内容显隐和可访问的展开状态。"""
        self.expanded = expanded
        self.view.setVisible(expanded)
        self.separator.setVisible(expanded)
        self.toggle_button.setIcon(FluentIcon.CARE_UP_SOLID if expanded else FluentIcon.CARE_DOWN_SOLID)
        self.toggle_button.setToolTip("收起" if expanded else "展开")
        self.updateGeometry()

    def toggle(self) -> None:
        """展开或收起当前卡片。"""
        self.set_expanded(not self.expanded)
