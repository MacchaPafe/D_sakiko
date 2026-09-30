"""仅在显式选择 Cocoa 的 macOS 环境验证原生初始化与恢复。"""

from __future__ import annotations

import ctypes
import os
import sys
from unittest import TestCase, skipUnless
from unittest.mock import Mock
from queue import Queue
from types import SimpleNamespace

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPlainTextEdit, QWidget

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
            self.assertEqual(native.animationBehavior(), AppKit.NSWindowAnimationBehaviorNone)
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
            self.assertNotEqual(other_native.animationBehavior(), AppKit.NSWindowAnimationBehaviorNone)
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

    def test_input_focus_activates_native_ime_client_without_activating_app(self) -> None:
        """非激活面板展开与重新展开时恢复 Cocoa 输入客户端，并能预编辑及提交中文。"""
        import AppKit
        import objc

        window = QWidget()
        self.addCleanup(window.close)
        window.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        window.setAttribute(Qt.WA_ShowWithoutActivating)
        window.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        window.setGeometry(200, 200, 320, 180)
        focus = create_pet_focus(window)
        editor = QPlainTextEdit(window)
        editor.setGeometry(10, 10, 300, 160)
        window.show()
        native = objc.objc_object(c_void_p=int(window.winId())).window()
        view = native.contentView()
        AppKit.NSApp.deactivate()
        QTest.qWait(100)
        active_before = bool(AppKit.NSApp.isActive())
        for cycle in range(2):
            editor.clear()
            editor.show()
            # 复现此前只有面板拿到键盘、Qt 视图未成为第一响应者的状态。
            native.makeFirstResponder_(None)
            focus.request_input()
            editor.setFocus(Qt.MouseFocusReason)
            QTest.qWait(50)
            self.assertTrue(focus.has_input_focus(), f"第 {cycle + 1} 次展开未取得原生焦点")
            self.assertEqual(native.firstResponder(), view)
            self.assertEqual(AppKit.NSTextInputContext.currentInputContext(), view.inputContext())
            self.assertIsNotNone(view.inputContext())
            self.assertEqual(bool(AppKit.NSApp.isActive()), active_before)
            self.assertTrue(view.validAttributesForMarkedText())

            replacement = AppKit.NSMakeRange(AppKit.NSNotFound, 0)
            view.setMarkedText_selectedRange_replacementRange_(
                "nihao", AppKit.NSMakeRange(5, 0), replacement
            )
            self.assertTrue(view.hasMarkedText())
            self.assertEqual(editor.toPlainText(), "")
            rect, _ = view.firstRectForCharacterRange_actualRange_(AppKit.NSMakeRange(0, 0), None)
            self.assertGreater(rect.size.height, 0)
            view.insertText_replacementRange_("你好", replacement)
            self.assertFalse(view.hasMarkedText())
            self.assertEqual(editor.toPlainText(), "你好")
            frame_before = native.frame()
            self.assertEqual(native.animationBehavior(), AppKit.NSWindowAnimationBehaviorNone)
            editor.hide()
            focus.release_input()
            self.assertFalse(focus.has_input_focus())
            QTest.qWait(50)
            self.assertNotEqual(AppKit.NSApp.keyWindow(), native)
            self.assertTrue(native.isVisible())
            self.assertEqual(native.frame(), frame_before)

    def test_pet_first_expand_survives_stale_qt_focus_and_real_loss_still_closes(self) -> None:
        """直接显示桌宠后首次展开抵御滞后 Qt 通知，真实失焦仍收起且可以继续中文输入。"""
        import AppKit
        import objc
        from desktop_pet.window import PetWindow
        from runtime.drafts import DraftStore
        from runtime.voice_input import VoiceInputService
        from ui_main.theme import derive_theme_palette

        drafts = DraftStore()
        voice = VoiceInputService(drafts)
        voice._state("idle")
        host = Mock(current_chat_id="first-focus", drafts=drafts, voice_input=voice)
        host._theme_palette = derive_theme_palette("#7799CC")
        host.is_response_active.return_value = False
        host.isVisible.return_value = False
        host._current_model_supports_vision.return_value = True
        pet = PetWindow(host, Queue(), Queue(), SimpleNamespace(value=True))
        self.addCleanup(voice.close)
        self.addCleanup(pet.shutdown)
        other = QWidget()
        self.addCleanup(other.close)
        editor = QPlainTextEdit(other)
        # 主窗口从未显示，不预先 deactivate/activate 或请求过键盘焦点。
        pet.renderer.hide()
        pet.renderer.timer.stop()
        pet.show()
        QTest.qWait(100)
        for cycle in range(2):
            pet.text_button.click()
            native = objc.objc_object(c_void_p=int(pet.winId())).window()
            self.assertTrue(native.isKeyWindow())
            # 模拟冷启动时晚到的控件焦点通知，不伪造原生 key window。
            self.app.focusChanged.emit(pet.input.text_edit, editor)
            QTest.qWait(150)
            self.assertTrue(pet.expanded, f"第 {cycle + 1} 次展开被 Qt 过渡通知收起")
            self.assertTrue(native.isKeyWindow())
            self.assertFalse(pet.panel.isHidden())
            view = native.contentView()
            self.assertEqual(AppKit.NSTextInputContext.currentInputContext(), view.inputContext())
            self.assertIsNotNone(view.inputContext())
            pet.input.setPlainText("")
            replacement = AppKit.NSMakeRange(AppKit.NSNotFound, 0)
            view.setMarkedText_selectedRange_replacementRange_(
                "nihao", AppKit.NSMakeRange(5, 0), replacement
            )
            self.assertTrue(view.hasMarkedText())
            view.insertText_replacementRange_("你好", replacement)
            self.assertEqual(pet.input.toPlainText(), "你好")
            other.show()
            objc.objc_object(c_void_p=int(other.winId())).window().makeKeyWindow()
            QTest.qWait(200)
            self.assertFalse(pet.expanded)
            self.assertFalse(native.isKeyWindow())
            self.assertEqual(pet.input.toPlainText(), "你好")
            other.hide()

    def test_native_passthrough_changes_system_hit_without_changing_focus(self) -> None:
        """系统命中跳过穿透面板，恢复交互后重新命中，键盘焦点不被开关夺走。"""
        import AppKit
        import objc

        bottom = QWidget()
        self.addCleanup(bottom.close)
        bottom.setWindowTitle("桌宠原生穿透测试底层")
        bottom.setGeometry(180, 180, 500, 400)
        bottom.show()
        window = QWidget()
        self.addCleanup(window.close)
        window.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        window.setAttribute(Qt.WA_TranslucentBackground)
        window.setAttribute(Qt.WA_ShowWithoutActivating)
        window.setStyleSheet("background:rgba(60,100,150,255)")
        window.setGeometry(220, 220, 300, 250)
        focus = create_pet_focus(window)
        window.show()
        native = objc.objc_object(c_void_p=int(window.winId())).window()
        QTest.qWait(100)
        key_before = bool(native.isKeyWindow())
        point = window.mapToGlobal(QPoint(100, 100))
        screen_top = AppKit.NSMaxY(AppKit.NSScreen.screens()[0].frame())
        native_point = AppKit.NSMakePoint(point.x(), screen_top - point.y())
        for passthrough in (False, True, False, True):
            with self.subTest(passthrough=passthrough):
                focus.set_mouse_passthrough(passthrough)
                QTest.qWait(80)
                number = AppKit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_(
                    native_point, 0
                )
                self.assertEqual(number == native.windowNumber(), not passthrough)
                self.assertEqual(bool(native.isKeyWindow()), key_before)
                self.assertTrue(native.styleMask() & AppKit.NSWindowStyleMaskNonactivatingPanel)

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
