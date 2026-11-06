"""时间轴自然高度与小屏滚动回归，不连接真实舞台或存档。"""
import os
import sys
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtWidgets import QApplication, QWidget
from chat.chat import Chat, Message
from live2d_support.multi_char_resources import TheaterResources, audio_span
from ui_small_theater.multi_char_editor import MultiCharEditor
from ui_small_theater.multi_char_timeline import MultiCharTimeline


@pytest.fixture(scope="module")
def app():
    """共用离屏应用，只测 Qt 尺寸约束及滚动。"""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def timeline(app):
    """使用固定可读字体和空四轨，隔离窗口协调器。"""
    window = QWidget()
    font = QFont(window.font())
    font.setPixelSize(14)
    window.setFont(font)
    session = SimpleNamespace(
        names=["角色甲", "角色乙"], timeline_window=window, draft={"characters": {}},
        span=audio_span("", "测试台词"),
        resources={name: TheaterResources(None) for name in ("角色甲", "角色乙")},
    )
    view = MultiCharTimeline(session)
    yield view
    view.close()
    window.close()
    view.deleteLater()
    window.deleteLater()
    app.processEvents()


def test_intrinsic_height_is_independent_of_default_and_stretched_viewports(timeline, app):
    """未布局的 478px 视口和手动拉大只影响实际轨高，不能污染默认高度。"""
    view = timeline
    preferred = view.intrinsic_height()
    _, _, minimum_row, _, _ = view.font_layout_metrics()
    assert preferred < 250
    assert view.sizeHint().height() == preferred
    for height in (1200, 600, preferred, view.minimumHeight()):
        view.resize(1100, height)
        view.show()
        app.processEvents()
        view.rebuild()
        assert view.intrinsic_height() == preferred
        assert view.sizeHint().height() == preferred
        if height >= 600:
            assert view.row_height > minimum_row
            assert abs(view.sceneRect().height() - view.viewport().height()) <= 8


def test_intrinsic_height_scales_with_font_and_preserves_legible_tracks(timeline, app):
    """放大字体提高自然高度，四轨最低行高仍容纳正文和上下留白。"""
    heights = []
    for pixels in (14, 22, 36):
        font = QFont(timeline.session.timeline_window.font())
        font.setPixelSize(pixels)
        timeline.session.timeline_window.setFont(font)
        timeline.rebuild()
        timeline.resize(1300, timeline.minimumHeight())
        timeline.show()
        app.processEvents()
        metrics = QFontMetrics(font)
        assert timeline.row_height >= metrics.height() + 2 * timeline.row_padding
        assert len(timeline.rows) == 4
        heights.append(timeline.intrinsic_height())
    assert heights[0] < heights[1] < heights[2]


def test_small_viewport_keeps_four_tracks_accessible_by_vertical_scroll(timeline, app):
    """小屏只缩短视口，末轨可以滚动到完整显示，不删除或压扁四轨。"""
    timeline.resize(1100, timeline.minimumHeight())
    timeline.show()
    app.processEvents()
    assert timeline.minimumHeight() < timeline.intrinsic_height()
    assert len(timeline.rows) == 4
    scroll = timeline.verticalScrollBar()
    assert scroll.maximum() > 0
    scroll.setValue(scroll.maximum())
    app.processEvents()
    bottom = timeline.mapToScene(timeline.viewport().rect().bottomLeft()).y()
    assert bottom >= timeline.row_y(3) + timeline.row_height - 2
    scroll.setValue(0)
    assert timeline.mapToScene(timeline.viewport().rect().topLeft()).y() == 0


@pytest.fixture
def editor_factory(app, monkeypatch):
    """创建真实编排控件，屏蔽三窗自动移动以单独检查初始尺寸。"""
    monkeypatch.setattr("ui_small_theater.multi_char_editor.MultiCharWindowLayout", Mock())
    sessions = []

    def create():
        owner = QWidget()
        owner.to_live2d_change_character_queue = Queue()
        owner._save_chat = Mock(return_value=True)
        owner.performance_editor_finished = Mock()
        message = Message.from_dict({"character_name": "角色甲", "text": "测试台词"})
        chat = Chat(message_list=[message])
        slots = [{"character_name": name, "model_json_path": None} for name in ("角色甲", "角色乙")]
        editor = MultiCharEditor(owner, chat, message, slots)
        owner.performance_editor = editor
        sessions.append(editor)
        app.processEvents()
        return editor

    yield create
    for editor in sessions:
        editor.finish()
        editor.owner.close()
    app.processEvents()


@pytest.mark.parametrize("initial_height", [120, 478, 1200])
def test_editor_preferred_height_ignores_initial_viewport(editor_factory, monkeypatch, initial_height):
    """初始视口再高也只申请字体四轨加工具栏，不能再次挤占近半屏。"""
    initialize = MultiCharTimeline.__init__

    def initialize_large_view(view, *args, **kwargs):
        initialize(view, *args, **kwargs)
        view.resize(1100, initial_height)
        view.rebuild()

    monkeypatch.setattr(MultiCharTimeline, "__init__", initialize_large_view)
    editor = editor_factory()
    preferred = editor.timeline_window.property("preferredHeight")
    metrics = editor.timeline_window.fontMetrics()
    assert editor.view.intrinsic_height() < preferred < editor.view.intrinsic_height() + metrics.height() * 7
    assert preferred < 350
    editor.timeline_window.resize(1500, 700)
    editor.view.rebuild()
    editor.update_timeline_preferred_height()
    assert editor.timeline_window.property("preferredHeight") == preferred


def test_small_editor_window_retains_toolbar_and_scrolls_tracks(editor_factory, app):
    """压缩编排窗口时工具栏始终在窗口内，四轨通过内部纵滚访问。"""
    editor = editor_factory()
    window = editor.timeline_window
    window.resize(1600, window.minimumSizeHint().height())
    app.processEvents()
    assert window.height() < window.property("preferredHeight")
    assert window.contentsRect().contains(editor.timeline_toolbar.geometry())
    assert editor.save_button.isVisible() and editor.play.isVisible()
    assert len(editor.view.rows) == 4
    assert editor.view.verticalScrollBar().maximum() > 0
