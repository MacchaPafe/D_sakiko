"""编排工作区的外框约束、Qt 最小尺寸与屏幕变化回归。"""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtCore import QPoint, QRect, QSize, Qt
from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget
from ui_small_theater.multi_char_windows import MultiCharWindowLayout, workspace_rects
import ui_small_theater.multi_char_windows as windows


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("available", [QRect(0, 0, 1920, 1040), QRect(-1920, -200, 1920, 1040)])
def test_workspace_moves_normal_windows_only_as_far_as_needed(available):
    left, top = available.left(), available.top()
    stage = QRect(left + 100, top + 300, 800, 700)
    control = QRect(left + 900, top + 300, 600, 700)
    stage, control, timeline = workspace_rects(stage, control, available, 280, gap=8)
    assert stage.height() == control.height() == 700
    assert control.top() == available.bottom() + 1 - 700 - 8 - 280
    assert timeline.top() == control.bottom() + 1 + 8
    assert stage.right() + 1 == control.left()
    assert timeline.left() == stage.left() and timeline.right() == control.right()
    assert all(available.contains(rect) for rect in (stage, control, timeline))


def test_resource_minimum_gets_space_before_shrinking_timeline_below_toolbar():
    available = QRect(0, 0, 1280, 760)
    stage = QRect(0, 150, 640, 620)
    control = QRect(640, 150, 500, 620)
    stage, control, timeline = workspace_rects(
        stage, control, available, 320,
        minimum_control=QSize(500, 520), minimum_timeline_height=180,
    )
    assert control.width() >= 500 and control.height() >= 520
    assert 180 <= timeline.height() <= 240
    assert stage.bottom() < timeline.top() and control.bottom() < timeline.top()
    assert all(available.contains(rect) for rect in (stage, control, timeline))


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_native_coordinate_conversion_round_trips_on_negative_origin_monitor(scale):
    layout = object.__new__(MultiCharWindowLayout)
    origin, physical = QPoint(-1707, -200), (-2560, -300)
    layout.coordinates = lambda: (origin, physical, scale)
    logical = QRect(-1603, -140, 801, 523)
    native = layout.to_native(logical)
    assert native[:2] == (round(104 * scale - 2560), round(60 * scale - 300))
    restored = layout.to_logical(native)
    assert all(abs(a - b) <= 1 for a, b in zip(logical.getRect(), restored.getRect()))


def test_visible_outer_target_is_converted_to_client_geometry_once(monkeypatch):
    """透明缩放边缘应只补一次，标题栏仍包含在目标可见外框内。"""
    frame = QRect(100, 100, 416, 438)
    client = QRect(108, 130, 400, 400)
    visible = QRect(107, 100, 402, 431)
    window = SimpleNamespace(frameGeometry=lambda: frame, geometry=lambda: client, setGeometry=Mock())
    monkeypatch.setattr(windows, "visible_geometry", lambda _: visible)
    windows.set_outer_geometry(window, QRect(500, 50, 402, 431))
    window.setGeometry.assert_called_once_with(QRect(501, 80, 400, 400))


@pytest.fixture
def workspace(app, monkeypatch):
    """真实 QWidget 执行最小尺寸钳制，仅隔离原生舞台与屏幕枚举。"""
    owner, resources, timeline = QWidget(), QWidget(), QWidget(None, Qt.FramelessWindowHint)
    screen = SimpleNamespace(availableGeometry=lambda: QRect(screen.available))
    screen.available = QRect(0, 0, 1280, 760)
    monkeypatch.setattr(resources, "screen", lambda: screen)
    resources.setMinimumSize(420, 480)
    resources.setGeometry(600, 150, 480, 560)
    owner.setGeometry(600, 150, 480, 560)
    timeline.setMinimumHeight(160)
    timeline.setProperty("preferredHeight", 300)
    timeline.resize(1100, 440)
    for window in (owner, resources, timeline):
        window.show()
    app.processEvents()
    sent = Mock()
    layout = MultiCharWindowLayout(owner, resources, timeline, sent)
    layout.stage_width = 500
    layout.ready = True
    monkeypatch.setattr(layout, "read_native", lambda: None)
    yield SimpleNamespace(layout=layout, owner=owner, resources=resources,
                          timeline=timeline, screen=screen, sent=sent)
    layout.closed = True
    layout.timer.stop()
    for window in (owner, resources, timeline):
        window.close()
    app.processEvents()


def assert_workspace_fits(workspace):
    resource_frame = windows.visible_geometry(workspace.resources)
    timeline = workspace.timeline.geometry()
    stage = QRect(*workspace.sent.call_args.kwargs["geometry"])
    available = workspace.screen.available
    assert resource_frame.bottom() < timeline.top()
    assert stage.bottom() < timeline.top()
    assert all(available.contains(rect) for rect in (resource_frame, stage, timeline))
    assert timeline.height() >= workspace.timeline.minimumHeight()


def test_follow_respects_actual_qt_minimum_and_does_not_reuse_oversized_timeline(workspace, app):
    workspace.layout.follow()
    app.processEvents()
    assert_workspace_fits(workspace)
    assert workspace.timeline.height() <= workspace.timeline.property("preferredHeight")
    assert workspace.resources.height() >= 480
    old = (windows.visible_geometry(workspace.resources), workspace.timeline.geometry())
    workspace.layout.follow()
    assert old == (windows.visible_geometry(workspace.resources), workspace.timeline.geometry())


def test_follow_reflows_when_available_area_changes_without_resource_move(workspace, app):
    workspace.screen.available = QRect(0, 0, 1280, 1040)
    workspace.resources.setGeometry(600, 100, 480, 580)
    workspace.layout.follow()
    app.processEvents()
    frame_before = windows.visible_geometry(workspace.resources)
    timeline_before = workspace.timeline.geometry()
    assert frame_before == workspace.layout.previous_rect
    workspace.screen.available = QRect(0, 0, 1280, 740)
    workspace.layout.follow()
    app.processEvents()
    assert workspace.timeline.geometry() != timeline_before
    assert_workspace_fits(workspace)


def test_follow_reserves_child_layout_minimum_hint_in_outer_frame(workspace, app):
    root = QVBoxLayout(workspace.resources)
    panel = QWidget()
    panel.setMinimumHeight(520)
    root.addWidget(panel)
    root.activate()
    app.processEvents()
    assert workspace.resources.minimumSizeHint().height() > 520
    workspace.layout.follow()
    app.processEvents()
    assert_workspace_fits(workspace)
    assert panel.height() >= 520


def test_bind_uses_content_height_without_screen_percentage_floor(workspace, app):
    workspace.resources.setMinimumSize(100, 100)
    workspace.timeline.setMinimumHeight(80)
    workspace.timeline.setProperty("preferredHeight", 100)
    workspace.layout.original_frame = QRect(300, 80, 300, 260)
    workspace.layout.ready = False
    workspace.layout.bind(None, geometry=[0, 80, 300, 260])
    app.processEvents()
    workspace.layout.timer.stop()
    assert workspace.timeline.height() == 100
    assert windows.visible_geometry(workspace.resources).bottom() < workspace.timeline.geometry().top()
