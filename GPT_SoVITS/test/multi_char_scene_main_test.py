"""小剧场场景编排的主控制通信测试，不读取或写入真实剧本。"""
import copy
import os
import sys
from pathlib import Path
from queue import Queue
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QTextDocument
from PyQt5.QtWidgets import QApplication
from chat.chat import Chat, Message, SmallTheaterPromptGenerator
from multi_char_main import ViewerGUI


@pytest.fixture(scope="module")
def app():
    """为文本渲染与弹窗替身提供离屏 Qt 应用。"""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host():
    """保留真实主控方法，隔离音频线程、持久化与播放进程。"""
    messages = [
        Message.from_dict({
            "character_name": "A", "text": "前句",
            "scene_before": {"visible_characters": ["A"], "background": "school.png"},
        }),
        Message.from_dict({
            "character_name": "", "text": "夜色渐深。", "translation": "",
            "muted": True,
            "scene_before": {"visible_characters": []},
            "performance_timeline": {"characters": {}},
        }),
    ]
    result = SimpleNamespace(
        current_chat=Chat(message_list=messages),
        character_list=[SimpleNamespace(character_name=name, has_valid_voice_model=Mock(return_value=True))
                        for name in ("A", "B")],
        audio_gen_module=SimpleNamespace(audio_language_choice=""),
        sync_live2d_active_slots=Mock(), display_live2d_message=Mock(),
        message_queue=Queue(), performance_editor=None,
        _save_chat=Mock(return_value=True), regenerate_audio_continue=Mock(),
    )
    for name in ("_single_replay_payload", "_find_character_by_name", "finish_audio_regeneration"):
        setattr(result, name, MethodType(getattr(ViewerGUI, name), result))
    return result


def test_turn_roundtrip_preserves_scene_and_mute_without_runtime_context(host):
    """通信恢复保留空角色和新配置，不把用于定位的整段上下文写回存档。"""
    payload = host._single_replay_payload(1)
    restored = ViewerGUI._message_from_turn_dict(payload)
    assert restored.character_name == ""
    assert restored.muted
    assert restored.scene_before == {"visible_characters": []}
    assert "scene_context" not in restored.as_dict()
    payload["scene_before"]["visible_characters"].append("B")
    assert restored.scene_before == {"visible_characters": []}
    assert host.current_chat.message_list[1].scene_before == {"visible_characters": []}


def test_single_replay_keeps_prior_scene_and_current_target(host):
    """回放无角色消息仍发送台词，并带上前句舞台及正确的整段下标。"""
    before = [message.as_dict() for message in host.current_chat.message_list]
    ViewerGUI.on_link_clicked(host, QUrl("replay:1"))
    payload = host.display_live2d_message.call_args.args[0][0]
    assert payload["character_name"] == ""
    assert payload["scene_context"] == {"messages": before, "index": 1}
    assert payload["scene_context"]["messages"][0]["scene_before"]["background"] == "school.png"
    assert [message.as_dict() for message in host.current_chat.message_list] == before
    assert all("scene_context" not in message for message in payload["scene_context"]["messages"])
    host.sync_live2d_active_slots.assert_called_once()


@pytest.mark.parametrize("speaker,expected", [("", "夜色 <渐深>。"), ("A", "★A：夜色 <渐深>。")])
def test_unowned_text_has_no_name_prefix_or_placeholder(app, speaker, expected):
    """无角色行直接显示文本，特殊字符不会被当作 HTML 吞掉。"""
    message = Message.from_dict({"character_name": speaker, "text": "夜色 <渐深>。"})
    original, translation = ViewerGUI.render_message_html(3, message)
    document = QTextDocument()
    document.setHtml(original)
    assert document.toPlainText() == expected
    assert 'href="replay:3"' in original
    assert translation == ""


def test_unowned_message_cannot_borrow_voice(host, monkeypatch):
    """有可用角色声线时，无角色消息仍拒绝生成，不借用第一个角色。"""
    import multi_char_main
    notice = Mock()
    monkeypatch.setattr(multi_char_main.QMessageBox, "information", notice)
    assert not ViewerGUI.regenerate_audio(host, 1)
    host.regenerate_audio_continue.assert_not_called()
    assert not hasattr(host, "regenerate_character")
    assert notice.call_args.args[2] == "这句台词未选择语音角色，无法生成音频。"


def test_muted_actor_can_generate_without_unmuting(host):
    """静音角色仍可生成自己的音频，生成入口不会自动取消静音。"""
    message = host.current_chat.message_list[1]
    message.character_name = "B"
    assert ViewerGUI.regenerate_audio(host, 1)
    assert message.muted
    assert host.regenerate_character is host.character_list[1]
    host.regenerate_audio_continue.assert_called_once()


def test_empty_name_not_added_to_cast_or_resolved_as_character(host):
    """空角色不占用双人角色名单，也不匹配不完整角色配置。"""
    host.current_chat.prompt_generator = SmallTheaterPromptGenerator(["", "A", "B"])
    assert ViewerGUI._chat_character_names(host, host.current_chat) == ["A", "B"]
    host.character_list.insert(0, SimpleNamespace(character_name=""))
    assert ViewerGUI._find_character_by_name(host, "") is None


def test_scene_load_error_is_notice_not_script_message(host):
    """舞台加载失败只进入提示框，不触发新台词存储。"""
    before = list(host.current_chat.message_list)
    ViewerGUI.display_this_turn_text(host, {
        "type": "multi_char_scene_error", "error": "背景加载失败，已保留原背景。",
    })
    assert host.message_queue.get_nowait() == "背景加载失败，已保留原背景。"
    assert host.current_chat.message_list == before


def test_transition_receipt_goes_to_editor_without_creating_message(host):
    """句前预览回执只更新编辑器状态，不能作为空台词加入正式剧本。"""
    before = list(host.current_chat.message_list)
    host.performance_editor = SimpleNamespace(receive=Mock())
    receipt = {"type": "multi_char_transition", "session": "editor", "sequence": 2, "playing": True}
    ViewerGUI.display_this_turn_text(host, receipt)
    host.performance_editor.receive.assert_called_once_with(receipt)
    assert host.current_chat.message_list == before


def test_new_audio_preserves_scene_and_mute_and_replays_with_context(host, tmp_path):
    """音频独立保存且不改变舞台与静音，成功回放仍有完整场景定位。"""
    import soundfile as sf
    message = host.current_chat.message_list[1]
    message.character_name = "B"
    old_scene = copy.deepcopy(message.scene_before)
    audio_path = tmp_path / "new.wav"
    sf.write(str(audio_path), [0.0] * 4000, 8000)
    context = (host.current_chat, message, message.text, message.audio_path, object())
    host._audio_regeneration = context
    host._audio_regeneration_callback = Mock()
    ViewerGUI.replace_audio_path(host, [str(audio_path)], context)
    assert message.audio_path == str(audio_path)
    assert message.muted
    assert message.scene_before == old_scene
    payload = host.display_live2d_message.call_args.args[0][0]
    assert payload["scene_context"]["index"] == 1
    assert payload["muted"]
    assert payload["audio_path"] == str(audio_path)
