"""隔离执行真实小剧场调度循环，验证句内与句前时钟以及静音、画外音边界。"""
import ast
import logging
import sys
from collections import deque
from copy import deepcopy
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live2d_support.multi_char_performance import MultiCharPerformance
from live2d_support.multi_char_resources import audio_span
from live2d_support.multi_char_scene import transition_from_total
from live2d_support.multi_char_stage import MultiCharStage


class Clock:
    """为真实调度器提供同一个可控单调时钟。"""

    def __init__(self):
        """从零秒开始，测试只在边界帧推进。"""
        self.now = 0.0

    def __call__(self):
        """读取当前秒数。"""
        return self.now


class Music:
    """以真实文件时长模拟 pygame 音频通道，不初始化音频设备。"""

    def __init__(self, clock):
        """保存时钟及空闲音频状态。"""
        self.clock = clock
        self.started = 0.0
        self.duration = 0
        self.active = False
        self.played = []

    def start(self, path):
        """有效文件记录播放起点，未配置音频保持空闲。"""
        self.stop()
        span = audio_span(path, "")
        if not span.path:
            return False
        self.started = self.clock()
        self.duration = span.duration_ms
        self.active = True
        self.played.append((self.started, path))
        return True

    def stop(self):
        """停止本句语音。"""
        self.active = False

    def get_busy(self):
        """有效音频在自己的完整时长内报告忙碌。"""
        return self.active and (self.clock() - self.started) * 1000 < self.duration

    def get_pos(self):
        """模拟 pygame 的音频进度以及未播放时的负一返回值。"""
        if not self.get_busy():
            return -1
        return round((self.clock() - self.started) * 1000)


class ClosureGlobals(ast.NodeTransformer):
    """把抽取函数中的闭包绑定指向测试命名空间，不改写任何业务分支。"""

    def visit_Nonlocal(self, node):
        """保留相同绑定名，以 global 在隔离执行时共享调度状态。"""
        return ast.copy_location(ast.Global(names=node.names), node)


