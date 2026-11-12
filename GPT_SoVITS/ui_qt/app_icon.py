"""桌面窗口共享的程序标识，不使用角色头像作为窗口图标。"""
from __future__ import annotations

from pathlib import Path
from sys import platform
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PyQt5.QtGui import QIcon


# 位图与源文件 logo_thumb.svg 同放 launcher/assets/，保留 SVG 遮罩与透明边缘。
APP_ICON_PATH = Path(__file__).resolve().parents[2] / "launcher" / "assets" / "logo_thumb.png"
# 同一 SVG 居中缩放，512 像素画布每侧留 44 像素透明边。
MACOS_DOCK_ICON_PATH = APP_ICON_PATH.with_name("logo_thumb_macos_dock.png")


def _icon_path(for_dock: bool) -> Path:
    """仅在 macOS 的 Dock 图标设置入口使用带留边的资源。"""
    if for_dock and platform == "darwin":
        return MACOS_DOCK_ICON_PATH
    return APP_ICON_PATH


def application_icon(*, for_dock: bool = False) -> QIcon:
    """按绝对资源路径创建 Qt 图标，不依赖程序启动目录。"""
    from PyQt5.QtGui import QIcon
    return QIcon(str(_icon_path(for_dock)))


def set_pygame_window_icon(*, for_dock: bool = False) -> None:
    """SDL 的 SVG 支持存在尺寸差异，统一使用同源 PNG。"""
    import pygame
    pygame.display.set_icon(pygame.image.load(str(_icon_path(for_dock))))
