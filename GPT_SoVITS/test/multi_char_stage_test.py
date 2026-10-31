"""小剧场舞台状态机回归：使用确定时钟与假纹理服务，不启动图形窗口。"""
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live2d_support.multi_char_scene import transition_from_total
from live2d_support.multi_char_stage import MultiCharStage


class Backgrounds:
    """记录提前加载、实际显示和回收时点，模拟有效与丢失的背景纹理。"""

    def __init__(self):
        """默认背景与其他已知文件分离，未加载文件只有 prepare 后才能显示。"""
        self.default_name = "default.png"
        self.current_name = self.default_name
        self.available = {"default.png", "day.jpg", "night.png", "rain.jpg"}
        self.textures = {self.default_name}
        self.prepared = []
        self.shown = []
        self.deleted = []

    def prepare(self, name):
        """为存在的文件建立假纹理，缺失文件和非法引用抛出加载错误。"""
        self.prepared.append(name)
        if name not in self.available:
            raise ValueError("无法解码背景")
        self.textures.add(name)

    def show(self, name):
        """只切换已加载的背景，及时发现提前回收了待展示纹理的错误。"""
        assert name in self.textures
        self.current_name = name
        self.shown.append(name)

    def prune(self, keep):
        """删除不再使用的纹理，并记录实际回收集合。"""
        self.deleted.extend(self.textures - keep)
        self.textures.intersection_update(keep)


@pytest.fixture
def stage():
    """建立双人舞台及可推进的测试时钟，不改变模型持久布局。"""
    clock = Mock(return_value=0.0)
    offsets = {"A": (-0.6, 0.1), "B": (0.65, -0.2)}
    return MultiCharStage(
        characters=lambda: ["A", "B"],
        offsets=lambda: offsets,
        backgrounds=Backgrounds(),
        reset=Mock(), report=Mock(), clock=clock,
    )


def tick_at(stage, milliseconds):
    """将时钟设置为本次阶段开始后的指定毫秒并推进一帧。"""
    stage.clock.return_value = stage.started + milliseconds / 1000
    stage.tick()


def target(stage, visible, background="default.png"):
    """提前准备目标背景并构造独立舞台目标。"""
    stage.backgrounds.prepare(background)
    return {"visible_characters": visible, "background": background}


def test_two_to_one_fades_and_centers_in_parallel_without_reset(stage):
    """退场与留场者居中同时持续 300ms，之后仍忙碌到 500ms 缓冲结束。"""
    offsets = deepcopy(stage.offsets())
    stage.begin(target(stage, ["A"]), {})
    assert stage.busy
    assert stage.visible("A") and not stage.visible("B")
    tick_at(stage, 150)
    assert stage.actors["A"] == pytest.approx((1.0, -0.3, 0.1))
    assert stage.actors["B"] == pytest.approx((0.5, 0.65, -0.2))
    tick_at(stage, 300)
    assert stage.actors["A"] == pytest.approx((1.0, 0.0, 0.1))
    assert stage.actors["B"][0] == 0
    assert stage.busy
    tick_at(stage, 499)
    assert stage.busy
    tick_at(stage, 500)
    assert not stage.busy
    stage.reset.assert_not_called()
    assert stage.offsets() == offsets


def test_one_to_two_resets_only_entrant_and_restores_kept_slot(stage):
    """重新入场者复位并在自身站位渐显，留场者移回保存的双人位置。"""
    stage.show(target(stage, ["A"]))
    stage.begin(target(stage, ["A", "B"]), {})
    stage.reset.assert_called_once_with("B")
    tick_at(stage, 150)
    assert stage.actors["A"] == pytest.approx((1.0, -0.3, 0.1))
    assert stage.actors["B"] == pytest.approx((0.5, 0.65, -0.2))
    tick_at(stage, 500)
    assert stage.actors["A"] == pytest.approx((1.0, -0.6, 0.1))
    assert stage.actors["B"] == pytest.approx((1.0, 0.65, -0.2))
    stage.reset.assert_called_once_with("B")


def test_single_a_to_b_uses_set_difference_and_keeps_exit_position(stage):
    """人数不变也执行 A 退场和 B 入场，双方都在单人中心渐变。"""
    stage.show(target(stage, ["A"]))
    stage.begin(target(stage, ["B"]), {})
    tick_at(stage, 150)
    assert stage.actors["A"] == pytest.approx((0.5, 0.0, 0.1))
    assert stage.actors["B"] == pytest.approx((0.5, 0.0, -0.2))
    stage.reset.assert_called_once_with("B")
    tick_at(stage, 300)
    assert stage.actors["A"][0] == 0
    assert stage.actors["B"] == pytest.approx((1.0, 0.0, -0.2))


