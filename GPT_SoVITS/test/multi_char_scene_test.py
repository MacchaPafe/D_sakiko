"""小剧场句前数据、舞台继承与时间边界的回归，不连接图形运行时。"""
import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chat.chat import Chat, ChatManager, Message
from live2d_support.multi_char_resources import audio_span
from live2d_support.multi_char_scene import (
    SceneTiming,
    background_path,
    background_root,
    resolve_before,
    resolve_scene,
    transition_from_total,
)


def test_optional_scene_and_mute_preserve_legacy_messages():
    """读取旧剧本不凭空写入场景和静音，新字段也不混入 LLM 历史。"""
    legacy = Message.from_dict({"character_name": "A", "text": "你好"})
    assert legacy.scene_before is None
    assert legacy.muted is False
    assert "scene_before" not in legacy.as_dict()
    assert "muted" not in legacy.as_dict()
    legacy.scene_before = {"visible_characters": []}
    legacy.muted = True
    payload = json.loads(legacy.to_llm_query("assistant"))
    assert "scene_before" not in payload
    assert "muted" not in payload


def test_unowned_message_roundtrip_and_copy_isolation():
    """无角色台词和空舞台往返后保持原意，序列化副本不共享场景或时间轴。"""
    source = {
        "character_name": "",
        "text": "夜色渐深。",
        "muted": True,
        "audio_path": "original.wav",
        "scene_before": {"visible_characters": [], "background": "night.jpg"},
        "performance_timeline": {"characters": {"A": {"motions": []}}},
    }
    message = Message.from_dict(source)
    source["scene_before"]["visible_characters"].append("B")
    exported = message.as_dict()
    clone = Message.from_dict(exported)
    exported["scene_before"]["visible_characters"].append("A")
    assert message.scene_before == clone.scene_before == {
        "visible_characters": [], "background": "night.jpg"}
    assert clone.character_name == ""
    assert clone.muted is True
    assert clone.audio_path == "original.wav"
    clone.muted = False
    assert clone.audio_path == "original.wav"
    assert Message.from_dict(clone.as_dict()).muted is False


def test_scene_survives_chat_backup(tmp_path):
    """场景与静音随对话备份恢复，无角色台词不需要角色目录占位。"""
    message = Message.from_dict({
        "text": "雨停了。", "character_name": "", "muted": True,
        "scene_before": {"visible_characters": [], "transition": transition_from_total()},
    })
    chat = Chat(name="场景回归", message_list=[message])
    backup = tmp_path / "scene.zip"
    with patch("chat.chat._reference_audio_dir", return_value=tmp_path):
        ChatManager([chat]).export_chats_to_backup([chat.chat_id], backup)
        restored = ChatManager().import_chats_from_backup(backup).imported_chats[0]
    assert restored.message_list[0].as_dict() == message.as_dict()


def test_scene_resolves_from_fixed_base_and_never_inherits_black():
    """任意索引重建完整舞台，不借用上次播放残留，也不继承黑场动作。"""
    messages = [
        {"scene_before": {"visible_characters": ["A"], "background": "night.jpg"}},
        Message.from_dict({"text": "画外音", "character_name": "B"}),
        {"scene_before": {"visible_characters": [], "transition": transition_from_total()}},
        {},
        {"scene_before": {"visible_characters": ["B"]}},
    ]
    original = deepcopy(messages)
    assert resolve_scene(messages, -1, ["A", "B"], "day.png") == {
        "visible_characters": ["A", "B"], "background": "day.png"}
    assert resolve_scene(messages, 1, ["A", "B"], "day.png") == {
        "visible_characters": ["A"], "background": "night.jpg"}
    assert resolve_scene(messages, 3, ["A", "B"], "day.png") == {
        "visible_characters": [], "background": "night.jpg"}
    assert resolve_scene(messages, 4, ["A", "B"], "day.png") == {
        "visible_characters": ["B"], "background": "night.jpg"}
    assert resolve_before(messages, 0, ["A", "B"], "other.png")["background"] == "other.png"
    assert resolve_before(messages, 2, ["A", "B"], "day.png")["visible_characters"] == ["A"]
    assert messages == original


def test_insert_keeps_scene_before_on_original_successor():
    """A 后插入 C 不搬移 B 的句前变化，C 沿用 A 的舞台。"""
    first = {"scene_before": {"visible_characters": ["A"]}}
    successor = {"scene_before": {
        "visible_characters": ["B"], "background": "night.jpg",
        "transition": transition_from_total(),
    }}
    messages = [first, {}, successor]
    assert resolve_scene(messages, 1, ["A", "B"], "day.png") == {
        "visible_characters": ["A"], "background": "day.png"}
    assert resolve_scene(messages, 2, ["A", "B"], "day.png") == {
        "visible_characters": ["B"], "background": "night.jpg"}
    assert SceneTiming.from_scene(messages[1]).duration_ms == 500
    assert SceneTiming.from_scene(successor["scene_before"]).duration_ms == 2000


def test_invalid_scene_projection_does_not_rewrite_source():
    """运行投影过滤未知角色、忽略损坏类型，保存的资源引用仍原样保留。"""
    messages = [
        {"scene_before": {"visible_characters": ["A", "unknown", "A"]}},
        {"scene_before": {"visible_characters": None, "background": 123}},
        {"scene_before": ["broken"]},
    ]
    saved = deepcopy(messages)
    state = resolve_scene(messages, 2, ["A", "B"], "default.png")
    assert state == {"visible_characters": ["A"], "background": "default.png"}
    assert messages == saved


