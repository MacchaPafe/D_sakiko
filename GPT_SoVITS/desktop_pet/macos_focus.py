"""仅由 Cocoa 后端加载的非激活面板适配。"""

from __future__ import annotations

import ctypes
from typing import cast

import AppKit
import objc
from PyQt5.QtCore import QThread, Qt
from PyQt5.QtWidgets import QApplication, QWidget

from desktop_pet.focus import PetFocus


class _Point(ctypes.Structure):
    """匹配 64 位 macOS 原生 NSPoint 的内存布局。"""

    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _Size(ctypes.Structure):
    """匹配 64 位 macOS 原生 NSSize 的内存布局。"""

    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _Rect(ctypes.Structure):
    """匹配原生面板初始化参数 NSRect。"""

    _fields_ = [("origin", _Point), ("size", _Size)]


def _pet_frame(
    panel: AppKit.NSPanel, rect: AppKit.NSRect, screen: AppKit.NSScreen | None
) -> AppKit.NSRect:
    """允许透明窗口边缘越过屏幕顶部，保留 Qt 请求的拖动与缩放位置。"""
    return rect


def _allow_pet_frame(panel: AppKit.NSPanel) -> None:
    """仅为当前桌宠替换无新增实例字段的原生子类，不修改 Qt 全局面板。"""
    runtime = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    runtime.object_getClass.argtypes = [ctypes.c_void_p]
    runtime.object_getClass.restype = ctypes.c_void_p
    runtime.objc_getClass.argtypes = [ctypes.c_char_p]
    runtime.objc_getClass.restype = ctypes.c_void_p
    runtime.objc_allocateClassPair.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]
    runtime.objc_allocateClassPair.restype = ctypes.c_void_p
    runtime.objc_registerClassPair.argtypes = [ctypes.c_void_p]
    runtime.objc_registerClassPair.restype = None
    runtime.class_getInstanceSize.argtypes = [ctypes.c_void_p]
    runtime.class_getInstanceSize.restype = ctypes.c_size_t
    runtime.object_setClass.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    runtime.object_setClass.restype = ctypes.c_void_p

    pointer = objc.pyobjc_id(panel)
    original = runtime.object_getClass(pointer)
    class_name = b"DSakikoUnconstrainedPetPanel"
    subclass = runtime.objc_getClass(class_name)
    if original == subclass:
        return
    if original != runtime.objc_getClass(b"QNSPanel"):
        raise RuntimeError("无法为非 Qt 桌宠面板配置位置约束")
    if not subclass:
        subclass = runtime.objc_allocateClassPair(original, class_name, 0)
        if not subclass:
            raise RuntimeError("无法创建桌宠专用原生面板类")
        runtime.objc_registerClassPair(subclass)
        # 由 PyObjC 处理 NSRect 的结构体返回 ABI；不增加 Python 子类实例字段。
        objc.classAddMethods(
            objc.lookUpClass(class_name.decode("ascii")),
            [
                objc.selector(
                    _pet_frame,
                    selector=b"constrainFrameRect:toScreen:",
                    signature=AppKit.NSWindow.constrainFrameRect_toScreen_.signature,
                )
            ],
        )
    if runtime.class_getInstanceSize(original) != runtime.class_getInstanceSize(
        subclass
    ):
        raise RuntimeError("桌宠原生面板实例布局不兼容")
    runtime.object_setClass(pointer, subclass)