def scheduler_code():
    """从实际播放函数抽取调度与入口函数，避免测试重写一份类似的状态机。"""
    source = Path(__file__).resolve().parents[1] / "multi_char_live2d_module.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    player = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "play_live2d")
    names = {"begin_playlist", "begin_sentence", "is_dialogue_idle", "show_preview_scene"}
    functions = [node for node in player.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(functions) == len(names)
    loop = next(node for node in player.body if isinstance(node, ast.While))
    start = next(index for index, node in enumerate(loop.body)
                 if isinstance(node, ast.Expr) and ast.unparse(node) == "stage.tick()")
    end = next(index for index, node in enumerate(loop.body)
               if isinstance(node, ast.If) and "idle_recover_timer" in ast.unparse(node.test))
    definitions = ast.Module(body=deepcopy(functions), type_ignores=[])
    definitions = ast.fix_missing_locations(ClosureGlobals().visit(definitions))
    frame = ast.Module(body=deepcopy(loop.body[start:end]), type_ignores=[])
    return compile(definitions, str(source), "exec"), compile(frame, str(source), "exec")


class Playback:
    """执行真实句前舞台与真实句内调度，替换音频和 OpenGL 宿主依赖。"""

    def __init__(self, monkeypatch):
        """建立模型替身、真实时间轴引擎和从主循环抽取的单帧代码。"""
        self.clock = Clock()
        monkeypatch.setattr("live2d_support.multi_char_performance.time.monotonic", self.clock)
        self.music = Music(self.clock)
        self.models = [Mock(version="v3", _performance_generation=0) for _ in range(2)]
        slots = [{"character_name": name} for name in ("A", "B")]
        self.sent = []
        sender = SimpleNamespace(put=self.record)
        self.performance = MultiCharPerformance(lambda: self.models, lambda: slots, self.record, self.music)
        backgrounds = SimpleNamespace(
            current_name="default.png", default_name="default.png",
            prepare=Mock(), show=Mock(), prune=Mock(),
        )
        self.stage = MultiCharStage(
            lambda: ["A", "B"], lambda: {"A": (-0.6, 0), "B": (0.6, 0)},
            backgrounds, self.performance.reset, Mock(), self.clock,
        )
        self.performance.visible = self.stage.visible
        host = SimpleNamespace(
            playlist=[], playlist_pointer=0, performance_round=0,
            active_slots=slots, _start_turn_audio=Mock(side_effect=self.music.start),
            _clear_eye_reopen_state=Mock(), _try_start_emotion_motion=Mock(),
        )
        self.context = {
            "self": host, "stage": self.stage, "performance": self.performance,
            "pygame": SimpleNamespace(mixer=SimpleNamespace(music=self.music)),
            "time": SimpleNamespace(time=self.clock, monotonic=self.clock),
            "model_group": self.models, "timeline_direction": lambda name: "C",
            "overlay": Mock(), "tell_qt_this_turn_finish_queue": sender,
            "to_live2d_module_queue": Queue(), "queued_playlist": deque(),
            "preparing_sentence": False, "transition_preview": False,
            "stop_on_next_sentence": False, "lip_sync_model": None,
            "this_turn_model": None, "next_turn_earliest_start_at": 0,
            "logger": logging.getLogger(__name__),
        }
        definitions, self.frame = scheduler_code()
        exec(definitions, self.context)

    def record(self, payload):
        """记录实际启动台词或预览反馈的时间，数据保存为独立副本。"""
        self.sent.append((round(self.clock() * 1000), deepcopy(payload)))

    def load(self, turns):
        """按正式 IPC 路径提交播放列表并执行首帧。"""
        self.context["to_live2d_module_queue"].put({"playlist": turns})
        self.step(0)

    def step(self, milliseconds):
        """推进指定绝对时刻的一帧，包含主循环的队列、停止与下一句调度。"""
        self.clock.now = milliseconds / 1000
        exec(self.frame, self.context)

    def idle(self):
        """调用真实主循环中的空闲判定。"""
        return self.context["is_dialogue_idle"]()

    def started(self):
        """过滤反馈，只返回实际启动台词的时间和文本。"""
        return [(at, message["text"]) for at, message in self.sent if "text" in message]


@pytest.fixture
def playback(monkeypatch):
    """每项测试使用全新的真实调度器及隔离 IPC 队列。"""
    return Playback(monkeypatch)


def line(text, **changes):
    """建立兼容旧存档的台词，默认没有编排或音频。"""
    value = {"character_name": "A", "text": text, "translation": "", "audio_path": ""}
    value.update(changes)
    return value


def make_audio(tmp_path, duration_ms):
    """生成指定长度的临时真实 WAV，让被测时长读取使用 soundfile 实现。"""
    import soundfile as sf
    path = tmp_path / "voice.wav"
    sf.write(str(path), [0.0] * (duration_ms * 8), 8000)
    return str(path)


def test_legacy_silent_sentence_finishes_full_formula_then_separate_gap(playback):
    """旧未编排的 20 字无声台词到 2500ms 才结束，下一句从 3000ms 开始。"""
    playback.load([line("字" * 20), line("下一句", character_name="B")])
    assert playback.started() == [(0, "字" * 20)]
    playback.step(2499)
    assert playback.performance.playing
    assert not playback.stage.busy
    playback.step(2500)
    assert not playback.performance.playing
    assert playback.stage.busy
    assert not playback.idle()
    playback.step(2999)
    assert len(playback.started()) == 1
    playback.step(3000)
    assert playback.started() == [(0, "字" * 20), (3000, "下一句")]


def test_silent_sentence_then_black_starts_next_at_4500(playback):
    """黑场完全发生在 2500ms 台词之后，下一句从 4500ms 开始且不再叠加间隔。"""
    next_line = line("B", scene_before={
        "visible_characters": ["B"], "background": "night.png",
        "transition": transition_from_total(),
    })
    playback.load([line("字" * 20), next_line])
    playback.step(2500)
    assert playback.stage.busy
    playback.step(2800)
    assert playback.stage.black == pytest.approx(0.5)
    assert playback.stage.state["background"] == "default.png"
    playback.step(3200)
    assert playback.stage.black == 1
    assert playback.stage.state["background"] == "night.png"
    assert len(playback.started()) == 1
    playback.step(4499)
    assert len(playback.started()) == 1
    playback.step(4500)
    assert playback.started() == [(0, "字" * 20), (4500, "B")]
    assert not playback.stage.busy
    assert playback.stage.black == 0


def test_muted_real_audio_uses_its_duration_without_playback_or_lipsync(playback, tmp_path):
    """静音仍以真实 1500ms 音频计时，保留文件且不启动语音或任何人的口型。"""
    audio = make_audio(tmp_path, 1500)
    first = line("字" * 20, audio_path=audio, muted=True)
    playback.load([first, line("B", character_name="B")])
    assert playback.performance.span.duration_ms == 1500
    assert playback.music.played == []
    playback.context["self"]._start_turn_audio.assert_not_called()
    assert playback.context["lip_sync_model"] is None
    assert first["audio_path"] == audio and Path(audio).is_file()
    playback.step(1499)
    assert playback.performance.playing
    playback.step(1500)
    assert playback.stage.busy
    playback.step(2000)
    assert playback.started() == [(0, "字" * 20), (2000, "B")]


def test_first_sentence_ignores_saved_black_and_applies_target_immediately(playback):
    """首句即使写有黑场也不等待，先就位再在零时刻显示文本和开始计时。"""
    first = line("首句", scene_before={
        "visible_characters": [], "background": "night.png",
        "transition": transition_from_total(),
    })
    playback.load([first])
    assert playback.started() == [(0, "首句")]
    assert playback.stage.state == {"visible_characters": [], "background": "night.png"}
    assert playback.stage.black == 0
    assert not playback.stage.busy


def test_single_replay_resolves_full_context_without_replaying_earlier_phases(playback):
    """只回放所选句时继承上文背景和空舞台，不执行目标句存储的黑场。"""
    messages = [
        line("上文", scene_before={"visible_characters": [], "background": "night.png"}),
        line("回放目标", scene_before={"transition": transition_from_total()}),
    ]
    replay = deepcopy(messages[1])
    replay["scene_context"] = {"messages": deepcopy(messages), "index": 1}
    playback.load([replay])
    assert playback.started() == [(0, "回放目标")]
    assert playback.stage.state == {"visible_characters": [], "background": "night.png"}
    assert not playback.stage.busy
    assert playback.stage.black == 0


def test_final_silent_sentence_does_not_report_idle_when_pointer_already_at_end(playback):
    """播放指针在开始时递增，末句仍须完整播放到 2500ms 才能报告空闲。"""
    playback.load([line("字" * 20)])
    assert playback.context["self"].playlist_pointer == 1
    assert not playback.idle()
    playback.step(2499)
    assert not playback.idle()
    playback.step(2500)
    assert playback.idle()


@pytest.mark.parametrize("stop_at", [2800, 3200])
def test_stop_during_black_clears_mask_and_does_not_start_next_sentence(playback, stop_at):
    """淡出或全黑时收到 STOP 会取消句前阶段，不播后句且不遗留黑幕或忙碌。"""
    playback.load([
        line("字" * 20),
        line("不能播放", scene_before={
            "visible_characters": ["B"], "transition": transition_from_total(),
        }),
    ])
    playback.step(2500)
    playback.context["to_live2d_module_queue"].put("STOP")
    playback.step(stop_at)
    assert playback.stage.black == 0
    assert not playback.stage.busy
    assert playback.idle()
    playback.step(6000)
    assert playback.started() == [(0, "字" * 20)]


def test_offstage_voice_plays_normally_without_borrowing_visible_actor_mouth(playback, tmp_path):
    """A 不在场仍可正常发声，B 不会因此执行 A 的动作或驱动 B 的口型。"""
    audio = make_audio(tmp_path, 1000)
    playback.load([line("画外音", audio_path=audio, scene_before={"visible_characters": ["B"]})])
    assert playback.music.played == [(0, audio)]
    assert playback.context["this_turn_model"] is None
    assert playback.context["lip_sync_model"] is None
    playback.context["self"]._try_start_emotion_motion.assert_not_called()
    assert playback.stage.state["visible_characters"] == ["B"]
    playback.step(999)
    assert not playback.idle()
    playback.step(1000)
    assert playback.idle()


def test_unowned_message_is_played_and_has_no_speaker_placeholder(playback):
    """无角色台词正常完成公式时长，字幕使用空姓名且不绑定任何模型。"""
    playback.load([line("夜色渐深。", character_name="", muted=True)])
    assert playback.started() == [(0, "夜色渐深。")]
    playback.context["overlay"].set_text.assert_called_once_with("", "夜色渐深。\n")
    assert playback.context["this_turn_model"] is None
    assert playback.context["lip_sync_model"] is None
    playback.step(1000)
    assert playback.idle()


def test_hidden_events_are_consumed_and_not_replayed_after_reentry(playback):
    """隐藏期间跳过表情事件并消费游标，恢复可见后只执行新到点事件。"""
    expression = {"logical_id": "smile", "file_name": "smile.exp3.json"}
    saved = line("字" * 20, scene_before={"visible_characters": ["A"]}, performance_timeline={
        "characters": {"B": {"expressions": [
            {"at_ms": 0, "resource": expression},
            {"at_ms": 1000, "resource": expression},
        ]}},
    })
    resources = Mock()
    resources.resolve.return_value = {"resource": expression}
    playback.performance.catalog = Mock(return_value=resources)
    playback.load([saved])
    playback.models[1].SetExpression.assert_not_called()
    playback.performance.catalog.assert_not_called()
    playback.stage.show({"visible_characters": ["A", "B"], "background": "default.png"})
    playback.step(500)
    playback.models[1].SetExpression.assert_not_called()
    playback.step(1000)
    playback.models[1].SetExpression.assert_called_once_with("smile")
    assert len(saved["performance_timeline"]["characters"]["B"]["expressions"]) == 2


def test_plain_editor_preview_shows_resolved_target_without_transition(playback):
    """普通单句预览直接进入目标场景并播放句内时间轴，不重放上文黑场。"""
    messages = [
        line("之前", scene_before={"background": "day.jpg"}),
        line("目标", muted=True, scene_before={
            "visible_characters": [], "background": "night.png",
            "transition": transition_from_total(),
        }),
    ]
    playback.stage.enter_editor()
    playback.performance.command({"action": "enter", "session": "editor"})
    command = {"action": "play", "session": "editor", "turn": messages[1],
               "scene_context": {"messages": messages, "index": 1}}
    playback.performance.command(command)
    playback.context["show_preview_scene"](command)
    assert playback.performance.playing
    assert playback.stage.state == {"visible_characters": [], "background": "night.png"}
    assert not playback.stage.busy
    assert playback.stage.black == 0
    playback.context["overlay"].set_text.assert_called_with("A", "目标\n")


def test_transition_preview_ends_without_starting_sentence(playback):
    """专用过渡入口从前句场景演示黑场，完成后不自动启动台词音频或时间轴。"""
    messages = [
        line("之前", scene_before={"background": "day.jpg"}),
        line("目标", scene_before={
            "visible_characters": ["B"], "background": "night.png",
            "transition": transition_from_total(),
        }),
    ]
    playback.stage.enter_editor()
    playback.performance.command({"action": "enter", "session": "editor"})
    command = {"action": "transition", "session": "editor", "turn": messages[1],
               "scene_context": {"messages": messages, "index": 1}}
    playback.performance.command(command)
    playback.context["show_preview_scene"](command)
    assert playback.stage.busy
    assert playback.stage.state["background"] == "day.jpg"
    assert not playback.performance.playing
    playback.step(700)
    assert playback.stage.black == 1
    assert playback.stage.state["background"] == "night.png"
    playback.step(2000)
    assert playback.stage.black == 0
    assert not playback.stage.busy
    assert not playback.performance.playing
    assert playback.started() == []
    transitions = [message for _, message in playback.sent if message.get("type") == "multi_char_transition"]
    assert [message["playing"] for message in transitions] == [True, False]


def test_scene_change_cancels_transition_without_resuming_or_resetting_cursor(playback):
    """预览修改场景终止黑场并直接呈现新目标，保留编辑器提供的游标位置。"""
    playback.stage.enter_editor()
    playback.performance.command({"action": "enter", "session": "editor"})
    messages = [line("之前"), line("目标", scene_before={"transition": transition_from_total()})]
    transition = {"action": "transition", "session": "editor", "turn": messages[1],
                  "scene_context": {"messages": messages, "index": 1}}
    playback.performance.command(transition)
    playback.context["show_preview_scene"](transition)
    playback.step(700)
    assert playback.stage.black == 1
    messages[1]["scene_before"]["visible_characters"] = []
    command = dict(transition, action="scene", position=350)
    playback.performance.command(command)
    playback.context["show_preview_scene"](command)
    assert playback.stage.black == 0
    assert not playback.stage.busy
    assert not playback.performance.playing
    assert playback.performance.offset == 350
    assert playback.stage.state["visible_characters"] == []


def test_resource_refresh_preserves_running_preview_stage_and_other_character_cache(playback):
    """刷新只清当前角色资源索引，正在播放的时钟、舞台、动作与另一角色缓存不变。"""
    playback.performance.command({"action": "enter", "session": "editor"})
    playback.performance.command({"action": "play", "session": "editor", "turn": line("字" * 20)})
    playback.step(500)
    a_cache, b_cache = object(), object()
    playback.performance.resources = {("A", "modelA", "C"): a_cache, ("B", "modelB", "C"): b_cache}
    before = (playback.performance.position(), deepcopy(playback.stage.state), dict(playback.stage.actors))
    playback.performance.reset_all = Mock()
    playback.performance.command({"action": "refresh", "session": "editor", "character": "A"})
    assert playback.performance.resources == {("B", "modelB", "C"): b_cache}
    assert playback.performance.playing
    assert (playback.performance.position(), playback.stage.state, playback.stage.actors) == before
    playback.performance.reset_all.assert_not_called()


def bind_visual_state(playback):
    """让模型替身记录真实调度触发后的姿态，并保留原生复位调用记录。"""
    resources = Mock()
    resources.catalog.motion.return_value = SimpleNamespace(entry={})

    def resolve(kind, resource):
        """将测试资源引用投影为真实事件播放函数需要的文件信息。"""
        return {"resource": resource, "file": resource["file_name"]}

    resources.resolve.side_effect = resolve
    playback.performance.catalog = Mock(return_value=resources)
    visuals = []
    for model in playback.models:
        visual = SimpleNamespace(motion="default", expression="default")
        visuals.append(visual)

        def reset_motion(visual=visual):
            """模拟原生参数复位对当前动作姿态的影响。"""
            visual.motion = "default"

        def reset_expression(visual=visual):
            """模拟默认表情恢复，不修改动作姿态。"""
            visual.expression = "default"

        def start_motion(file, entry, callback, visual=visual):
            """记录事件真正启动的新动作，持续到明确覆盖或复位。"""
            visual.motion = file
            return True

        def set_expression(expression, visual=visual):
            """记录事件真正应用的新表情，持续到明确覆盖或复位。"""
            visual.expression = expression
            return True

        model.model.ResetAllParameters.side_effect = reset_motion
        model._restore_default_expression.side_effect = reset_expression
        model._start_performance_motion.side_effect = start_motion
        model.SetExpression.side_effect = set_expression
    return visuals


def role_timeline(motion=None, expression=None, at_ms=0):
    """构造角色双轨，未指定的资源保留为明确的空数组。"""
    motions = []
    expressions = []
    if motion is not None:
        motions.append({"at_ms": at_ms, "resource": {
            "logical_id": motion, "file_name": motion + ".motion3.json",
        }})
    if expression is not None:
        expressions.append({"at_ms": at_ms, "resource": {
            "logical_id": expression, "file_name": expression + ".exp3.json",
        }})
    return {"motions": motions, "expressions": expressions}


def test_two_empty_sentences_keep_pose_until_next_valid_events(playback):
    """实际触发动作和表情后，跨两句双空轨保持姿态，下一有效事件到点才接管。"""
    visuals = bind_visual_state(playback)
    messages = [
        line("甲", performance_timeline={"characters": {"A": role_timeline("wave", "smile")}}),
        line("乙", character_name="B", performance_timeline={"characters": {"A": role_timeline()}}),
        line("丙", character_name="B", performance_timeline={"characters": {"A": role_timeline()}}),
        line("丁", performance_timeline={"characters": {"A": role_timeline("nod", "sad", 200)}}),
    ]
    playback.load(messages)
    assert (visuals[0].motion, visuals[0].expression) == ("wave.motion3.json", "smile")
    model = playback.models[0]
    reset_counts = (
        model.model.StopAllMotions.call_count,
        model.model.ResetAllParameters.call_count,
        model.model.ResetExpressions.call_count,
    )
    for instant in (600, 1100, 1700, 2200, 2800, 3300, 3499):
        playback.step(instant)
        assert (visuals[0].motion, visuals[0].expression) == ("wave.motion3.json", "smile")
        assert (
            model.model.StopAllMotions.call_count,
            model.model.ResetAllParameters.call_count,
            model.model.ResetExpressions.call_count,
        ) == reset_counts
    assert playback.started() == [(0, "甲"), (1100, "乙"), (2200, "丙"), (3300, "丁")]
    model._start_performance_motion.assert_called_once_with("wave.motion3.json", {}, None)
    model.SetExpression.assert_called_once_with("smile")
    playback.step(3500)
    assert (visuals[0].motion, visuals[0].expression) == ("nod.motion3.json", "sad")
    assert model._start_performance_motion.call_count == 2
    assert model.SetExpression.call_count == 2


@pytest.mark.parametrize("single_replay", [False, True])
def test_new_playback_resets_both_roles_before_empty_first_sentence(playback, single_replay):
    """新整段或单句回放的首句双轨为空时，双人各复位一次且不继承上一轮末态。"""
    visuals = bind_visual_state(playback)
    previous = line("前", performance_timeline={"characters": {
        name: role_timeline("wave", "smile") for name in ("A", "B")
    }})
    playback.load([previous])
    playback.step(600)
    assert playback.idle()
    assert [(visual.motion, visual.expression) for visual in visuals] == [
        ("wave.motion3.json", "smile"), ("wave.motion3.json", "smile"),
    ]
    counts = [(model.model.StopAllMotions.call_count, model.model.ResetAllParameters.call_count,
               model.model.ResetExpressions.call_count) for model in playback.models]
    playback.performance.managed.add("旧角色")
    target = line("新", performance_timeline={"characters": {
        name: role_timeline() for name in ("A", "B")
    }})
    if single_replay:
        target["scene_context"] = {"messages": [previous, deepcopy(target)], "index": 1}
        messages = [target]
    else:
        messages = [target, line("后")]
    playback.context["to_live2d_module_queue"].put({"playlist": messages})
    playback.step(1000)
    assert [(visual.motion, visual.expression) for visual in visuals] == [
        ("default", "default"), ("default", "default"),
    ]
    for model, before in zip(playback.models, counts):
        assert (model.model.StopAllMotions.call_count, model.model.ResetAllParameters.call_count,
                model.model.ResetExpressions.call_count) == tuple(count + 1 for count in before)
        assert model._start_performance_motion.call_count == 1
        assert model.SetExpression.call_count == 1
    assert playback.performance.managed == {"A", "B"}
    assert playback.started() == [(0, "前"), (1000, "新")]


@pytest.mark.parametrize("empty_kind", ["motions", "expressions"])
def test_only_one_empty_track_keeps_existing_reset_rule(playback, empty_kind):
    """仅一轨为空仍在句首复位该轨，非空轨维持原姿态直到自己的事件触发。"""
    visuals = bind_visual_state(playback)
    next_tracks = role_timeline("nod", "sad", 200)
    next_tracks[empty_kind] = []
    playback.load([
        line("前", performance_timeline={"characters": {"A": role_timeline("wave", "smile")}}),
        line("后", performance_timeline={"characters": {"A": next_tracks}}),
    ])
    model = playback.models[0]
    before_motion_resets = model.model.ResetAllParameters.call_count
    before_expression_resets = model.model.ResetExpressions.call_count
    playback.step(600)
    playback.step(1100)
    if empty_kind == "motions":
        assert (visuals[0].motion, visuals[0].expression) == ("default", "smile")
        assert model.model.ResetAllParameters.call_count == before_motion_resets + 1
        assert model.model.ResetExpressions.call_count == before_expression_resets
    else:
        assert (visuals[0].motion, visuals[0].expression) == ("wave.motion3.json", "default")
        assert model.model.ResetAllParameters.call_count == before_motion_resets
        assert model.model.ResetExpressions.call_count == before_expression_resets + 1
    playback.step(1300)
    expected = ("default", "sad") if empty_kind == "motions" else ("nod.motion3.json", "default")
    assert (visuals[0].motion, visuals[0].expression) == expected
