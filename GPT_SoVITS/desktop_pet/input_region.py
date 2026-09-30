"""桌宠鼠标区域：可见角色与交互控件命中，其他位置交给下层应用。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject, QPoint, QRectF, Qt, QTimer
from PyQt5.QtGui import QCursor, QPainterPath
from PyQt5.QtWidgets import QWidget

if TYPE_CHECKING:
    from desktop_pet.window import PetWindow


def rounded_contains(widget: QWidget, point: QPoint, radius: float) -> bool:
    """仅命中当前显示控件的圆角内部，不把阴影和透明角计入交互区域。"""
    if widget.isHidden():
        return False
    path = QPainterPath()
    path.addRoundedRect(QRectF(widget.geometry()), radius, radius)
    return path.contains(point)


class PetInputRegion(QObject):
    """独立轮询全局指针，穿透期间仍能恢复角色命中与悬浮入口。"""

    def __init__(self, window: PetWindow) -> None:
        """将区域追踪绑定窗口生命周期，不安装全局输入钩子。"""
        super().__init__(window)
        self.window = window
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(8)
        self.timer.timeout.connect(self.refresh)
        self._refreshing = False

    def start(self) -> None:
        """窗口恢复时重新计算穿透，隐藏期间不轮询鼠标。"""
        if self.window._focus.mouse_passthrough:
            self.refresh()
            self.timer.start()

    def contains(self, point: QPoint) -> bool:
        """合并角色轮廓、可操作卡片与加载失败入口，纯提示气泡允许穿透。"""
        window = self.window
        if not window.rect().contains(point):
            return False
        if not window.fallback.isHidden():
            if window.fallback.geometry().contains(point):
                return True
        elif window.renderer.hit_test(point - window.renderer.pos()):
            return True
        return any(
            rounded_contains(control, point, radius)
            for control, radius in (
                (window.tools, 12.0), (window.panel, 16.0), (window.subtitle, 8.0)
            )
        )

    def refresh(self) -> None:
        """先更新悬浮再切换系统命中；按住鼠标期间保持原接收方以保护拖动。"""
        window = self.window
        if self._refreshing or not window.isVisible() or not window._focus.mouse_passthrough:
            return
        if window._focus.mouse_buttons_pressed() or window.renderer.press is not None:
            return
        self._refreshing = True
        try:
            point = window.mapFromGlobal(QCursor.pos())
            hit = self.contains(point)
            # 只在入口已经展开时保留跨越间隙的悬浮；间隙本身仍然穿透。
            bridge = window.bounds.translated(window.renderer.pos()).united(window.tools.geometry())
            hovered = hit or (window.hovered and bridge.contains(point))
            if window.hovered != hovered:
                window.hovered = hovered
                window.refresh_state()
                hit = self.contains(point)
            passthrough = not hit
            window._focus.set_mouse_passthrough(passthrough)
        finally:
            self._refreshing = False

    def stop(self) -> None:
        """销毁渲染器前停止指针追踪。"""
        self.timer.stop()
