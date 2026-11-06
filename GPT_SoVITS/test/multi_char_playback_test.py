"""正式播放规则以真实舞台和时间轴执行，输入不接入用户桌面。"""
import ast
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from multi_char_scene_playback_test import Playback, line, make_audio
from live2d_support.multi_char_scene import transition_from_total


@pytest.fixture
def player(monkeypatch):
    return Playback(monkeypatch)


def command(player, action, **values):
    payload = {"action": action, "session": player.controller.session,
               "revision": player.controller.revision, "request_id": action + "-request"}
    payload.update(values)
    return player.controller.command(payload)


def load(player, messages, **values):
    return command(player, "load", session="theater", revision=player.controller.revision + 1,
                   messages=messages, **values)


def test_load_waits_and_manual_wait_never_consumes_next_transition(player):
    load(player, [line("甲"), line("乙", scene_before={"visible_characters": ["B"]})])
    assert player.controller.phase == "ready" and player.idle()
    player.step(10000)
    assert player.started() == []
    assert player.controller.click()
    assert player.started() == [(10000, "甲")]
    assert not player.controller.click()
    player.step(10600)
    assert player.controller.phase == "ready"
    assert (player.controller.current_index, player.controller.pending_index) == (0, 1)
    player.step(20000)
    assert player.stage.state["visible_characters"] == ["A", "B"]
    assert player.controller.click()
    assert player.controller.phase == "transition"
    assert not player.controller.click()
    player.step(20499)
    assert player.started() == [(10000, "甲")]
    player.step(20500)
    assert player.started() == [(10000, "甲"), (20500, "乙")]


@pytest.mark.parametrize("off_at", [650, 1300, 2599, 2700])
def test_turning_auto_off_finishes_black_and_its_sentence(player, off_at):
    load(player, [line("甲"), line("乙", scene_before={"transition": transition_from_total()}), line("丙")])
    player.controller.toggle_auto()
    player.step(600)
    assert player.controller.phase == "transition"
    player.step(off_at)
    assert player.controller.toggle_auto()
    assert not player.controller.auto
    if off_at < 2600:
        player.step(2600)
    player.step(3200 if off_at < 2600 else 3300)
    assert player.controller.phase == "ready"
    assert player.controller.pending_index == 2
    assert player.stage.black == 0
    assert [text for _, text in player.started()] == ["甲", "乙"]
    player.step(10000)
    assert len(player.started()) == 2


def test_turning_auto_on_mid_sentence_does_not_restart_and_end_never_loops(player):
    load(player, [line("甲"), line("乙")])
    player.controller.click()
    player.step(300)
    assert player.controller.toggle_auto()
    assert player.started() == [(0, "甲")]
    player.step(600)
    player.step(1100)
    player.step(1700)
    assert player.controller.phase == "ended"
    assert not player.controller.auto
    player.step(5000)
    assert player.started() == [(0, "甲"), (1100, "乙")]
    player.controller.click()
    assert player.started()[-1] == (5000, "甲")
    assert not player.controller.auto


def test_end_space_restarts_first_scene_in_auto(player):
    load(player, [line("甲"), line("乙", scene_before={"visible_characters": [], "background": "night.png"})])
    command(player, "auto_from", index=1)
    player.step(600)
    assert player.controller.phase == "ended"
    assert player.stage.state["visible_characters"] == []
    assert player.controller.toggle_auto()
    assert player.stage.state == {"visible_characters": ["A", "B"], "background": "default.png"}
    assert player.started() == [(0, "乙"), (600, "甲")]


@pytest.mark.parametrize("at", [200, 600, 800, 1100])
@pytest.mark.parametrize("action", ["single", "auto_from"])
def test_busy_requests_are_rejected_and_never_run_later(player, at, action):
    load(player, [line("甲"), line("乙")])
    player.controller.toggle_auto()
    player.step(at)
    assert not command(player, action, index=0)
    assert player.sent[-1][1]["accepted"] is False
    assert player.sent[-1][1]["error"] == "请等待当前播放结束"
    player.step(2000)
    player.step(2600)
    player.step(10000)
    assert [text for _, text in player.started()] == ["甲", "乙"]


