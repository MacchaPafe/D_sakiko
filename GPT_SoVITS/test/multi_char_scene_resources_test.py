"""场景编排的静音、隐藏事件及不打断播放的资源刷新测试。"""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live2d_support.multi_char_performance import MultiCharPerformance, TimelineCursor


def motion(at_ms, name="nod"):
    """生成带唯一资源引用的时间点，便于区分已跳过与新到期事件。"""
    return {"at_ms": at_ms, "resource": {"logical_id": name, "file_name": name + "_C.motion3.json"}}


def model_fixture(directory, name):
    """在临时目录创建可由真实目录解析器读取的 V3 资源清单。"""
    directory.mkdir()
    motion_path = directory / "nod_C.motion3.json"
    motion_path.write_text(json.dumps({"Meta": {"Duration": 1.2, "Loop": True}}), encoding="utf-8")
    expression_path = directory / "smile.exp3.json"
    expression_path.write_text(json.dumps({"Type": "Live2D Expression", "Parameters": []}), encoding="utf-8")
    path = directory / "model.model3.json"
    path.write_text(json.dumps({"FileReferences": {
        "Motions": {"normal": [{"File": motion_path.name}]},
        "Expressions": [{"Name": "smile", "File": expression_path.name}],
    }}), encoding="utf-8")
    native = Mock()
    native.LoadExtraMotion.side_effect = range(100)
    return SimpleNamespace(
        name=name, model_json_path=str(path), version="v3", model=native,
        _performance_generation=0, _default_expression_active=False,
        performance_expression_active=False, _custom_expression_active=False,
        default_expression_id="smile", _restore_default_expression=Mock(),
        _start_performance_motion=Mock(return_value=True),
        SetExpression=Mock(return_value=True), StartRandomMotion=Mock(),
    )


@pytest.fixture
def player(tmp_path, monkeypatch):
    """使用真实资源解析、可控时钟与原生模型替身搭建双人播放器。"""
    models = [model_fixture(tmp_path / name, name) for name in ("A", "B")]
    slots = [{"character_name": model.name} for model in models]
    clock = [10.0]
    monkeypatch.setattr("live2d_support.multi_char_performance.time.monotonic", lambda: clock[0])
    music = Mock()
    music.get_pos.return_value = -1
    engine = MultiCharPerformance(lambda: models, lambda: slots, Mock(), music)
    engine.preview = True
    engine.session = "editor"
    engine.reset_all = Mock()
    engine.reset = Mock()
    engine.test_clock = clock
    return engine


def test_muted_audio_uses_real_duration_without_playing_or_mouth(player, tmp_path):
    """有效音频静音后仍按文件计时，播放与口型采样都不启动。"""
    import soundfile as sf
    audio = tmp_path / "voice.wav"
    sf.write(str(audio), [0.25] * 9600, 8000)
    turn = {"character_name": "A", "text": "短句", "audio_path": str(audio), "muted": True,
            "performance_timeline": {"characters": {"A": {"motions": [motion(1100)]}}}}
    player.music.get_pos.return_value = 8000
    player.command({"action": "play", "session": "editor", "turn": turn, "position": 200})
    assert player.span.path == str(audio)
    assert player.span.duration_ms == 1200
    player.test_clock[0] += 0.9
    assert player.position() == 1100
    player.tick()
    player.bindings()["A"]._start_performance_motion.assert_called_once()
    assert player.playing
    assert player.mouth() is None
    player.music.load.assert_not_called()
    player.music.play.assert_not_called()
    player.music.get_pos.assert_not_called()
    player.test_clock[0] += 0.1
    player.tick()
    assert not player.playing
    assert player.offset == 1200
    assert turn["audio_path"] == str(audio)


def test_hidden_events_are_consumed_and_not_replayed_on_entry(player):
    """隐藏角色的到期事件只消费游标；重新上场后只播放后续新事件。"""
    visible = set()
    player.visible = visible.__contains__
    timeline = {"characters": {"A": {
        "motions": [motion(0), motion(100), motion(300)],
        "expressions": [{"at_ms": 50, "resource": {"logical_id": "smile"}},
                        {"at_ms": 300, "resource": {"logical_id": "smile"}}],
    }}}
    original = copy.deepcopy(timeline)
    player.begin({"text": "测试无声台词", "performance_timeline": timeline})
    player.test_clock[0] += 0.2
    player.tick()
    model = player.bindings()["A"]
    model._start_performance_motion.assert_not_called()
    model.SetExpression.assert_not_called()
    assert player.cursor.positions[("A", "motions")] == 2
    assert player.cursor.positions[("A", "expressions")] == 1
    visible.add("A")
    player.tick()
    model._start_performance_motion.assert_not_called()
    model.SetExpression.assert_not_called()
    player.test_clock[0] += 0.1
    player.tick()
    model._start_performance_motion.assert_called_once()
    model.SetExpression.assert_called_once_with("smile")
    assert timeline == original


