"""Windows 桌宠仅抑制鼠标激活；主动文字输入继续使用正常 Qt 焦点。"""

from __future__ import annotations

from ctypes import wintypes

from PyQt5 import sip
from PyQt5.QtCore import QByteArray

from desktop_pet.focus import PetFocus

WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3


class WindowsPetFocus(PetFocus):
    """让点击和拖动继续送达桌宠，但不因此切换前台窗口。"""

    passive_mouse = True

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