def test_single_preserves_progress_and_restores_formal_scene_on_continue(player):
    load(player, [line("甲", scene_before={"background": "morning.png"}), line("乙"),
                  line("丙", scene_before={"background": "night.png", "visible_characters": []})])
    player.controller.click()
    player.step(600)
    assert command(player, "single", index=2)
    assert player.stage.state == {"visible_characters": [], "background": "night.png"}
    assert not player.controller.click()
    assert not player.controller.toggle_auto()
    assert not command(player, "single", index=1)
    player.step(1200)
    assert player.controller.phase == "ready"
    assert (player.controller.current_index, player.controller.pending_index) == (0, 1)
    assert player.stage.state["background"] == "night.png"
    # 再次临时回放也不能把临时夜景变成正式场景基准。
    assert command(player, "single", index=2)
    player.step(1800)
    assert player.controller.click()
    assert player.stage.state == {"visible_characters": ["A", "B"], "background": "morning.png"}
    player.step(2301)
    assert player.started() == [(0, "甲"), (600, "丙"), (1200, "丙"), (2301, "乙")]


def test_single_at_end_returns_to_end_instead_of_replacing_restart_origin(player):
    load(player, [line("甲"), line("乙")])
    command(player, "auto_from", index=1)
    player.step(600)
    command(player, "single", index=1)
    player.step(1200)
    assert player.controller.phase == "ended" and player.controller.pending_index == 2
    player.controller.click()
    assert player.started()[-1] == (1200, "甲")


def test_auto_from_skips_selected_transition_but_runs_following_transition(player):
    black = {"transition": transition_from_total(), "background": "night.png"}
    load(player, [line("旧"), line("起", scene_before=black), line("后", scene_before=black)])
    assert command(player, "auto_from", index=1)
    assert player.started() == [(0, "起")]
    assert not player.stage.busy
    assert player.stage.state["background"] == "night.png"
    player.step(600)
    assert player.controller.phase == "transition"
    player.step(2599)
    assert player.started() == [(0, "起")]
    player.step(2600)
    assert player.started() == [(0, "起"), (2600, "后")]


def test_editor_reload_keeps_restored_scene_until_first_input_and_uses_new_snapshot(player):
    load(player, [line("旧", scene_before={"background": "night.png", "visible_characters": []})])
    player.controller.click()
    player.step(600)
    restored = deepcopy(player.stage.state)
    assert command(player, "reserve_editor", request_id="edit-1")
    assert not player.controller.click()
    assert load(player, [line("新首句"), line("插入句")], preserve_scene=True)
    assert player.stage.state == restored
    assert player.controller.phase == "ready" and player.controller.pending_index == 0
    assert not player.controller.auto and player.controller.reservation is None
    player.step(2000)
    assert len(player.started()) == 1
    player.controller.click()
    assert player.started()[-1] == (2000, "新首句")
    assert player.stage.state == {"visible_characters": ["A", "B"], "background": "default.png"}


def test_revision_and_reservation_reject_old_requests_and_ack_current_requests(player):
    load(player, [line("甲")])
    before = len(player.sent)
    assert not command(player, "single", index=0, revision=-1)
    assert not command(player, "load", messages=[line("错")], revision=-1)
    assert len(player.sent) == before
    assert command(player, "reserve_editor", request_id="first")
    ack = player.sent[-1][1]
    assert ack["accepted"] and ack["request_id"] == "first" and ack["busy"]
    command(player, "release_editor", reservation_id="different")
    assert not player.idle()
    command(player, "release_editor", reservation_id="first")
    assert player.idle()
    assert command(player, "reserve_editor", request_id="second")
    command(player, "release_editor", reservation_id="first")
    assert not player.idle()
    load(player, [line("新")])
    assert player.idle()


def test_audio_block_inside_preview_does_not_stop_preview_or_change_stage(player):
    load(player, [line("甲")])
    player.performance.preview = True
    player.performance.playing = True
    player.stage.state["background"] = "preview.png"
    player.stage.cancel = Mock(wraps=player.stage.cancel)
    player.music.stop = Mock(wraps=player.music.stop)
    assert command(player, "block", reason="audio")
    assert player.performance.playing and player.performance.preview
    player.stage.cancel.assert_not_called()
    player.music.stop.assert_not_called()
    assert player.stage.state["background"] == "preview.png"
    assert not player.controller.toggle_auto()
    command(player, "unblock", reason="audio")
    assert not player.idle()