def test_zero_actor_stage_preserves_identities_and_can_reenter_both(stage):
    """空舞台仍保留两个角色及原站位，之后两名角色并行渐显并各复位一次。"""
    stage.begin(target(stage, []), {})
    tick_at(stage, 150)
    assert [actor[0] for actor in stage.actors.values()] == [0.5, 0.5]
    tick_at(stage, 500)
    assert set(stage.actors) == {"A", "B"}
    assert not stage.visible("A") and not stage.visible("B")
    assert [actor[0] for actor in stage.actors.values()] == [0, 0]
    stage.reset.assert_not_called()
    stage.begin(target(stage, ["A", "B"]), {})
    assert [call.args for call in stage.reset.call_args_list] == [("A",), ("B",)]
    tick_at(stage, 150)
    assert [actor[0] for actor in stage.actors.values()] == pytest.approx([0.5, 0.5])
    tick_at(stage, 300)
    assert [actor[0] for actor in stage.actors.values()] == pytest.approx([1, 1])


def test_unchanged_stage_adds_buffer_without_reset_or_movement(stage):
    """没有角色或背景变化时仍保留 500ms，留场者的姿态不被复位。"""
    before = dict(stage.actors)
    stage.begin(target(stage, ["A", "B"]), {})
    tick_at(stage, 150)
    assert stage.actors == before
    assert stage.busy
    stage.reset.assert_not_called()
    tick_at(stage, 500)
    assert not stage.busy


def test_black_does_not_change_background_or_actors_before_full_black(stage):
    """先淡出到黑，再同时切背景和上下场，整个黑场仅用一个 2000ms 时钟。"""
    before = dict(stage.actors)
    stage.begin(target(stage, ["A"], "night.png"), {
        "transition": transition_from_total(),
    })
    assert stage.backgrounds.current_name == "default.png"
    assert "night.png" in stage.backgrounds.textures
    tick_at(stage, 300)
    assert stage.black == pytest.approx(0.5)
    assert stage.actors == before
    tick_at(stage, 599)
    assert stage.backgrounds.current_name == "default.png"
    assert stage.visible("B")
    tick_at(stage, 600)
    assert stage.black == 1
    assert stage.backgrounds.current_name == "night.png"
    assert not stage.visible("B")
    tick_at(stage, 750)
    assert stage.actors["A"] == pytest.approx((1.0, -0.3, 0.1))
    assert stage.actors["B"][0] == pytest.approx(0.5)
    tick_at(stage, 900)
    assert stage.actors["A"] == pytest.approx((1.0, 0.0, 0.1))
    assert stage.actors["B"][0] == 0
    tick_at(stage, 1700)
    assert stage.black == pytest.approx(0.5)
    assert stage.busy
    tick_at(stage, 2000)
    assert stage.black == 0
    assert not stage.busy
    assert stage.backgrounds.shown.count("night.png") == 1
    stage.reset.assert_not_called()


def test_short_black_keeps_its_timing_and_finishes_actor_changes_in_parallel(stage):
    """最短黑场保留 200ms 全黑，150～450ms 上下场与末段淡入并行完成。"""
    stage.begin(target(stage, ["A"]), {"transition": transition_from_total(500)})
    tick_at(stage, 149)
    assert stage.visible("B")
    tick_at(stage, 300)
    assert stage.black == 1
    assert stage.actors["B"][0] == pytest.approx(0.5)
    tick_at(stage, 350)
    assert stage.black == 1
    assert stage.actors["B"][0] > 0
    tick_at(stage, 450)
    assert stage.actors["B"][0] == pytest.approx(0)
    assert stage.actors["A"][1] == pytest.approx(0)
    assert 0 < stage.black < 1
    tick_at(stage, 500)
    assert stage.black == 0
    assert not stage.busy


def test_first_sentence_applies_directly_even_if_storage_contains_black(stage):
    """首句舞台直接呈现，不增加黑场或 500ms 的等待。"""
    stage.begin(target(stage, [], "day.jpg"), {
        "transition": transition_from_total(),
    }, first=True)
    assert not stage.busy
    assert stage.black == 0
    assert stage.backgrounds.current_name == "day.jpg"
    assert [actor[0] for actor in stage.actors.values()] == [0, 0]


def test_frame_lag_finishes_stage_once_without_replaying_entries(stage):
    """一次卡顿跨过整个过渡后仍仅入场一次，并落到正确目标背景和站位。"""
    stage.show(target(stage, []))
    stage.begin(target(stage, ["B"], "rain.jpg"), {
        "transition": transition_from_total(),
    })
    tick_at(stage, 3500)
    assert not stage.busy
    assert stage.black == 0
    assert stage.actors["B"] == pytest.approx((1.0, 0.0, -0.2))
    assert stage.backgrounds.current_name == "rain.jpg"
    stage.reset.assert_called_once_with("B")
    tick_at(stage, 4000)
    stage.reset.assert_called_once_with("B")


def test_cancel_clears_black_and_new_target_is_applied_without_old_transition(stage):
    """修改预览场景时取消黑场，再显示新目标，不留下旧时钟或黑幕。"""
    stage.begin(target(stage, ["A"], "night.png"), {
        "transition": transition_from_total(),
    })
    tick_at(stage, 1000)
    assert stage.black == 1
    stage.cancel()
    assert not stage.busy
    assert stage.black == 0
    stage.show(target(stage, ["B"], "day.jpg"))
    tick_at(stage, 5000)
    assert stage.state == {"visible_characters": ["B"], "background": "day.jpg"}
    assert stage.actors["B"] == pytest.approx((1.0, 0.0, -0.2))
    assert stage.black == 0


