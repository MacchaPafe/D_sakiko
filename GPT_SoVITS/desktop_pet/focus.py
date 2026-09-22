"""桌宠焦点策略：默认使用 Qt，macOS 按需适配非激活面板。"""

from __future__ import annotations

import logging
import sys

from PyQt5 import sip
from PyQt5.QtCore import QByteArray, Qt
from PyQt5.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)


class PetFocus:
    """保留 Windows、Linux 和原生适配不可用时的 Qt 行为。"""

    nonactivating = False
    passive_mouse = False

    def __init__(self, window: QWidget) -> None:
        """记录所属桌宠，不提前创建平台窗口。"""
        self.window = window

    def request_input(self) -> None:
        """用户主动输入时激活普通 Qt 窗口。"""
        self.window.activateWindow()

    def release_input(self) -> None:
        """普通窗口继续由系统管理焦点归还。"""

    def has_input_focus(self) -> bool:
        """同时检查应用与窗口，避免已失焦工具窗口的旧活动标志。"""
        return (
            QApplication.applicationState() == Qt.ApplicationActive
            and self.window.isActiveWindow()
        )

    def refresh_native(self) -> None:
        """默认后端没有需要更新的原生属性。"""

    def native_event(
        self, event_type: QByteArray, message: sip.voidptr
    ) -> tuple[bool, int]:
        """默认后端不拦截任何平台消息。"""
        return False, 0


def create_pet_focus(window: QWidget) -> PetFocus:
    """仅在 Cocoa 后端导入 AppKit，失败时保留可使用的 Qt 桌宠。"""
    if sys.platform == "win32" and QApplication.platformName() == "windows":
        from desktop_pet.windows_focus import WindowsPetFocus

        return WindowsPetFocus(window)
    if sys.platform == "darwin" and QApplication.platformName() == "cocoa":
        try:
            from desktop_pet.macos_focus import MacPetFocus

            return MacPetFocus(window)
        except Exception:
            logger.exception("桌宠非激活面板初始化失败，回退到 Qt 焦点行为")
    return PetFocus(window)
