"""程序图标脱离启动目录仍能加载，Qt 与 SDL 使用同一份透明资源。"""
import os
import sys
from pathlib import Path
from unittest.mock import Mock
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtWidgets import QApplication
from ui_qt.app_icon import APP_ICON_PATH, application_icon, set_pygame_window_icon


@pytest.fixture(scope="session")
def icon_app():
    """测试期间持有应用，避免销毁其他模块共享的 Qt 配置对象。"""
    return QApplication.instance() or QApplication([])


def test_icon_loads_outside_project_without_cropping(icon_app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert APP_ICON_PATH.is_absolute()
    icon = application_icon()
    for size in (16, 32, 48, 256):
        pixmap = icon.pixmap(size, size)
        assert not pixmap.isNull()
        assert pixmap.width() == size and pixmap.height() == size
    import pygame
    setter = Mock()
    monkeypatch.setattr(pygame.display, "set_icon", setter)
    set_pygame_window_icon()
    surface = setter.call_args.args[0]
    assert surface.get_size() == (512, 512)
    assert surface.get_at((0, 0)).a == 0
    # 原 SVG 直接交给 SDL 会缩成左上 120px，派生 PNG 应铺满整个图标。
    assert surface.get_at((256, 400)).a == 255