@pytest.mark.parametrize("filename", [
    "", "../other.png", "..\\other.jpg", "folder/bg.png", "folder\\bg.png",
    "X:\\outside.png", "/outside.png", "bg.png:stream", "bg.jpeg", "bg.gif",
])
def test_background_rejects_non_root_image_references(tmp_path, filename):
    """背景引用拒绝子目录、路径穿越、绝对路径与不支持的扩展名。"""
    with pytest.raises(ValueError):
        background_path(filename, tmp_path)


def test_background_root_independent_of_cwd(tmp_path, monkeypatch):
    """默认背景始终从应用资源目录解析，允许大小写扩展名和暂时丢失的文件。"""
    expected = Path(__file__).resolve().parents[2] / "live2d_related"
    monkeypatch.chdir(tmp_path)
    assert background_root() == expected
    assert background_path("未找到.JPG") == expected / "未找到.JPG"


def test_background_rejects_link_to_external_file(tmp_path):
    """即使文件名合法，也不能借资源目录中的文件链接访问外部图片。"""
    directory = tmp_path / "resources"
    directory.mkdir()
    external = tmp_path / "outside.png"
    external.write_bytes(b"test")
    try:
        (directory / "linked.png").symlink_to(external)
    except OSError:
        pytest.skip("当前账户不允许创建符号链接")
    with pytest.raises(ValueError):
        background_path("linked.png", directory)


def test_normal_gap_is_separate_and_preserves_actor_buffer():
    """20 字无声台词完整播放 2500ms，之后上下场 300ms 再稳定 200ms。"""
    timing = SceneTiming.from_scene({"visible_characters": ["A"]})
    assert timing.duration_ms == 500
    assert timing.change_at_ms == 0
    assert timing.actors_progress(0) == 0
    assert timing.actors_progress(150) == 0.5
    assert timing.actors_progress(299) < 1
    assert timing.actors_progress(300) == timing.actors_progress(499) == 1
    assert timing.black_opacity(100) == 0
    assert not timing.finished(499)
    assert timing.finished(500)
    assert audio_span("", "字" * 20).duration_ms + timing.duration_ms == 3000


def test_black_gap_uses_one_clock_and_changes_only_under_black():
    """默认黑场不叠加普通间隔，上下场只在全黑阶段的前 300ms 进行。"""
    scene = {"transition": transition_from_total()}
    timing = SceneTiming.from_scene(scene)
    assert timing.duration_ms == 2000
    assert timing.change_at_ms == 600
    assert timing.black_opacity(300) == 0.5
    assert timing.black_opacity(600) == timing.black_opacity(1400) == 1
    assert timing.black_opacity(1700) == 0.5
    assert timing.black_opacity(2000) == 0
    assert timing.actors_progress(599) == 0
    assert timing.actors_progress(750) == 0.5
    assert timing.actors_progress(900) == 1
    assert not timing.finished(1999)
    assert timing.finished(2000)
    assert audio_span("", "字" * 20).duration_ms + timing.duration_ms == 4500


@pytest.mark.parametrize("total", [1, 500, 501, 502, 1000, 2000, 4000, 5000])
def test_black_total_clamping_preserves_room_for_actor_transition(total):
    """保留原黑场时长分配，300ms 上下场从全黑开始且不延长总时长。"""
    transition = transition_from_total(total)
    timing = SceneTiming.from_scene({"transition": transition})
    assert timing.duration_ms == max(500, min(4000, total))
    assert timing.hold_ms >= 200
    assert timing.fade_out_ms == timing.fade_in_ms
    assert timing.black_opacity(timing.change_at_ms + 100) == 1
    assert timing.actors_progress(timing.change_at_ms + 299) < 1
    assert timing.actors_progress(timing.change_at_ms + 300) == 1
    assert timing.change_at_ms + 300 <= timing.duration_ms


def test_short_black_keeps_original_timing_while_actors_finish_during_fade_in():
    """500ms 黑场保持 150/200/150，300ms 角色变化可与最后淡入阶段并行。"""
    scene = {"transition": {"type": "black", "fade_out_ms": 150,
                            "hold_ms": 200, "fade_in_ms": 150}}
    timing = SceneTiming.from_scene(scene)
    assert (timing.fade_out_ms, timing.hold_ms, timing.fade_in_ms) == (150, 200, 150)
    assert timing.duration_ms == 500
    assert timing.actors_progress(350) < 1
    assert timing.actors_progress(450) == 1
    assert timing.black_opacity(350) == 1
    assert 0 < timing.black_opacity(450) < 1
    assert scene["transition"]["hold_ms"] == 200


def test_first_sentence_has_neither_black_nor_default_gap():
    """首句即便存档中含黑场，也直接呈现目标场景并从句内零时刻开始。"""
    scene = {"transition": transition_from_total(), "visible_characters": []}
    timing = SceneTiming.from_scene(scene, first=True)
    assert timing.duration_ms == timing.change_at_ms == 0
    assert timing.black_opacity(0) == 0
    assert timing.actors_progress(0) == 1
    assert timing.finished(0)
    assert "transition" in scene


def test_broken_black_config_uses_default_without_mutation():
    """无效时长不会卡死句前阶段，运行时使用默认总时长而不改写原引用。"""
    scene = {"transition": {"type": "black", "fade_out_ms": float("inf")}}
    timing = SceneTiming.from_scene(scene)
    assert timing.duration_ms == 2000
    assert scene["transition"]["fade_out_ms"] == float("inf")