def _create_panel(window: QWidget) -> AppKit.NSPanel:
    """仅在同步创建桌宠原生窗口期间调整初始化参数，并恢复原实现。"""
    if QThread.currentThread() != QApplication.instance().thread():
        raise RuntimeError("桌宠原生面板必须在 GUI 线程创建")
    if window.testAttribute(Qt.WA_WState_Created):
        raise RuntimeError("非激活面板必须在原生窗口首次创建时配置")

    selector_name = b"initWithContentRect:styleMask:backing:defer:screen:"
    runtime = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    runtime.sel_registerName.argtypes = [ctypes.c_char_p]
    runtime.sel_registerName.restype = ctypes.c_void_p
    runtime.objc_getClass.argtypes = [ctypes.c_char_p]
    runtime.objc_getClass.restype = ctypes.c_void_p
    runtime.object_getClass.argtypes = [ctypes.c_void_p]
    runtime.object_getClass.restype = ctypes.c_void_p
    runtime.class_getName.argtypes = [ctypes.c_void_p]
    runtime.class_getName.restype = ctypes.c_char_p
    runtime.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    runtime.class_getInstanceMethod.restype = ctypes.c_void_p
    runtime.class_addMethod.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_char_p,
    ]
    runtime.class_addMethod.restype = ctypes.c_bool
    runtime.method_getTypeEncoding.argtypes = [ctypes.c_void_p]
    runtime.method_getTypeEncoding.restype = ctypes.c_char_p
    runtime.method_getImplementation.argtypes = [ctypes.c_void_p]
    runtime.method_getImplementation.restype = ctypes.c_void_p
    runtime.method_setImplementation.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    runtime.method_setImplementation.restype = ctypes.c_void_p
    native_class = runtime.objc_getClass(b"NSPanel")
    selector = runtime.sel_registerName(selector_name)
    method = runtime.class_getInstanceMethod(native_class, selector)
    if not method:
        raise RuntimeError("找不到 NSPanel 初始化入口")
    original_imp = runtime.method_getImplementation(method)
    if not original_imp:
        raise RuntimeError("无法保存 NSPanel 初始化实现")
    # 如果方法继承自 NSWindow，先在 NSPanel 复制原实现，不能修改 NSWindow。
    runtime.class_addMethod(
        native_class, selector, original_imp, runtime.method_getTypeEncoding(method)
    )
    method = runtime.class_getInstanceMethod(native_class, selector)
    signature = ctypes.CFUNCTYPE(
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        _Rect,
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_bool,
        ctypes.c_void_p,
    )
    original = signature(original_imp)
    created: list[int] = []

    def initialize(
        panel: int,
        command: int,
        rect: _Rect,
        style: int,
        backing: int,
        defer: bool,
        screen: int | None,
    ) -> int:
        """只修改此创建调用中的首个 Qt 工具面板，其他面板直接转发。"""
        target = (
            runtime.class_getName(runtime.object_getClass(panel)) == b"QNSPanel"
            and not created
        )
        if target:
            style |= AppKit.NSWindowStyleMaskNonactivatingPanel
        result = original(panel, command, rect, style, backing, defer, screen)
        if target:
            created.append(result)
        return result

    # 不运行事件循环；退出同步 winId() 调用即恢复原生 IMP。
    # 不使用 classAddMethods，避免 PyObjC 为临时 Python 方法缓存错误的实现信息。
    replacement = signature(initialize)
    try:
        runtime.method_setImplementation(
            method, ctypes.cast(replacement, ctypes.c_void_p)
        )
        view = objc.objc_object(c_void_p=int(window.winId()))
    finally:
        runtime.method_setImplementation(method, original_imp)
    panel = cast(AppKit.NSPanel, view.window())
    if not created or objc.pyobjc_id(panel) != created[0]:
        raise RuntimeError("Qt 未通过预期的 NSPanel 初始化入口创建桌宠")
    return panel


class MacPetFocus(PetFocus):
    """允许桌宠接收键盘输入，同时保留其他应用的前台状态。"""

    nonactivating = True
    passive_mouse = True

    def __init__(self, window: QWidget) -> None:
        """创建独立非激活面板，不改变聊天窗口和其他工具面板。"""
        super().__init__(window)
        self.panel = _create_panel(window)
        _allow_pet_frame(self.panel)
        self.refresh_native()

    def refresh_native(self) -> None:
        """恢复 Qt 设置后的面板标志，并让后台悬浮事件进入 Qt。"""
        self.panel.setStyleMask_(
            self.panel.styleMask() | AppKit.NSWindowStyleMaskNonactivatingPanel
        )
        self.panel.setBecomesKeyOnlyIfNeeded_(True)
        self.panel.setAcceptsMouseMovedEvents_(True)
        view = self.panel.contentView()
        for old in list(view.trackingAreas()):
            if old.options() & AppKit.NSTrackingActiveAlways:
                continue
            options = old.options() & ~(
                AppKit.NSTrackingActiveInActiveApp
                | AppKit.NSTrackingActiveInKeyWindow
                | AppKit.NSTrackingActiveWhenFirstResponder
            )
            options |= AppKit.NSTrackingActiveAlways
            area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                old.rect(), options, old.owner(), old.userInfo()
            )
            view.removeTrackingArea_(old)
            view.addTrackingArea_(area)

    def request_input(self) -> None:
        """只让面板接收键盘，不调用应用激活接口。"""
        self.panel.makeKeyWindow()

    def release_input(self) -> None:
        """收起输入框时释放原生键盘焦点。"""
        if self.panel.isKeyWindow():
            self.panel.resignKeyWindow()

    def has_input_focus(self) -> bool:
        """以原生键盘焦点为准，避免 Qt 工具窗口活动标志滞留。"""
        return bool(self.panel.isKeyWindow())