def test_audio_fill_blocks_stage_between_sentences_and_across_reload(player):
    """单句解锁后整批占用仍阻止舞台鼠标、空格及回放请求。"""
    load(player, [line("原剧本")])
    assert command(player, "block", reason="audio_fill")
    assert command(player, "block", reason="audio")
    assert command(player, "unblock", reason="audio")
    assert not player.controller.click()
    assert not player.controller.toggle_auto()
    assert not command(player, "single", index=0)
    assert not command(player, "reserve_editor")
    load(player, [line("补齐后")], blocked_reasons=["audio_fill"], preserve_scene=True)
    assert not player.controller.click()
    assert command(player, "unblock", reason="audio_fill")
    assert player.controller.click()


def test_generation_block_stops_current_and_load_retains_block_until_unblock(player):
    load(player, [line("甲"), line("乙")])
    player.controller.toggle_auto()
    player.step(200)
    command(player, "block", reason="generation")
    assert not player.performance.playing and not player.controller.auto
    assert not player.controller.click()
    load(player, [line("新")], blocked_reasons=["generation"])
    assert not player.idle()
    command(player, "unblock", reason="generation")
    assert player.idle()
    player.controller.click()
    assert player.started()[-1] == (200, "新")


def test_empty_script_and_invalid_index_are_safe(player):
    load(player, [])
    assert player.controller.phase == "empty" and player.idle()
    assert not player.controller.click() and not player.controller.toggle_auto()
    assert not command(player, "single", index=0)
    assert not command(player, "auto_from", index=-1)
    assert not command(player, "load", messages=None, revision=9)
    assert player.controller.messages == []


def assert_failed_load_is_recoverable(player, payload):
    ack = player.sent[-1][1]
    assert ack["session"] == payload["session"] and ack["revision"] == payload["revision"]
    assert ack["request_id"] == payload["request_id"] and ack["accepted"] is False
    assert player.controller.load_failed and player.controller.messages == []
    assert player.controller.reservation is None and not player.performance.playing
    assert not player.controller.click()
    assert not command(player, "single", index=0, messages=[line("旧模型不能播放")])
    assert not command(player, "auto_from", index=0, messages=[line("旧模型不能播放")])
    assert not command(player, "reserve_editor")
    assert load(player, [line("重试成功")])
    assert not player.controller.load_failed
    assert player.controller.click()
    assert player.started()[-1][1] == "重试成功"


def test_invalid_load_adopts_new_identity_and_rejects_playback_until_retry(player):
    load(player, [line("旧")])
    command(player, "reserve_editor", request_id="old-editor")
    payload = {"action": "load", "session": "replacement", "revision": 10,
               "request_id": "invalid-load", "messages": None}
    assert not player.controller.command(payload)
    assert_failed_load_is_recoverable(player, payload)


def host_command_code():
    """抽取真实宿主分发，测试模型失败和装载时的字幕处理而不启动 OpenGL。"""
    source = Path(__file__).resolve().parents[1] / "multi_char_live2d_module.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    dispatch = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                    and "playback.command(x)" in ast.unparse(node))
    # 该分发块中的其它消息分支有 continue；包装一轮循环保留真实处理逻辑。
    loop = ast.For(target=ast.Name(id="unused", ctx=ast.Store()),
                   iter=ast.List(elts=[ast.Constant(0)], ctx=ast.Load()), body=[dispatch], orelse=[])
    return compile(ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[])), str(source), "exec")


def test_model_setup_failure_ack_uses_new_load_identity_and_runtime_can_retry(player):
    load(player, [line("旧")])
    player.controller.click()
    payload = {"type": "multi_char_playback", "action": "load", "session": "replacement",
               "revision": 10, "request_id": "model-failed", "messages": [line("新")],
               "slots_payload": {"slots": []}}
    context = {"x": payload, "message_type": "multi_char_playback", "playback": player.controller,
               "performance": player.performance, "stage": player.stage,
               "expression_preview": SimpleNamespace(close=Mock()), "layout_editing": False,
               "apply_slots_payload": Mock(side_effect=RuntimeError("model setup failed")),
               "logger": Mock(), "overlay": Mock()}
    exec(host_command_code(), context)
    assert_failed_load_is_recoverable(player, payload)