def test_editor_restores_actual_stage_and_retains_snapshot_across_sentence_changes(stage):
    """编辑起点用程序默认背景，退出恢复进入前的舞台且切句不能重写快照。"""
    stage.show(target(stage, ["A"], "night.png"))
    saved_state = deepcopy(stage.state)
    saved_actors = dict(stage.actors)
    stage.enter_editor()
    stage.show(stage.resolve([{}], 0))
    assert stage.backgrounds.current_name == "default.png"
    assert "night.png" in stage.backgrounds.textures
    stage.enter_editor()
    stage.show(target(stage, [], "rain.jpg"))
    assert "night.png" in stage.backgrounds.textures
    stage.exit_editor()
    assert stage.saved is None
    assert stage.state == saved_state
    assert stage.actors == saved_actors
    assert stage.backgrounds.current_name == "night.png"
    assert stage.backgrounds.textures == {"default.png", "night.png"}
    assert "rain.jpg" in stage.backgrounds.deleted


def test_new_playback_restarts_base_instead_of_using_last_scene(stage):
    """下次播放不会把上次剧本结尾的人物或背景当作默认起点。"""
    stage.show(target(stage, [], "night.png"))
    stage.new_playback()
    assert stage.state == {"visible_characters": ["A", "B"], "background": "default.png"}
    assert stage.actors["A"] == pytest.approx((1.0, -0.6, 0.1))
    assert stage.actors["B"] == pytest.approx((1.0, 0.65, -0.2))
    assert stage.backgrounds.textures == {"default.png"}


def test_resolve_target_folds_context_without_replaying_previous_changes(stage):
    """单句定位合并上文场景，只预载最终背景，不提前切换实际舞台或触发入场。"""
    messages = [
        {"scene_before": {"visible_characters": ["A"], "background": "day.jpg"}},
        {"scene_before": {"visible_characters": [], "background": "night.png"}},
        {},
    ]
    saved = deepcopy(messages)
    resolved = stage.resolve(messages, 2)
    assert resolved == {"visible_characters": [], "background": "night.png"}
    assert stage.backgrounds.current_name == "default.png"
    assert stage.backgrounds.prepared == ["night.png"]
    stage.reset.assert_not_called()
    stage.show(resolved)
    assert stage.backgrounds.current_name == "night.png"
    assert messages == saved


def test_missing_background_falls_back_to_last_loadable_override(stage):
    """中途回放背景丢失时，回退到上文最后有效背景，与逐句完整播放一致。"""
    messages = [
        {"scene_before": {"background": "day.jpg", "visible_characters": ["A"]}},
        {"scene_before": {"background": "missing.png"}},
        {},
    ]
    saved = deepcopy(messages)
    resolved = stage.resolve(messages, 2)
    assert resolved == {"visible_characters": ["A"], "background": "day.jpg"}
    assert stage.backgrounds.prepared == ["missing.png", "day.jpg"]
    stage.report.assert_called_once()
    assert "missing.png" in stage.report.call_args.args[0]
    assert stage.backgrounds.current_name == "default.png"
    assert messages == saved


def test_missing_background_overrides_fall_back_to_base_not_previous_actual_stage(stage):
    """目标句没有可加载的上文背景时回到固定基底，不遗留此前预览的背景。"""
    stage.show(target(stage, ["A"], "night.png"))
    resolved = stage.resolve([
        {"scene_before": {"background": "gone1.png"}},
        {"scene_before": {"background": "gone2.png"}},
    ], 1)
    assert resolved["background"] == "default.png"
    stage.show(resolved)
    assert stage.backgrounds.current_name == "default.png"


def test_background_resolve_handles_invalid_saved_shapes_without_rewriting(stage):
    """未知存档结构不会中断背景回溯，Message 及 IPC 字典可共同解析。"""
    messages = [
        SimpleNamespace(scene_before={"background": "day.jpg"}),
        {"scene_before": ["invalid"]},
        {"scene_before": {"background": {"not": "filename"}}},
        {"scene_before": {"background": "missing.png"}},
    ]
    resolved = stage.resolve(messages, 3)
    assert resolved["background"] == "day.jpg"
    stage.report.assert_called_once()


def test_backgrounds_are_retained_during_fade_and_recycled_after_transition(stage):
    """旧背景保留到切换结束，后续回收不保留无关历史纹理或删除当前纹理。"""
    stage.show(target(stage, ["A", "B"], "day.jpg"))
    stage.begin(target(stage, ["A"], "night.png"), {
        "transition": transition_from_total(),
    })
    tick_at(stage, 300)
    assert stage.backgrounds.textures == {"default.png", "day.jpg", "night.png"}
    tick_at(stage, 750)
    assert stage.backgrounds.current_name == "night.png"
    assert "day.jpg" in stage.backgrounds.textures
    tick_at(stage, 2000)
    assert stage.backgrounds.textures == {"default.png", "night.png"}
    assert "day.jpg" in stage.backgrounds.deleted
