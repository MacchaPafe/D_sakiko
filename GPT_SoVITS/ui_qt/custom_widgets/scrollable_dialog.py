from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt5.QtGui import QHideEvent, QScreen, QShowEvent
from PyQt5.QtWidgets import (
    QDialog, QFrame, QLayout, QLayoutItem, QScrollArea, QSizePolicy,
    QStyle, QVBoxLayout, QWidget,
)


class ResponsiveButtonLayout(QLayout):
    """宽度足够时横排按钮，空间不足时保留原控件并改为竖排。"""

    def __init__(self) -> None:
        """初始化不占额外边距的自适应按钮布局。"""
        super().__init__()
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(8)

    def addItem(self, item: QLayoutItem) -> None:
        """接管布局项并通知父布局重新计算尺寸。"""
        self._items.append(item)
        self.invalidate()

    def count(self) -> int:
        """返回布局项数量。"""
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        """按索引读取布局项。"""
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        """移交指定布局项的所有权。"""
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def expandingDirections(self) -> Qt.Orientations:
        """仅在水平方向利用额外空间。"""
        return Qt.Orientations(Qt.Horizontal)

    def hasHeightForWidth(self) -> bool:
        """让父布局按照实际宽度预留换行后的高度。"""
        return True

    def minimumSize(self) -> QSize:
        """最小宽度只要求容纳单个按钮，避免横排反过来限制窗口缩小。"""
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def sizeHint(self) -> QSize:
        """以一行容纳全部按钮作为理想尺寸。"""
        sizes = [item.sizeHint() for item in self._items if not item.isEmpty()]
        return QSize(
            sum(size.width() for size in sizes) + max(0, len(sizes) - 1) * self.spacing(),
            max((size.height() for size in sizes), default=0),
        )

    def heightForWidth(self, width: int) -> int:
        """计算当前宽度下的单行或多行高度。"""
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def minimumHeightForWidth(self, width: int) -> int:
        """让滚动区域为换行后的按钮保留完整高度，而不压缩其他分组。"""
        return self.heightForWidth(width)

    def setGeometry(self, rect: QRect) -> None:
        """在布局分配的区域内排列现有按钮。"""
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def _arrange(self, rect: QRect, apply: bool) -> int:
        """共享测量与放置逻辑，避免断点附近的高度与排列不一致。"""
        items = [item for item in self._items if not item.isEmpty()]
        if not items:
            return 0
        sizes = [item.sizeHint().expandedTo(item.minimumSize()) for item in items]
        spacing = self.spacing()
        required_width = sum(size.width() for size in sizes) + spacing * (len(items) - 1)
        vertical = rect.width() < required_width
        height = (
            sum(size.height() for size in sizes) + spacing * (len(items) - 1)
            if vertical else max(size.height() for size in sizes)
        )
        if apply:
            extra = max(0, rect.width() - required_width)
            x, y = rect.x(), rect.y()
            for index, (item, size) in enumerate(zip(items, sizes)):
                width = rect.width() if vertical else size.width() + extra // len(items)
                if not vertical and index == len(items) - 1:
                    width = rect.right() - x + 1
                item.setGeometry(QRect(x, y, width, size.height() if vertical else height))
                if vertical:
                    y += size.height() + spacing
                else:
                    x += width + spacing
        return height


class ScrollableDialog(QDialog):
    """使用原生滚动区域承载长内容，并将弹窗限制在当前屏幕工作区内。"""

    def __init__(self, parent: QWidget | None = None, *, preferred_size: QSize) -> None:
        """创建可滚动内容区和独立底部操作区。"""
        super().__init__(parent)
        self._screen: QScreen | None = None
        self._placed = False
        self.resize(preferred_size)
        self.setSizeGripEnabled(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("dialogScrollArea")
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scroll_area.setStyleSheet("QScrollArea#dialogScrollArea { background: transparent; }")
        self.scroll_area.viewport().setAutoFillBackground(False)

        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        self.content_layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
        self.scroll_area.setWidget(self.content_widget)
        self.content_widget.setAutoFillBackground(False)

        self.footer_layout = QVBoxLayout()
        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(12, 12, 12, 12)
        self.outer_layout.addWidget(self.scroll_area, 1)
        self.outer_layout.addLayout(self.footer_layout)

    def showEvent(self, event: QShowEvent) -> None:
        """显示后读取真实窗口边框，并监听跨屏与工作区变化。"""
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None:
            handle.screenChanged.connect(self._watch_screen)
        parent = self.parentWidget()
        # 聊天主窗口的历史属性 screen 保存 QRect，需显式调用 QWidget 方法。
        screen = QWidget.screen(parent) if parent is not None else QWidget.screen(self)
        self._watch_screen(screen)
        QTimer.singleShot(0, self._fit_to_screen)

    def hideEvent(self, event: QHideEvent) -> None:
        """隐藏时移除屏幕监听，避免已关闭弹窗响应后续屏幕变化。"""
        if self._screen is not None:
            self._screen.availableGeometryChanged.disconnect(self._fit_to_screen)
            self._screen = None
        handle = self.windowHandle()
        if handle is not None:
            handle.screenChanged.disconnect(self._watch_screen)
        super().hideEvent(event)

    def _watch_screen(self, screen: QScreen) -> None:
        """将工作区监听切换到窗口当前所在的屏幕。"""
        if screen is not self._screen:
            if self._screen is not None:
                self._screen.availableGeometryChanged.disconnect(self._fit_to_screen)
            self._screen = screen
            screen.availableGeometryChanged.connect(self._fit_to_screen)
        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        """约束客户区尺寸和完整窗口位置，同时保留用户已选择的较小尺寸。"""
        if not self.isVisible() or self._screen is None:
            return
        available = self._screen.availableGeometry().adjusted(12, 12, -12, -12)
        frame = self.frameGeometry()
        decoration = frame.size() - self.size()
        maximum = QSize(
            max(1, available.width() - decoration.width()),
            max(1, available.height() - decoration.height()),
        )
        margins = self.outer_layout.contentsMargins()
        scrollbar_width = self.style().pixelMetric(QStyle.PM_ScrollBarExtent)
        minimum_width = (
            self.content_layout.minimumSize().width()
            + margins.left() + margins.right() + scrollbar_width
        )
        self.setMinimumSize(min(minimum_width, maximum.width()), min(240, maximum.height()))
        self.setMaximumSize(maximum)
        self.resize(self.size().boundedTo(maximum).expandedTo(self.minimumSize()))

        frame = self.frameGeometry()
        if not self._placed:
            parent = self.parentWidget()
            center = parent.frameGeometry().center() if parent is not None else available.center()
            position = center - QPoint(frame.width() // 2, frame.height() // 2)
            self._placed = True
        else:
            position = frame.topLeft()
        self.move(
            max(available.left(), min(position.x(), available.right() - frame.width() + 1)),
            max(available.top(), min(position.y(), available.bottom() - frame.height() + 1)),
        )