@pytest.mark.parametrize("paused", [False, True])
def test_refresh_is_targeted_and_preserves_preview_state(player, paused):
    """刷新只失效当前角色目录，播放、暂停、游标、姿势和另一个角色均保留。"""
    catalog_a = player.catalog("A")
    catalog_b = player.catalog("B")
    assert catalog_a.duration({"logical_id": "nod"}) == 1200
    path = catalog_a.path.parent / "nod_C.motion3.json"
    path.write_text(json.dumps({"Meta": {"Duration": 2.5}}), encoding="utf-8")
    player.cursor = TimelineCursor({"characters": {"A": {"motions": [motion(700)]}}}, 5000)
    player.playing = True
    player.paused = paused
    player.offset = 600
    player.pause_position = 612
    player.started = 8.0
    player.handoff.add("A")
    player.expression_set.add("B")
    before = (player.cursor, player.playing, player.paused, player.offset, player.pause_position, player.started)
    player.command({"action": "refresh", "session": "editor", "character": "A"})
    assert (player.cursor, player.playing, player.paused, player.offset,
            player.pause_position, player.started) == before
    assert player.catalog("B") is catalog_b
    assert player.catalog("A") is not catalog_a
    assert player.catalog("A").duration({"logical_id": "nod"}) == 2500
    assert player.handoff == {"A"}
    assert player.expression_set == {"B"}
    player.reset.assert_not_called()
    player.reset_all.assert_not_called()
    assert player.music.mock_calls == []
    for model in player.bindings().values():
        assert model.model.mock_calls == []


def test_refresh_loads_new_content_on_next_event_only(player):
    """刷新时不加载原生文件，下次事件使用新版内容，相同内容重复触发复用版本。"""
    model = player.bindings()["A"]
    player.event("A", "motions", motion(0))
    model._start_performance_motion.assert_called_once()
    model.model.LoadExtraMotion.assert_not_called()
    player.command({"action": "refresh", "session": "editor", "character": "A"})
    model.model.LoadExtraMotion.assert_not_called()
    player.event("A", "motions", motion(100))
    assert model.model.LoadExtraMotion.call_count == 1
    first_index = model.model.StartMotion.call_args.args[1]
    player.event("A", "motions", motion(200))
    assert model.model.LoadExtraMotion.call_count == 1
    assert model.model.StartMotion.call_args.args[1] == first_index
    path = Path(model.model_json_path).parent / "nod_C.motion3.json"
    path.write_text(json.dumps({"Meta": {"Duration": 3.0}}), encoding="utf-8")
    player.command({"action": "refresh", "session": "editor", "character": "A"})
    assert model.model.LoadExtraMotion.call_count == 1
    player.event("A", "motions", motion(300))
    assert model.model.LoadExtraMotion.call_count == 2
    assert model.model.StartMotion.call_args.args[1] != first_index
    assert len(player.refreshed_playback.versions) == 2
    model.model.StopAllMotions.assert_not_called()


def test_expression_refresh_adds_versions_without_resetting_active_expression(player):
    """表情内容改变时追加新原生 ID，不清理仍被播放管理器引用的旧版本。"""
    model = player.bindings()["A"]
    item = {"at_ms": 0, "resource": {"logical_id": "smile"}}
    player.command({"action": "refresh", "session": "editor", "character": "A"})
    player.event("A", "expressions", item)
    first_id = model.model.SetExpression.call_args.args[0]
    player.event("A", "expressions", item)
    assert model.model.LoadExtraExpression.call_count == 1
    path = Path(model.model_json_path).parent / "smile.exp3.json"
    path.write_text(json.dumps({"Type": "Live2D Expression", "Parameters": [], "FadeInTime": 0.1}),
                    encoding="utf-8")
    player.command({"action": "refresh", "session": "editor", "character": "A"})
    player.event("A", "expressions", item)
    assert model.model.LoadExtraExpression.call_count == 2
    assert model.model.SetExpression.call_args.args[0] != first_id
    model.model.ResetExpressions.assert_not_called()
    assert model.performance_expression_active


@pytest.mark.parametrize("action", ["scene", "transition"])
def test_scene_preview_cancels_clock_but_preserves_pose_and_playhead(player, action):
    """句前设置中断台词调度时不清人物姿势，报告原来所选的时间轴位置。"""
    player.playing = True
    player.cursor = TimelineCursor({"characters": {"A": {"motions": [motion(0)]}}}, 5000)
    player.command({"action": action, "session": "editor", "position": 876})
    assert not player.playing
    assert player.cursor is None
    assert player.offset == 876
    player.reset_all.assert_not_called()
    player.reset.assert_not_called()
    player.music.stop.assert_called_once()
    player.tick()
    assert player.send.call_args.args[0]["position"] == 876
    assert not player.send.call_args.args[0]["playing"]


def test_removed_resource_after_refresh_skips_without_changing_timeline(player):
    """文件被移走后只跳过后续触发，不清除编排引用或重置人物状态。"""
    catalog = player.catalog("A")
    source = motion(300)
    original = copy.deepcopy(source)
    (catalog.path.parent / "nod_C.motion3.json").unlink()
    player.command({"action": "refresh", "session": "editor", "character": "A"})
    player.event("A", "motions", source)
    player.bindings()["A"].model.StartMotion.assert_not_called()
    player.reset.assert_not_called()
    assert source == original
