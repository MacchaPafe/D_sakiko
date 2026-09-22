"""验证焦点后端隔离与 Windows 原生消息策略，不模拟操作系统的实际焦点。"""

from __future__ import annotations

import builtins
import ctypes
import os
from ctypes import wintypes
from unittest import TestCase
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QByteArray
from PyQt5.QtWidgets import QApplication, QWidget

from desktop_pet.focus import PetFocus, create_pet_focus
from desktop_pet.windows_focus import MA_NOACTIVATE, WM_MOUSEACTIVATE, WindowsPetFocus


class PetFocusTests(TestCase):
    """无需 AppKit 和真实 Windows 桌面的平台选择与消息回归。"""

    @classmethod
    def setUpClass(cls) -> None:
        """共享离屏 Qt 应用。"""
        cls.app = QApplication.instance() or QApplication([])

    def test_windows_never_imports_macos_dependencies(self) -> None:
        """即使系统完全缺少 PyObjC，Windows 后端仍可初始化。"""
        imported: list[str] = []
        original_import = builtins.__import__

        def guarded_import(name: str, *args: object, **kwargs: object) -> object:
            """记录依赖请求，并拒绝所有 macOS 原生模块。"""
            imported.append(name)
            if name in {"AppKit", "objc", "desktop_pet.macos_focus"}:
                raise ImportError("Windows 无 macOS 依赖")
            return original_import(name, *args, **kwargs)

        window = QWidget()
        with (
            patch("desktop_pet.focus.sys.platform", "win32"),
            patch(
                "desktop_pet.focus.QApplication.platformName", return_value="windows"
            ),
            patch("builtins.__import__", side_effect=guarded_import),
        ):
            focus = create_pet_focus(window)
        self.assertIsInstance(focus, WindowsPetFocus)
        self.assertFalse(set(imported) & {"AppKit", "objc", "desktop_pet.macos_focus"})
        window.close()

    def test_offscreen_and_missing_cocoa_dependency_fall_back_to_qt(self) -> None:
        """离屏环境和缺失原生依赖时不影响 Qt 窗口创建。"""
        window = QWidget()
        with patch(
            "desktop_pet.focus.QApplication.platformName", return_value="offscreen"
        ):
            self.assertIs(type(create_pet_focus(window)), PetFocus)
        with (
            patch("desktop_pet.focus.sys.platform", "darwin"),
            patch("desktop_pet.focus.QApplication.platformName", return_value="cocoa"),
            patch.dict("sys.modules", {"desktop_pet.macos_focus": None}),
            self.assertLogs("desktop_pet.focus", level="ERROR"),
        ):
            self.assertIs(type(create_pet_focus(window)), PetFocus)
        window.close()

    def test_windows_mouse_activation_keeps_click_and_explicit_input_activates(
        self,
    ) -> None:
        """普通鼠标消息不激活也不吞掉点击，文字输入仍走 Qt 激活路径。"""
        window = Mock(spec=QWidget)
        focus = WindowsPetFocus(window)
        message = wintypes.MSG()
        message.message = WM_MOUSEACTIVATE
        pointer = sip.voidptr(ctypes.addressof(message))
        self.assertEqual(
            focus.native_event(QByteArray(b"windows_generic_MSG"), pointer),
            (True, MA_NOACTIVATE),
        )
        message.message = 0x0100  # WM_KEYDOWN 必须交给 Qt / 输入法。
        self.assertEqual(
            focus.native_event(QByteArray(b"windows_generic_MSG"), pointer), (False, 0)
        )
        self.assertEqual(focus.native_event(QByteArray(b"other"), pointer), (False, 0))
        self.assertEqual(
            focus.native_event(QByteArray(b"windows_generic_MSG"), sip.voidptr(0)),
            (False, 0),
        )
        window.activateWindow.assert_not_called()
        focus.request_input()
        window.activateWindow.assert_called_once()