@pytest.mark.parametrize("muted", [False, True])
def test_audio_duration_and_silent_last_sentence_stay_busy(player, tmp_path, muted):
    audio = make_audio(tmp_path, 1000)
    load(player, [line("长" * 100, audio_path=audio, muted=muted)])
    player.controller.click()
    player.step(999)
    assert not player.idle()
    player.step(1000)
    assert player.idle() and player.controller.phase == "ended"


def test_state_feedback_does_not_repeat_each_frame_and_turn_feedback_is_typed(player):
    load(player, [line("甲")])
    count = len(player.sent)
    for instant in (100, 200, 300):
        player.step(instant)
    assert len(player.sent) == count
    player.controller.click()
    payloads = [message for _, message in player.sent]
    turn = next(message for message in payloads if message.get("type") == "multi_char_playback_turn")
    assert turn["session"] == "theater" and turn["revision"] == 0 and turn["index"] == 0
    assert turn["turn"]["text"] == "甲"
    assert all("text" not in message for message in payloads)


def event_handler():
    source = Path(__file__).resolve().parents[1] / "multi_char_live2d_module.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    loop = next(node for node in ast.walk(tree) if isinstance(node, ast.For)
                and ast.unparse(node.iter) == "pygame.event.get()")
    return compile(ast.Module(body=[loop], type_ignores=[]), str(source), "exec")


def test_pygame_space_repeat_focus_and_layout_do_not_cross_input_boundaries(player):
    load(player, [line("甲"), line("乙")])
    constants = dict(QUIT=1, WINDOWFOCUSLOST=2, KEYUP=3, KEYDOWN=4, K_SPACE=5,
                     MOUSEBUTTONDOWN=6, MOUSEBUTTONUP=7, MOUSEMOTION=8, MOUSEWHEEL=9,
                     K_ESCAPE=10, K_q=11, K_r=12)
    events = []
    focused = [True]
    pg = SimpleNamespace(**constants, event=SimpleNamespace(get=lambda: events),
                         key=SimpleNamespace(get_focused=lambda: focused[0]))
    context = dict(pygame=pg, playback=player.controller, space_held=False, layout_editing=False,
                   self=SimpleNamespace(run=True))
    player.controller.occupied = lambda: context["layout_editing"]
    code = event_handler()

    def push(kind, **fields):
        events[:] = [SimpleNamespace(type=constants[kind], **fields)]
        exec(code, context)

    push("KEYDOWN", key=pg.K_SPACE)
    assert player.controller.auto
    push("KEYDOWN", key=pg.K_SPACE)
    push("KEYDOWN", key=pg.K_SPACE, repeat=True)
    assert player.controller.auto
    push("KEYUP", key=pg.K_SPACE)
    focused[0] = False
    push("KEYDOWN", key=pg.K_SPACE)
    assert player.controller.auto
    push("WINDOWFOCUSLOST")
    focused[0] = True
    push("KEYDOWN", key=pg.K_SPACE)
    assert not player.controller.auto
    push("KEYUP", key=pg.K_SPACE)
    context["layout_editing"] = True
    push("KEYDOWN", key=pg.K_SPACE)
    assert not player.controller.auto


def test_auto_end_hint_waits_ten_seconds_from_final_sentence_end_and_runs_once(player):
    """自动整剧的末句完成后保留字幕十秒，再一次性显示操作提示。"""
    load(player, [line("甲"), line("乙")])
    player.controller.toggle_auto()
    player.step(600)
    player.step(1100)
    player.step(1699)
    assert player.controller.phase == "playing"
    player.show_hint.assert_not_called()
    player.step(1700)
    assert player.controller.phase == "ended"
    assert player.context["overlay"].set_text.call_args.args == ("A", "乙\n")
    player.step(11699)
    player.show_hint.assert_not_called()
    player.step(11700)
    player.show_hint.assert_called_once_with()
    player.step(25000)
    player.show_hint.assert_called_once_with()


