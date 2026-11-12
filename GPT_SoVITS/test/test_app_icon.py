"""程序图标脱离启动目录仍能加载，Qt 与 SDL 使用同一份透明资源。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import Mock
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtWidgets import QApplication
from ui_qt import app_icon
from ui_qt.app_icon import APP_ICON_PATH, application_icon, set_pygame_window_icon


@pytest.fixture(scope="session")
def icon_app() -> QApplication:
    """测试期间持有应用，避免销毁其他模块共享的 Qt 配置对象。"""
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    ("platform_name", "for_dock", "expected_bounds"),
    [
        ("darwin", True, (44, 44, 424, 424)),
        ("darwin", False, (4, 4, 504, 504)),
        ("win32", True, (4, 4, 504, 504)),
    ],
)
def test_icon_loads_outside_project_without_cropping(
    icon_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    for_dock: bool,
    expected_bounds: tuple[int, int, int, int],
) -> None:
    """验证 Qt 与 SDL 使用相同图案范围，且仅 macOS Dock 使用较宽留边。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_icon, "platform", platform_name)
    assert APP_ICON_PATH.is_absolute()
    icon = application_icon(for_dock=for_dock)
    for size in (16, 32, 48, 256):
        pixmap = icon.pixmap(size, size)
        assert not pixmap.isNull()
        assert pixmap.width() == size and pixmap.height() == size
    image = icon.pixmap(512, 512).toImage()
    left = expected_bounds[0]
    assert image.pixelColor(left - 1, 256).alpha() == 0
    assert image.pixelColor(left, 256).alpha() > 0

    import pygame
    setter = Mock()
    monkeypatch.setattr(pygame.display, "set_icon", setter)
    set_pygame_window_icon(for_dock=for_dock)
    surface = setter.call_args.args[0]
    assert surface.get_size() == (512, 512)
    assert tuple(surface.get_bounding_rect()) == expected_bounds
