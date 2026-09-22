"""仅在显式选择 Cocoa 的 macOS 环境验证原生初始化与恢复。"""

from __future__ import annotations

import ctypes
import os
import sys
from unittest import TestCase, skipUnless
from unittest.mock import Mock

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QWidget

from desktop_pet.focus import create_pet_focus


@skipUnless(
    sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM") == "cocoa",
    "仅在 QT_QPA_PLATFORM=cocoa 的 macOS 原生会话运行",
)
class NativePetFocusTests(TestCase):
    """验证真实原生面板创建不污染同进程中的其他窗口。"""

    @classmethod
    def setUpClass(cls) -> None:
        """共享原生 Qt 应用，窗口保持隐藏以避免干扰桌面。"""
        cls.app = QApplication.instance() or QApplication([])

    def initializer_address(self) -> int:
        """读取真实方法地址，检测创建后和异常后的原样恢复。"""
        runtime = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        runtime.objc_getClass.argtypes = [ctypes.c_char_p]
        runtime.objc_getClass.restype = ctypes.c_void_p
        runtime.sel_registerName.argtypes = [ctypes.c_char_p]
        runtime.sel_registerName.restype = ctypes.c_void_p
        runtime.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        runtime.class_getInstanceMethod.restype = ctypes.c_void_p
        runtime.method_getImplementation.argtypes = [ctypes.c_void_p]
        runtime.method_getImplementation.restype = ctypes.c_void_p
        method = runtime.class_getInstanceMethod(
            runtime.objc_getClass(b"NSPanel"),
            runtime.sel_registerName(
                b"initWithContentRect:styleMask:backing:defer:screen:"
            ),
        )
        return int(runtime.method_getImplementation(method))

    def test_repeated_creation_restores_initializer_and_preserves_other_panels(
        self,
    ) -> None:
        """多次创建桌宠面板仍保留普通工具窗口语义及后台悬浮追踪。"""
        import AppKit
        import objc

        original = self.initializer_address()
        for _ in range(3):
            window = QWidget()
            window.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
            focus = create_pet_focus(window)
            self.assertTrue(focus.nonactivating)
            native = objc.objc_object(c_void_p=int(window.winId())).window()
            self.assertTrue(
                native.styleMask() & AppKit.NSWindowStyleMaskNonactivatingPanel
            )
            self.assertTrue(native.becomesKeyOnlyIfNeeded())
            areas = native.contentView().trackingAreas()
            self.assertTrue(areas)
            self.assertTrue(
                all(area.options() & AppKit.NSTrackingActiveAlways for area in areas)
            )
            focus.refresh_native()
            self.assertEqual(len(native.contentView().trackingAreas()), len(areas))
            self.assertEqual(self.initializer_address(), original)
            self.assertIsNotNone(
                AppKit.NSPanel.instanceMethodForSelector_(
                    b"initWithContentRect:styleMask:backing:defer:screen:"
                )
            )
            other = QWidget()
            other.setWindowFlags(Qt.Tool)
            other_native = objc.objc_object(c_void_p=int(other.winId())).window()
            self.assertFalse(
                other_native.styleMask() & AppKit.NSWindowStyleMaskNonactivatingPanel
            )
            other.close()
            window.close()

    def test_creation_failure_restores_initializer(self) -> None:
        """原生窗口创建抛错时也恢复 NSPanel 原始方法。"""
        from desktop_pet.macos_focus import _create_panel

        original = self.initializer_address()
        window = Mock(spec=QWidget)
        window.testAttribute.return_value = False
        window.winId.side_effect = RuntimeError("模拟原生窗口创建失败")
        with self.assertRaisesRegex(RuntimeError, "模拟原生窗口创建失败"):
            _create_panel(window)
        self.assertEqual(self.initializer_address(), original)
