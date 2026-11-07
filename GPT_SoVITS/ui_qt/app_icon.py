"""桌面窗口共享的程序标识，不使用角色头像作为窗口图标。"""
from pathlib import Path


# 位图与源文件 logo_thumb.svg 同放 launcher/assets/，保留 SVG 遮罩与透明边缘。
APP_ICON_PATH = Path(__file__).resolve().parents[2] / "launcher" / "assets" / "logo_thumb.png"


def application_icon():
    """按绝对资源路径创建 Qt 图标，不依赖程序启动目录。"""
    from PyQt5.QtGui import QIcon
    return QIcon(str(APP_ICON_PATH))


def set_pygame_window_icon():
    """SDL 的 SVG 支持存在尺寸差异，统一使用同源 PNG。"""
    import pygame
    pygame.display.set_icon(pygame.image.load(str(APP_ICON_PATH)))
