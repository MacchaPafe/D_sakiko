"""小剧场三窗口布局：记录实际外框、临时对齐并恢复进入前状态。"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PyQt5.QtCore import QRect, QTimer
from PyQt5.QtWidgets import QApplication, QWidget


class WindowPlacement(ctypes.Structure):
    """保存 Windows 窗口正常尺寸及最大化/最小化状态。"""

    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT), ("minPosition", wintypes.POINT),
                ("maxPosition", wintypes.POINT), ("normalPosition", wintypes.RECT)]


class MonitorInfo(ctypes.Structure):
    """取得屏幕物理坐标原点，用于 Qt 逻辑坐标跨 DPI 换算。"""

    _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD)]


def workspace_rects(stage, control, available, timeline_height, gap=0):
    """优先只上移；空间不足时缩小上方高度，返回三个逻辑外框矩形。"""
    timeline_height = min(timeline_height, max(140, available.height() // 2))
    total = min(stage.width() + control.width(), available.width())
    control_width = min(control.width(), max(180, round(total * 0.45)))
    stage_width = total - control_width
    top_height = min(max(stage.height(), control.height()), available.height() - timeline_height - gap)
    left = max(available.left(), min(control.left() - stage_width, available.right() + 1 - total))
    desired_top = min(stage.top(), control.top())
    overflow = max(0, desired_top + top_height + gap + timeline_height - available.bottom() - 1)
    top = max(available.top(), desired_top - overflow)
    stage_rect = QRect(left, top, stage_width, top_height)
    control_rect = QRect(left + stage_width, top, control_width, top_height)
    timeline_rect = QRect(left, top + top_height + gap, total, timeline_height)
    return stage_rect, control_rect, timeline_rect


def native_frame_insets(handle):
    """读取 Windows 可见边框外的透明缩放边缘，非 Windows 返回零。"""
    if sys.platform != "win32":
        return (0, 0, 0, 0)
    outer = wintypes.RECT()
    visible = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(outer))
    result = ctypes.windll.dwmapi.DwmGetWindowAttribute(handle, 9, ctypes.byref(visible), ctypes.sizeof(visible))
    if result != 0:
        return (0, 0, 0, 0)
    return (max(0, visible.left - outer.left), max(0, visible.top - outer.top),
            max(0, outer.right - visible.right), max(0, outer.bottom - visible.bottom))


def visible_geometry(window):
    """返回 Qt 窗口实际可见外框，拼接时不计入系统透明边缘。"""
    frame = window.frameGeometry()
    if sys.platform == "win32" and QApplication.platformName() == "windows":
        margins = native_frame_insets(wintypes.HWND(int(window.winId())))
        left, top, right, bottom = [round(value / window.devicePixelRatioF()) for value in margins]
        frame = frame.adjusted(left, top, -right, -bottom)
    return frame


def set_outer_geometry(window, rect):
    """将目标可见外框转成 Qt 客户区，同时补偿系统透明边缘。"""
    frame = window.frameGeometry()
    visible = visible_geometry(window)
    rect = rect.adjusted(frame.left() - visible.left(), frame.top() - visible.top(),
                         frame.right() - visible.right(), frame.bottom() - visible.bottom())
    client = window.geometry()
    left = client.left() - frame.left()
    top = client.top() - frame.top()
    right = frame.right() - client.right()
    bottom = frame.bottom() - client.bottom()
    window.setGeometry(rect.adjusted(left, top, -right, -bottom))


class MultiCharWindowLayout:
    """资源窗口作为工作区锚点，时间轴不单独移动或抢占全局置顶。"""

    def __init__(self, owner, resources, timeline, send):
        """记录原控制窗口状态；舞台实际状态由渲染回执提供句柄后读取。"""
        self.owner = owner
        self.resources = resources
        self.timeline = timeline
        self.send = send
        self.original = owner.saveGeometry()
        self.original_frame = visible_geometry(owner)
        self.original_state = owner.windowState()
        self.handle = None
        self.native_rect = None
        self.native_placement = None
        self.last_native = None
        self.closed = False
        self.syncing = False
        self.previous_rect = None
        self.stage_width = 0
        self.ready = False
        self.timer = QTimer(resources)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.follow)

    def coordinates(self):
        """返回当前屏幕的逻辑原点、物理原点及比例，不直接混合两套坐标。"""
        screen = self.resources.screen() or QApplication.primaryScreen()
        logical = screen.geometry().topLeft()
        scale = screen.devicePixelRatio()
        physical = (logical.x(), logical.y())
        if sys.platform == "win32":
            user = ctypes.windll.user32
            user.MonitorFromWindow.restype = wintypes.HANDLE
            monitor = user.MonitorFromWindow(wintypes.HWND(int(self.resources.winId())), 2)
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            user.GetMonitorInfoW(wintypes.HANDLE(monitor), ctypes.byref(info))
            physical = (info.monitor.left, info.monitor.top)
        return logical, physical, scale

    def to_logical(self, rect):
        """将 SDL/Windows 物理外框转换为当前屏幕的 Qt 逻辑外框。"""
        origin, physical, scale = self.coordinates()
        return QRect(round((rect[0] - physical[0]) / scale + origin.x()),
                     round((rect[1] - physical[1]) / scale + origin.y()),
                     round(rect[2] / scale), round(rect[3] / scale))

    def to_native(self, rect):
        """将 Qt 布局外框转换为 Windows 物理坐标。"""
        origin, physical, scale = self.coordinates()
        return (round((rect.x() - origin.x()) * scale + physical[0]),
                round((rect.y() - origin.y()) * scale + physical[1]),
                round(rect.width() * scale), round(rect.height() * scale))

    def read_native(self):
        """读取仍存在的舞台窗口外框，关闭时返回空。"""
        if not self.handle or not ctypes.windll.user32.IsWindow(self.handle):
            return None
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self.handle, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)

    def stage_visible_geometry(self, native):
        """把舞台原生外框中的透明边缘排除后换算到 Qt 坐标。"""
        left, top, right, bottom = native_frame_insets(self.handle)
        x, y, width, height = native
        return self.to_logical((x + left, y + top, width - left - right, height - top - bottom))

    def bind(self, handle, geometry=None):
        """首次握手后排列真实舞台，重复回执不覆盖原位置快照。"""
        if self.closed or self.ready:
            return
        # ViewerGUI.screen 是旧代码保存的 QRect，不能按实例方法调用。
        available = (QWidget.screen(self.owner) or QApplication.primaryScreen()).availableGeometry()
        control = self.original_frame
        stage = QRect(control.left() - round(control.width() * 1.3), control.top(),
                      round(control.width() * 1.3), control.height())
        if sys.platform == "win32" and handle:
            self.handle = wintypes.HWND(int(handle))
            self.native_rect = self.read_native()
            if self.native_rect:
                stage = self.stage_visible_geometry(self.native_rect)
                self.native_placement = WindowPlacement()
                self.native_placement.length = ctypes.sizeof(WindowPlacement)
                ctypes.windll.user32.GetWindowPlacement(self.handle, ctypes.byref(self.native_placement))
                if self.native_placement.showCmd in (2, 3):
                    ctypes.windll.user32.ShowWindow(self.handle, 9)
        elif geometry:
            stage = QRect(*geometry)
        timeline_height = self.timeline.property("preferredHeight") or self.timeline.minimumSizeHint().height()
        timeline_height = max(timeline_height, round(available.height() * 0.26))
        timeline_height = min(timeline_height, round(available.height() * 0.45))
        stage, control, bottom = workspace_rects(stage, control, available, timeline_height)
        self.stage_width = stage.width()
        self.resources.showNormal()
        set_outer_geometry(self.resources, control)
        self.timeline.setGeometry(bottom)
        self.previous_rect = None
        self.ready = bool(handle or geometry)
        self.follow()
        self.timer.start()

    def follow(self):
        """统一约束三窗到可用区域，鼠标拖拽期间不重排窗口。"""
        if self.closed or self.syncing or QApplication.mouseButtons() or self.resources.isMinimized():
            return
        frame = visible_geometry(self.resources)
        native = self.read_native()
        if native and self.last_native and native != self.last_native and frame == self.previous_rect:
            stage = self.stage_visible_geometry(native)
            self.stage_width = stage.width()
            frame = QRect(stage.right() + 1, stage.top(), frame.width(), stage.height())
        if frame == self.previous_rect:
            return
        self.syncing = True
        available = (self.resources.screen() or QApplication.primaryScreen()).availableGeometry()
        stage = QRect(frame.left() - self.stage_width, frame.top(), self.stage_width, frame.height())
        stage, frame, bottom = workspace_rects(stage, frame, available, self.timeline.height())
        self.stage_width = stage.width()
        set_outer_geometry(self.resources, frame)
        self.previous_rect = visible_geometry(self.resources)
        set_outer_geometry(self.owner, self.previous_rect)
        self.timeline.setGeometry(bottom)
        if self.handle and native:
            x, y, width, height = self.to_native(stage)
            left, top, right, bottom = native_frame_insets(self.handle)
            target = (x - left, y - top, width + left + right, height + top + bottom)
            ctypes.windll.user32.SetWindowPos(self.handle, None, *target, 0x0014)
            self.last_native = self.read_native()
        elif self.ready:
            self.send("window_geometry", geometry=[stage.x(), stage.y(), stage.width(), stage.height()])
        self.syncing = False

    def minimize_stage(self, minimized):
        """资源窗口最小化时同步舞台，恢复时不抢占系统全局置顶。"""
        if self.handle and self.read_native():
            ctypes.windll.user32.ShowWindow(self.handle, 6 if minimized else 9)
        elif self.ready:
            self.send("window_minimize", minimized=minimized)

    def restore(self):
        """恢复进入前实际位置、尺寸和状态；Qt 自身处理屏幕移除后的归位。"""
        self.closed = True
        self.timer.stop()
        self.owner.restoreGeometry(self.original)
        self.owner.setWindowState(self.original_state)
        if self.handle and self.native_placement and self.read_native():
            ctypes.windll.user32.SetWindowPlacement(self.handle, ctypes.byref(self.native_placement))
