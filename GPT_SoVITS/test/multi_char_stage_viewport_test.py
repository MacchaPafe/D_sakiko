"""执行真实舞台闭包和事件分支，验证可缩放窗口的绘制与鼠标坐标一致。"""
import ast
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live2d_support.layout import Live2DLayout


@pytest.fixture(scope="module")
def stage_code():
    """仅抽取生产函数及事件分支，不启动 SDL、OpenGL 或真实模型。"""
    source = Path(__file__).resolve().parents[1] / "multi_char_live2d_module.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    player = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "play_live2d")
    functions = [node for node in player.body if isinstance(node, ast.FunctionDef)
                 and node.name in ("stage_viewport", "slot_from_mouse_x")]
    assert len(functions) == 2
    set_mode = next(node for node in player.body if isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "set_mode")
    viewport = next(node for node in ast.walk(player) if isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "glViewport")
    drag = next(node for node in ast.walk(player) if isinstance(node, ast.If)
                and any(isinstance(part, ast.Attribute) and part.attr == "MOUSEMOTION"
                        for part in ast.walk(node.test)))
    def compiled(nodes):
        return compile(ast.Module(body=deepcopy(nodes), type_ignores=[]), str(source), "exec")
    return SimpleNamespace(functions=compiled(functions), set_mode=compiled([set_mode]),
                           viewport=compiled([viewport]), drag=compiled([drag]))


@pytest.fixture
def stage(stage_code):
    display = SimpleNamespace(get_window_size=Mock(return_value=(1064, 800)), set_mode=Mock())
    pygame = SimpleNamespace(display=display, RESIZABLE=16, MOUSEMOTION=1, MOUSEWHEEL=2)
    context = dict(pygame=pygame, display=(1064, 800), DOUBLEBUF=2, OPENGL=4,
                   glViewport=Mock())
    exec(stage_code.functions, context)
    return SimpleNamespace(context=context, display=display, code=stage_code)


def test_window_creation_passes_resizable_without_losing_opengl_or_double_buffer(stage):
    exec(stage.code.set_mode, stage.context)
    stage.display.set_mode.assert_called_once_with((1064, 800), 16 | 4 | 2)


@pytest.mark.parametrize("window,expected", [
    ((1064, 800), (0, 0, 1064, 800)),
    ((1400, 800), (168, 0, 1064, 800)),
    ((1064, 1100), (0, 150, 1064, 800)),
    ((532, 400), (0, 0, 532, 400)),
    ((1000, 500), (167, 0, 665, 500)),
    ((500, 1000), (0, 312, 500, 376)),
    ((1001, 501), (167, 0, 666, 501)),
])
def test_draw_uses_centered_aspect_preserving_viewport_after_resize(stage, window, expected):
    stage.display.get_window_size.return_value = window
    exec(stage.code.viewport, stage.context)
    stage.context["glViewport"].assert_called_once_with(*expected)
    x, y, width, height = expected
    assert abs(window[0] - width - 2 * x) <= 1
    assert abs(window[1] - height - 2 * y) <= 1
    assert abs(width - height * 1.33) <= 1


def test_slot_selection_tracks_current_width_across_resizes(stage):
    select = stage.context["slot_from_mouse_x"]
    assert select(350) == 0
    stage.display.get_window_size.return_value = (600, 800)
    assert select(350) == 1
    assert select(299) == 0 and select(300) == 1
    stage.display.get_window_size.return_value = (1400, 800)
    assert select(600) == 0
    assert select(699) == 0 and select(700) == 1


@pytest.mark.parametrize("window,delta,expected", [
    ((1064, 800), (266, 200), (0.5, -0.5)),
    ((532, 400), (133, 100), (0.5, -0.5)),
    ((1000, 500), (133, 100), (0.4, -0.4)),
    ((500, 1000), (100, 94), (0.4, -0.5)),
])
def test_drag_uses_rendered_pixel_size_including_letterbox(stage, window, delta, expected):
    stage.display.get_window_size.return_value = window
    context = stage.context
    context.update(
        event=SimpleNamespace(type=1, pos=(200 + delta[0], 200 + delta[1])),
        layout_editing=True, layout_dragging=True, layout_last_mouse_pos=(200, 200),
        layout_selected_slot=1, layout_dirty_slots=set(),
        model_layouts=[Live2DLayout(1.0, 0, 0), Live2DLayout(1.0, 0, 0)],
        apply_slot_layout=Mock(), show_layout_edit_overlay=Mock(),
    )
    exec(stage.code.drag, context)
    actual = context["model_layouts"][1]
    assert (actual.offset_x, actual.offset_y) == pytest.approx(expected)
    assert actual.scale == 1.0
    assert context["model_layouts"][0] == Live2DLayout(1.0, 0, 0)
    context["apply_slot_layout"].assert_called_once_with(1)
    assert context["layout_dirty_slots"] == {1}
    assert context["layout_last_mouse_pos"] == context["event"].pos


def test_drag_reads_new_size_for_each_event_without_a_new_editor_session(stage):
    context = stage.context
    context.update(
        event=SimpleNamespace(type=1, pos=(233, 200)),
        layout_editing=True, layout_dragging=True, layout_last_mouse_pos=(100, 100),
        layout_selected_slot=0, layout_dirty_slots=set(),
        model_layouts=[Live2DLayout(1.0, 0, 0), Live2DLayout(1.0, 0, 0)],
        apply_slot_layout=Mock(), show_layout_edit_overlay=Mock(),
    )
    exec(stage.code.drag, context)
    assert context["model_layouts"][0].offset_x == pytest.approx(0.25)
    assert context["model_layouts"][0].offset_y == pytest.approx(-0.25)
    stage.display.get_window_size.return_value = (532, 400)
    context["event"] = SimpleNamespace(type=1, pos=(366, 300))
    exec(stage.code.drag, context)
    assert context["model_layouts"][0].offset_x == pytest.approx(0.75)
    assert context["model_layouts"][0].offset_y == pytest.approx(-0.75)
