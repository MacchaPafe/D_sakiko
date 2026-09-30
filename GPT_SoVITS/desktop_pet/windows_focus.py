"""Windows 桌宠保留被动焦点，并通过分层窗口样式让空白区域穿透。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache
import logging

from PyQt5 import sip
from PyQt5.QtCore import QByteArray

from desktop_pet.focus import PetFocus

WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _user32() -> ctypes.WinDLL:
    """仅在真实 Windows 输入适配运行时加载原生 API，并保留指针宽度。"""
    library = ctypes.WinDLL("user32", use_last_error=True)
    get_style = library.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else library.GetWindowLongW
    set_style = library.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else library.SetWindowLongW
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_style.restype = ctypes.c_ssize_t
    library.GetAsyncKeyState.argtypes = [ctypes.c_int]
    library.GetAsyncKeyState.restype = ctypes.c_short
    return library


class WindowsPetFocus(PetFocus):
    """让点击和拖动继续送达桌宠，但不因此切换前台窗口。"""

    passive_mouse = True
    mouse_passthrough = True

    def set_mouse_passthrough(self, enabled: bool) -> None:
        """只切换透明窗口的鼠标穿透位，保留 Qt 的分层绘制和其他扩展样式。"""
        library = _user32()
        get_style = library.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else library.GetWindowLongW
        set_style = library.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else library.SetWindowLongW
        handle = int(self.window.winId())
        ctypes.set_last_error(0)
        style = get_style(handle, GWL_EXSTYLE)
        error = ctypes.get_last_error()
        if error:
            logger.error("读取桌宠窗口鼠标样式失败：%s", error)
            self.mouse_passthrough = False
            return
        updated = style | WS_EX_TRANSPARENT if enabled else style & ~WS_EX_TRANSPARENT
        if updated != style:
            ctypes.set_last_error(0)
            set_style(handle, GWL_EXSTYLE, updated)
            error = ctypes.get_last_error()
            if error:
                logger.error("更新桌宠窗口鼠标穿透失败：%s", error)
                self.mouse_passthrough = False

    def mouse_buttons_pressed(self) -> bool:
        """读取所有鼠标键的系统状态，保留从下层窗口开始的拖动。"""
        return any(_user32().GetAsyncKeyState(button) & 0x8000 for button in (1, 2, 4, 5, 6))

    def native_event(
        self, event_type: QByteArray, message: sip.voidptr
    ) -> tuple[bool, int]:
        """只处理 Qt 传入的窗口消息，其他事件交回原有窗口过程。"""
        if event_type != b"windows_generic_MSG" or not message:
            return False, 0
        native_message = wintypes.MSG.from_address(int(message))
        if native_message.message == WM_MOUSEACTIVATE:
            # 不吞掉鼠标事件；键盘入口的 expand() 会主动请求 Qt 输入焦点。
            return True, MA_NOACTIVATE
        return False, 0
