"""复制剧本使用真实 ChatManager，保存与舞台占用使用隔离替身。"""
from copy import deepcopy
from types import MethodType
from unittest.mock import Mock
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from chat.chat import Chat, ChatManager, ChatType, Message, SmallTheaterPromptGenerator
from multi_char_main import ViewerGUI
from multi_char_playback_main_test import host as playback_host, take_command


@pytest.fixture
def host(playback_host, monkeypatch):
    host = playback_host
    source = host.current_chat
    source.type = ChatType.SMALL_THEATER
    source.name = "测试剧本"
    source.prompt_generator = SmallTheaterPromptGenerator(["A", "B"])
    source.message_list[0].audio_path = "shared.wav"
    source.message_list[0].performance_timeline = {"characters": {"A": {"motions": [], "expressions": []}}}
    source.update_theater_meta({"situation": "原情境"})
    host.chat_manager = ChatManager([source])
    for name in ("clone_theater_chat", "_clone_reserved_chat"):
        setattr(host, name, MethodType(getattr(ViewerGUI, name), host))

    def switch(chat):
        host.current_chat = chat
        host.refresh_chat_display()
        host.refresh_chat_list()
        host.load_playback_script()

    host.switch_chat = Mock(side_effect=switch)
    monkeypatch.setattr("multi_char_main.QTimer.singleShot", Mock())
    monkeypatch.setattr("multi_char_main.QMessageBox.warning", Mock())
    return host


def reserve(host, source=None):
    host.clone_theater_chat(source or host.current_chat)
    return take_command(host)


def accept(host, request, **changes):
    result = dict(request, accepted=True, phase="ready", busy=True, auto=False)
    result.update(changes)
    host.receive_playback_state(result)


def test_clone_saves_before_switching_and_preserves_independent_content(host):
    source = host.current_chat
    original = deepcopy(source.to_dict())
    request = reserve(host)
    assert request["action"] == "reserve_editor"
    host._save_chat.assert_not_called()
    assert host.chat_manager.chat_list == [source]
    accept(host, request)
    clone = host.current_chat
    assert clone is not source and clone.chat_id != source.chat_id
    assert clone.name == "测试剧本 副本"
    assert host.chat_manager.chat_list == [source, clone]
    host._save_chat.assert_called_once()
    host.switch_chat.assert_called_once_with(clone)
    assert clone.message_list[0].audio_path == "shared.wav"
    assert clone.message_list[0].scene_before == source.message_list[0].scene_before
    clone.message_list[0].text = "改写"
    clone.message_list[0].scene_before["background"] = "new.png"
    clone.message_list[0].performance_timeline["characters"]["A"]["motions"].append({"at_ms": 0})
    clone.meta.theater.situation = "新情境"
    assert source.to_dict() == original
    commands = []
    while not host.to_live2d_change_character_queue.empty():
        commands.append(take_command(host))
    assert [command["action"] for command in commands] == ["load", "release_editor"]
    assert commands[0]["session"] == clone.chat_id


def test_copy_noncurrent_row_and_unique_name(host):
    active = host.current_chat
    source = Chat(name="另一剧本", type_=ChatType.SMALL_THEATER)
    existing = Chat(name="另一剧本 副本", type_=ChatType.SMALL_THEATER)
    host.chat_manager.chat_list.extend([source, existing])
    request = reserve(host, source)
    assert host.current_chat is active
    accept(host, request)
    clone = host.current_chat
    assert clone.name != existing.name and clone.name.startswith("另一剧本 副本")
    assert host.chat_manager.chat_list == [active, source, clone, existing]
    assert clone.message_list == []


@pytest.mark.parametrize("save_error", [False, True])
def test_save_failure_rolls_back_new_chat_and_selection(host, save_error):
    source = host.current_chat
    chats = host.chat_manager.chat_list
    host._save_chat.side_effect = RuntimeError("write failure") if save_error else None
    host._save_chat.return_value = False
    request = reserve(host)
    accept(host, request)
    assert host.chat_manager.chat_list is chats
    assert chats == [source]
    assert host.current_chat is source
    host.switch_chat.assert_not_called()
    assert take_command(host)["action"] == "release_editor"


@pytest.mark.parametrize("change", ["chat", "removed", "generation", "audio", "block", "stage"])
def test_changed_context_before_ack_cannot_clone(host, change):
    request = reserve(host)
    if change == "chat":
        host.current_chat = Chat()
    elif change == "removed":
        host.chat_manager.chat_list.clear()
    elif change == "generation":
        host._generation_context = {}
    elif change == "audio":
        host._audio_regeneration = object()
    elif change == "block":
        host._playback_blocked_reasons.add("audio_fill")
    else:
        host._playback_stage_available = False
    accept(host, request)
    host._save_chat.assert_not_called()
    host.switch_chat.assert_not_called()


@pytest.mark.parametrize("reply", ["stale", "refused", "timeout"])
def test_invalid_reservation_never_copies(host, reply):
    request = reserve(host)
    if reply == "stale":
        accept(host, request, revision=request["revision"] - 1)
    elif reply == "refused":
        accept(host, request, accepted=False)
    else:
        host._expire_editor_request(request["request_id"])
        accept(host, request)
    host._save_chat.assert_not_called()
    host.switch_chat.assert_not_called()
    assert len(host.chat_manager.chat_list) == 1


@pytest.mark.parametrize("busy", ["playing", "auto", "generation", "audio", "fill"])
def test_busy_operation_rejects_copy_before_reservation(host, busy):
    if busy == "playing":
        host._playback_status["busy"] = True
    elif busy == "auto":
        host._playback_status["auto"] = True
    elif busy == "generation":
        host._generation_context = {}
    elif busy == "audio":
        host._audio_regeneration = object()
    else:
        host._audio_fill = {}
    host.clone_theater_chat(host.current_chat)
    assert host.to_live2d_change_character_queue.empty()
    host._save_chat.assert_not_called()


def test_only_current_version_updates_highlight_and_load_clears_it(host):
    host._playback_highlight = Mock()
    receipt = {"session": host._playback_session, "revision": host._playback_revision,
               "phase": "playing", "current_index": 0, "busy": True}
    host.receive_playback_state(dict(receipt, revision=receipt["revision"] - 1))
    host._playback_highlight.update_state.assert_not_called()
    host.receive_playback_state(receipt)
    host._playback_highlight.update_state.assert_called_once_with(receipt)
    host.load_playback_script()
    host._playback_highlight.reset.assert_called_once()