@pytest.mark.parametrize("mode", ["manual", "single", "auto_disabled_on_last"])
def test_manual_or_single_end_never_schedules_end_hint(player, mode):
    """手动播完、临时单句与末句关闭自动均继续保留台词，不启动十秒提醒。"""
    load(player, [line("末句")])
    if mode == "single":
        assert command(player, "single", index=0)
    elif mode == "auto_disabled_on_last":
        player.controller.toggle_auto()
        player.step(300)
        player.controller.toggle_auto()
    else:
        player.controller.click()
    player.step(700)
    player.step(20000)
    player.show_hint.assert_not_called()


def test_auto_from_middle_schedules_hint_after_remaining_script_ends(player):
    """从中间开始自动播到整剧末尾也在末句结束十秒后提示。"""
    load(player, [line("前"), line("末句")])
    assert command(player, "auto_from", index=1)
    player.step(700)
    player.step(10699)
    player.show_hint.assert_not_called()
    player.step(10700)
    player.show_hint.assert_called_once_with()


@pytest.mark.parametrize("interruption", [
    "stop", "load", "failed_load", "manual_restart", "single", "reserve", "block", "preview",
])
def test_new_activity_cancels_previous_auto_end_hint_instead_of_deferring_it(player, interruption):
    """新活动取消上轮的迟到提示；活动结束后也不能重新覆盖当前字幕。"""
    load(player, [line("甲")])
    player.controller.toggle_auto()
    player.step(600)
    player.step(1000)
    if interruption == "stop":
        player.controller.stop()
    elif interruption == "load":
        load(player, [line("新")], preserve_scene=True)
    elif interruption == "failed_load":
        command(player, "load", messages=None, revision=player.controller.revision + 1)
    elif interruption == "manual_restart":
        player.controller.click()
        player.step(1600)
    elif interruption == "single":
        command(player, "single", index=0)
        player.step(1600)
    elif interruption == "reserve":
        assert command(player, "reserve_editor", request_id="editing")
        command(player, "release_editor", reservation_id="editing")
    elif interruption == "block":
        command(player, "block", reason="audio")
        command(player, "unblock", reason="audio")
    else:
        player.performance.preview = True
        player.step(1100)
        player.performance.preview = False
    player.step(20000)
    player.show_hint.assert_not_called()


def test_auto_restart_uses_new_finish_deadline_not_previous_round_deadline(player):
    """上轮提醒尚未到期就重新自动播放，十秒从新一轮完成时重新计算。"""
    load(player, [line("甲")])
    player.controller.toggle_auto()
    player.step(600)
    player.step(5000)
    player.controller.toggle_auto()
    player.step(5600)
    player.step(10600)
    player.show_hint.assert_not_called()
    player.step(15599)
    player.show_hint.assert_not_called()
    player.step(15600)
    player.show_hint.assert_called_once_with()


@pytest.mark.parametrize("preserve_scene", [False, True])
def test_first_load_preserves_startup_help_and_editor_reload_preserves_restored_subtitle(player, preserve_scene):
    """第一次装载保留启动帮助；之后普通换剧本清字幕，编排退出保留刚恢复的字幕。"""
    from live2d_support.multi_char_playback import PLAYBACK_HELP_TEXT

    shown = ["", PLAYBACK_HELP_TEXT]

    def set_text(name, text):
        """用字幕内容替身观察真实宿主分发的最终显示结果。"""
        shown[:] = [name, text]

    context = {
        "message_type": "multi_char_playback", "playback": player.controller,
        "performance": player.performance, "stage": player.stage,
        "expression_preview": SimpleNamespace(close=Mock()), "layout_editing": False,
        "apply_slots_payload": Mock(), "logger": Mock(),
        "overlay": SimpleNamespace(set_text=set_text),
    }
    code = host_command_code()
    context["x"] = {"type": "multi_char_playback", "action": "load", "session": "theater",
                    "revision": 0, "messages": [line("甲")], "slots_payload": {"slots": []}}
    exec(code, context)
    assert shown == ["", PLAYBACK_HELP_TEXT]
    assert player.controller.phase == "ready"
    set_text("A", "刚恢复的台词")
    context["x"] = dict(context["x"], revision=1, preserve_scene=preserve_scene)
    exec(code, context)
    assert shown == (["A", "刚恢复的台词"] if preserve_scene else ["", ""])
