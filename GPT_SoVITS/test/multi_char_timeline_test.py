"""小剧场时间轴的契约、资源和调度回归，所有数据均在临时目录。"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from performance_types import trim_timeline, valid_timeline_events
from live2d_support.multi_char_performance import TimelineCursor, MultiCharPerformance
from live2d_support.multi_char_resources import TheaterResources, audio_span, assign_colors


def motion(at, name="nod"):
    """构造不依赖实际模型的动作事件。"""
    return {"at_ms": at, "resource": {"logical_id": name, "file_name": name + ".motion3.json"}}


def test_trim_preserves_hidden_roles_and_original():
    """裁剪包含非在场角色及等于末尾的起点，不修改输入。"""
    source = {"characters": {"A": {"motions": [motion(0), motion(2000)], "expressions": []},
                             "B": {"motions": [], "expressions": [{"at_ms": 2100, "resource": None}]}}}
    result, count = trim_timeline(source, 2000)
    assert count == 2
    assert len(source["characters"]["A"]["motions"]) == 2
    assert result["characters"]["B"]["expressions"] == []


def test_cursor_seek_lag_and_end():
    """定位跳过历史、同点仅一次、卡顿取最后点、句末不补播。"""
    value = {"characters": {"A": {"motions": [motion(0), motion(1000), motion(1500)]},
                             "B": {"expressions": [{"at_ms": 1500, "resource": None}]}}}
    cursor = TimelineCursor(value, 3000, 1000)
    assert cursor.advance(999) == []
    assert cursor.advance(1000)[0][2]["at_ms"] == 1000
    assert cursor.advance(1000) == []
    assert len(cursor.advance(1600)) == 2
    assert cursor.advance(3000) == []
    cursor = TimelineCursor(value, 3000)
    assert cursor.advance(1600)[0][2]["at_ms"] == 1500


def test_corrupt_events_do_not_mean_expression_reset():
    """缺失 resource 和重复/负/非整数时间均不能变成重置事件。"""
    events = [{"at_ms": 0}, {"at_ms": True, "resource": None},
              {"at_ms": -1, "resource": None}, {"at_ms": 3, "resource": None},
              {"at_ms": 3, "resource": None}]
    assert valid_timeline_events(events, "expressions") == [{"at_ms": 3, "resource": None}]


def test_audio_missing_and_silence(tmp_path):
    """正常无声和原音频丢失必须区别，文字长度不计译文。"""
    assert audio_span("", "你好").duration_ms == 700
    assert not audio_span("", "你好").missing
    assert audio_span(str(tmp_path / "missing.wav"), "你好").missing
    import soundfile as sf
    sf.write(str(tmp_path / "test.wav"), [0.0] * 8000, 8000)
    assert audio_span(str(tmp_path / "test.wav"), "你好").duration_ms == 1000


def test_resource_fallback_and_duration(tmp_path):
    """资源按逻辑键和唯一文件名解析，读取单轮时长无需渲染器。"""
    (tmp_path / "nod_C.motion3.json").write_text(json.dumps({"Meta": {"Duration": 1.25}}))
    (tmp_path / "model.model3.json").write_text(json.dumps({"FileReferences": {
        "Motions": {"happiness": [{"File": "nod_C.motion3.json"}]}}}))
    resources = TheaterResources(str(tmp_path / "model.model3.json"))
    assert resources.duration({"logical_id": "nod"}) == 1250
    assert resources.resolve("motions", {"logical_id": "old", "file_name": "nod_C.motion3.json"})
    assert resources.resolve("motions", {"file_name": "missing"}) is None


def test_colors_stay_stable():
    """已有颜色不因重绘或时间排序改变，新相邻事件避免同色。"""
    value = {"characters": {"A": {"motions": [motion(0), motion(100)]}}}
    assign_colors(value, ["#FF0000", "#0000FF"])
    colors = [event["color"] for event in value["characters"]["A"]["motions"]]
    assert colors[0] != colors[1]
    assign_colors(value, ["#FFFFFF"])
    assert colors == [event["color"] for event in value["characters"]["A"]["motions"]]


def test_message_roundtrip_keeps_timeline_out_of_llm():
    """消息往返保留时间轴，旧消息不补字段，LLM 不接收编排。"""
    from chat.chat import Message
    data = {"character_name": "A", "text": "hello", "emotion": "normal",
            "performance_timeline": {"characters": {"A": {"motions": [], "expressions": []}}}}
    message = Message.from_dict(data)
    assert Message.from_dict(message.as_dict()).performance_timeline == data["performance_timeline"]
    assert "performance_timeline" not in Message.from_dict({"text": "old"}).as_dict()
    assert "performance_timeline" not in message.to_llm_query("assistant")
    exported = message.as_dict()
    exported["performance_timeline"]["characters"].clear()
    assert message.performance_timeline["characters"]


def test_preview_seek_waits_for_play_and_pause_freezes(monkeypatch):
    """精确定位到事件时暂停不触发，继续只执行一次；暂停时间不推进。"""
    clock = [10.0]
    monkeypatch.setattr('live2d_support.multi_char_performance.time.monotonic', lambda: clock[0])
    music = Mock()
    music.get_pos.return_value = -1
    player = MultiCharPerformance(lambda: [], lambda: [], Mock(), music)
    player.event = Mock()
    turn = {"text": "abcdefghij", "performance_timeline": {
        "characters": {"A": {"motions": [motion(500)], "expressions": []}}}}
    player.command({"action": "enter", "session": "test"})
    player.command({"action": "seek", "session": "test", "turn": turn, "position": 500, "resume": False})
    assert not player.event.called
    clock[0] += 20
    assert player.position() == 500
    player.command({"action": "resume", "session": "test"})
    player.tick()
    assert player.event.call_count == 1
    player.tick()
    assert player.event.call_count == 1


def test_formal_end_keeps_visuals_and_ignores_other_role():
    """正式句末只停止调度，动作/表情不被音频时长裁断。"""
    player = MultiCharPerformance(lambda: [], lambda: [], Mock(), Mock())
    player.playing = True
    player.cursor = TimelineCursor({"characters": {}}, 500)
    player.position = Mock(return_value=500)
    player.reset_all = Mock()
    player.tick()
    assert not player.playing
    player.reset_all.assert_not_called()


def test_preview_end_keeps_pose_until_explicit_stop():
    """预览自然到句末保留画面，只有显式停止才恢复默认状态。"""
    player = MultiCharPerformance(lambda: [], lambda: [], Mock(), Mock())
    player.preview = True
    player.session = "test"
    player.playing = True
    player.span = SimpleNamespace(duration_ms=500)
    player.cursor = TimelineCursor({"characters": {}}, 500)
    player.position = Mock(return_value=500)
    player.reset_all = Mock()
    player.tick()
    assert not player.playing
    assert player.offset == 500
    assert player.paused
    player.reset_all.assert_not_called()
    player.music.stop.assert_called_once()
    player.command({"session": "test", "action": "stop"})
    player.reset_all.assert_called_once()


def test_finished_motion_does_not_start_idle():
    """动作已结束且台词仍在播放时，不重置动作或启动待机。"""
    model = Mock()
    model.is_motion_finished.return_value = True
    player = MultiCharPerformance(lambda: [model], lambda: [{"character_name": "A"}], Mock(), Mock())
    player.playing = True
    player.cursor = TimelineCursor({"characters": {}}, 5000)
    player.position = Mock(return_value=2000)
    player.reset = Mock()
    player.tick()
    assert player.playing
    player.reset.assert_not_called()
    model.StartRandomMotion.assert_not_called()


def test_playlist_end_handles_unhashable_models():
    """执行真实播放循环的无声收尾分支，空编排和编排模型均不能触发哈希异常。"""
    import ast
    import time
    source = Path(__file__).resolve().parents[1] / "multi_char_live2d_module.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                  and "idle_recover_timer" in ast.unparse(node.test))
    code = compile(ast.Module(body=[branch], type_ignores=[]), str(source), "exec")
    model = SimpleNamespace(performance_state=object(), reset_performance=Mock(), StartRandomMotion=Mock())
    player = MultiCharPerformance(lambda: [model], lambda: [{"character_name": "A"}], Mock(), Mock())
    host = SimpleNamespace(playlist=[{}], playlist_pointer=1, _active_model_versions=Mock(return_value=[]),
                           _motion_position_for_slot=Mock(return_value="C"))
    context = dict(performance=player, self=host, audio_is_playing=False, time=time,
                   idle_recover_timer=0, next_turn_earliest_start_at=0,
                   model_group=[model], active_motion_facing_mode="screen")
    exec(code, context)
    model.reset_performance.assert_called_once()
    model.StartRandomMotion.assert_called_once()
    model.reset_performance.reset_mock()
    model.StartRandomMotion.reset_mock()
    player.managed.add("A")
    exec(code, context)
    model.reset_performance.assert_not_called()
    model.StartRandomMotion.assert_not_called()
    # 内容相等的另一个对象不能被当成当前槽位的模型。
    assert not player.is_managed_model(SimpleNamespace(**vars(model)))


def test_missing_motion_does_not_reset_expression():
    """换装缺失动作跳过，不用无效事件清理前一句表情。"""
    model = Mock()
    model.version = "v3"
    player = MultiCharPerformance(lambda: [model], lambda: [{"character_name": "A"}], Mock(), Mock())
    resources = Mock()
    resources.resolve.return_value = None
    player.catalog = Mock(return_value=resources)
    player.reset = Mock()
    player.event("A", "motions", motion(0))
    player.reset.assert_not_called()
    model.StartRandomMotion.assert_not_called()


def test_backup_roundtrip(tmp_path):
    """实际备份导入保留四轨和颜色，不要求模型文件随备份打包。"""
    from unittest.mock import patch
    from chat.chat import Chat, ChatManager, Message
    value = {"characters": {"A": {"motions": [dict(motion(0), color="#0077DD")], "expressions": []},
                             "B": {"motions": [], "expressions": [{"at_ms": 20, "resource": None}]}}}
    message = Message.from_dict({"character_name": "A", "text": "你好", "emotion": "like", "performance_timeline": value})
    chat = Chat(name="编排测试", message_list=[message])
    with patch('chat.chat._reference_audio_dir', return_value=tmp_path):
        manager = ChatManager([chat])
        backup = tmp_path / 'test.zip'
        manager.export_chats_to_backup([chat.chat_id], backup)
        restored = ChatManager().import_chats_from_backup(backup).imported_chats[0]
    assert restored.message_list[0].performance_timeline == value
