"""仅在显式选择 Cocoa 的 macOS 环境验证原生初始化与恢复。"""

from __future__ import annotations

import ctypes
import os
import sys
from unittest import TestCase, skipUnless
from unittest.mock import Mock

from PyQt5.QtCore import QPoint, Qt
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

    def test_pet_can_cross_top_without_changing_other_panels(self) -> None:
        """真实显示透明测试面板，验证顶部移动、缩放和恢复均无原生回弹。"""
        import AppKit
        import objc

        window = QWidget()
        self.addCleanup(window.close)
        window.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        window.setAttribute(Qt.WA_TranslucentBackground)
        window.setAttribute(Qt.WA_ShowWithoutActivating)
        window.setAttribute(Qt.WA_TransparentForMouseEvents)
        window.setWindowOpacity(0)
        window.resize(380, 610)
        focus = create_pet_focus(window)
        self.assertTrue(focus.nonactivating)
        panel = objc.objc_object(c_void_p=int(window.winId())).window()
        window.show()
        self.app.processEvents()
        screen_top = AppKit.NSMaxY(AppKit.NSScreen.screens()[0].frame())

        def assert_native_position() -> None:
            """同时检查 Qt 与原生实际位置，避免仅验证 Qt 的请求坐标。"""
            self.app.processEvents()
            self.assertAlmostEqual(
                screen_top - AppKit.NSMaxY(panel.frame()), window.y(), delta=1
            )
            self.assertAlmostEqual(panel.frame().origin.x, window.x(), delta=1)

        for y in (100, 30, 0, -50, -100):
            window.move(100, y)
            assert_native_position()
        foot = window.pos() + QPoint(window.width() // 2, 465)
        window.resize(380, 510)
        window.move(foot - QPoint(window.width() // 2, 372))
        assert_native_position()
        before = window.pos()
        window.hide()
        window.show()
        focus.refresh_native()
        assert_native_position()
        self.assertEqual(window.pos(), before)
        self.assertTrue(panel.styleMask() & AppKit.NSWindowStyleMaskNonactivatingPanel)

        other = QWidget()
        self.addCleanup(other.close)
        other.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        other_panel = objc.objc_object(c_void_p=int(other.winId())).window()
        for screen in AppKit.NSScreen.screens():
            requested = AppKit.NSMakeRect(
                screen.frame().origin.x + 100,
                AppKit.NSMaxY(screen.frame()) + 100 - 610,
                380,
                610,
            )
            self.assertEqual(
                panel.constrainFrameRect_toScreen_(requested, screen), requested
            )
            self.assertLess(
                AppKit.NSMaxY(
                    other_panel.constrainFrameRect_toScreen_(requested, screen)
                ),
                AppKit.NSMaxY(requested),
            )
