"""主控单句删除的真实 IPC、保存失败及确认期间舞台互斥回归。"""
from copy import deepcopy
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from multi_char_playback_main_test import app, host as playback_host, take_command
from multi_char_scene_playback_test import Playback
from chat.chat import Chat, Message
from multi_char_main import InteractiveChatBrowser, ViewerGUI
from PyQt5.QtCore import QPoint


@pytest.fixture
def host(playback_host, app, monkeypatch):
    """复用内存主控，仅绑定本次新增的两个真实入口。"""
    import multi_char_main
    for name in ("delete_sentence", "_delete_reserved_sentence"):
        setattr(playback_host, name, MethodType(getattr(ViewerGUI, name), playback_host))
    monkeypatch.setattr(multi_char_main.QTimer, "singleShot", Mock())
    return playback_host


@pytest.fixture
def dialogs(monkeypatch):
    """确认框不进入用户桌面；删除及保存事务仍执行生产代码。"""
    import multi_char_main
    from ui_small_theater import multi_char_message_edit
    confirmation, warning = Mock(return_value=True), Mock()
    monkeypatch.setattr(multi_char_message_edit, "confirm_message_deletion", confirmation)
    monkeypatch.setattr(multi_char_main.QMessageBox, "warning", warning)
    return SimpleNamespace(confirm=confirmation, warning=warning)


def accept(host, request, **changes):
    """模拟舞台对同版请求的明确确认。"""
    result = dict(request, accepted=True, phase="ready", busy=True, auto=False)
    result.update(changes)
    host.receive_playback_state(result)


def remaining_commands(host):
    commands = []
    while not host.to_live2d_change_character_queue.empty():
        commands.append(take_command(host))
    return commands


def test_delete_waits_for_matching_ack_then_saves_and_loads_latest_script(host, dialogs):
    original = list(host.current_chat.message_list)
    revision = host._playback_revision
    host.delete_sentence(0)
    reserve = take_command(host)
    assert reserve["action"] == "reserve_editor"
    dialogs.confirm.assert_not_called()
    host._save_chat.assert_not_called()
    assert host.current_chat.message_list == original

    accept(host, reserve, revision=revision - 1)
    dialogs.confirm.assert_not_called()
    assert reserve["request_id"] in host._playback_pending
    accept(host, reserve)
    dialogs.confirm.assert_called_once_with(host, original[0])
    host._save_chat.assert_called_once_with()
    assert host.current_chat.message_list == original[1:]
    load, release = remaining_commands(host)
    assert load["action"] == "load" and load["revision"] > revision
    assert load["preserve_scene"] is True
    assert load["messages"] == [original[1].as_dict()]
    assert release["action"] == "release_editor"
    assert release["reservation_id"] == reserve["request_id"]
    host.refresh_chat_display.assert_called_once_with()
    host.refresh_chat_list.assert_called_once_with()
    assert host._editor_request is None


@pytest.mark.parametrize("outcome", ["cancel", "save_false", "save_error"])
def test_cancel_or_failed_save_releases_reservation_without_reloading(host, dialogs, outcome):
    original = list(host.current_chat.message_list)
    revision = host._playback_revision
    if outcome == "cancel":
        dialogs.confirm.return_value = False
    elif outcome == "save_false":
        host._save_chat.return_value = False
    else:
        host._save_chat.side_effect = OSError("disk unavailable")
    host.delete_sentence(1)
    reserve = take_command(host)
    accept(host, reserve)
    assert host.current_chat.message_list == original
    assert host._playback_revision == revision
    commands = remaining_commands(host)
    assert len(commands) == 1
    assert commands[0]["action"] == "release_editor"
    assert commands[0]["reservation_id"] == reserve["request_id"]
    host.refresh_chat_display.assert_not_called()
    host.refresh_chat_list.assert_not_called()
    assert host._save_chat.call_count == (outcome != "cancel")
    assert dialogs.warning.call_count == (outcome == "save_error")


def test_stage_rejection_does_not_open_confirmation_or_save(host, dialogs):
    host.delete_sentence(0)
    reserve = take_command(host)
    accept(host, reserve, accepted=False, phase="playing")
    dialogs.confirm.assert_not_called()
    host._save_chat.assert_not_called()
    assert host._editor_request is None and not host._playback_pending
    assert host.to_live2d_change_character_queue.empty()


def test_unrelated_and_expired_acceptance_never_deletes(host, dialogs):
    host.delete_sentence(0)
    reserve = take_command(host)
    accept(host, reserve, request_id="older-reservation")
    dialogs.confirm.assert_not_called()
    assert host._editor_request["request_id"] == reserve["request_id"]
    assert take_command(host)["reservation_id"] == "older-reservation"
    host._expire_editor_request(reserve["request_id"])
    assert take_command(host)["reservation_id"] == reserve["request_id"]
    accept(host, reserve)
    dialogs.confirm.assert_not_called()
    host._save_chat.assert_not_called()
    assert take_command(host)["reservation_id"] == reserve["request_id"]


def invalidate(host, reason):
    """模拟确认框处理嵌套事件时发生的真实失效条件。"""
    if reason == "chat":
        host.current_chat = Chat(message_list=[Message.from_dict({"text": "另一剧本"})])
    elif reason == "message":
        old = host.current_chat.message_list[0]
        host.current_chat.message_list[0] = Message.from_dict(old.as_dict())
    elif reason == "generation":
        host._generation_context = {"chat": host.current_chat}
    elif reason == "audio":
        host._audio_regeneration = {"chat": host.current_chat}
    elif reason == "worker":
        host.generate_thread = Mock(isRunning=Mock(return_value=True))
    elif reason == "editor":
        host.performance_editor = Mock()
    elif reason == "blocked":
        host._playback_blocked_reasons.add("audio")
    elif reason == "stage":
        host.on_stage_closed()
    elif reason == "revision":
        host.load_playback_script(preserve_scene=True)
    elif reason == "session":
        host._playback_session = "different-session"


@pytest.mark.parametrize("when", ["before_ack", "in_dialog"])
@pytest.mark.parametrize("reason", ["chat", "message", "generation", "audio", "worker", "editor", "blocked"])
def test_changed_target_or_tasks_abort_delete_before_and_after_confirmation(host, dialogs, when, reason):
    chat = host.current_chat
    host.delete_sentence(0)
    reserve = take_command(host)
    if when == "before_ack":
        invalidate(host, reason)
    else:
        def confirm(*args):
            invalidate(host, reason)
            return True
        dialogs.confirm.side_effect = confirm
    accept(host, reserve)
    assert len(chat.message_list) == 2
    host._save_chat.assert_not_called()
    assert dialogs.confirm.call_count == (when == "in_dialog")
    release = take_command(host)
    assert release["action"] == "release_editor"
    assert release["reservation_id"] == reserve["request_id"]
    assert host.to_live2d_change_character_queue.empty()


@pytest.mark.parametrize("reason", ["stage", "revision", "session"])
def test_stage_or_script_identity_change_during_confirmation_cancels_delete(host, dialogs, reason):
    original = list(host.current_chat.message_list)
    def confirm(*args):
        invalidate(host, reason)
        return True
    dialogs.confirm.side_effect = confirm
    host.delete_sentence(0)
    reserve = take_command(host)
    accept(host, reserve)
    assert host.current_chat.message_list == original
    host._save_chat.assert_not_called()
    host.refresh_chat_display.assert_not_called()
    commands = remaining_commands(host)
    assert len([item for item in commands if item["action"] == "load"]) == (reason == "revision")
    if reason != "stage":
        assert commands[-1]["action"] == "release_editor"
        assert commands[-1]["reservation_id"] == reserve["request_id"]
    else:
        assert commands == []


@pytest.mark.parametrize("busy", ["transition", "playing", "auto", "pending", "generation", "audio", "worker", "editor", "blocked", "stage"])
def test_delete_entry_refuses_busy_or_unavailable_stage(host, dialogs, busy):
    if busy in ("transition", "playing"):
        host._playback_status.update(phase=busy, busy=True)
    elif busy == "auto":
        host._playback_status["auto"] = True
    elif busy == "pending":
        host._playback_pending.add("pending-load")
    else:
        invalidate(host, busy)
    host.delete_sentence(0)
    assert host.to_live2d_change_character_queue.empty()
    dialogs.confirm.assert_not_called()
    host._save_chat.assert_not_called()


def test_delete_menu_is_last_with_icon_and_callback_rechecks_busy_state(host, dialogs, app, monkeypatch):
    import multi_char_main
    browser = InteractiveChatBrowser()
    browser.playback_available = host.playback_available
    browser.delete_signal.connect(lambda index: host.delete_sentence(index))
    monkeypatch.setattr(browser, "anchorAt", lambda point: "replay:1")
    enabled = []
    def choose(menu, position):
        actions = menu.actions()
        action = actions[-1]
        assert action.text() == "删除本句" and actions[-2].isSeparator()
        assert not action.icon().isNull()
        enabled.append(action.isEnabled())
        host._playback_status.update(phase="playing", busy=True)
        return action
    monkeypatch.setattr(multi_char_main.QMenu, "exec_", choose)
    try:
        browser.contextMenuEvent(SimpleNamespace(pos=lambda: QPoint(4, 4)))
        browser.contextMenuEvent(SimpleNamespace(pos=lambda: QPoint(4, 4)))
        assert enabled == [True, False]
        assert host.to_live2d_change_character_queue.empty()
        dialogs.confirm.assert_not_called()
    finally:
        browser.close()


def connected_player(host, monkeypatch):
    """将真实状态机回执接回真实 Qt 主控方法，仍隔离音频、OpenGL 和存档。"""
    player = Playback(monkeypatch)
    player.controller.send = host.receive_playback_state
    player.controller.command({"action": "load", "session": host._playback_session,
                               "revision": host._playback_revision,
                               "messages": [item.as_dict() for item in host.current_chat.message_list]})
    assert player.controller.click()
    player.step(20000)
    assert player.controller.phase == "ready"
    assert player.controller.pending_index == 1
    return player


@pytest.mark.parametrize("outcome", ["success", "cancel", "save_false", "save_error"])
def test_actual_reservation_blocks_stage_input_and_only_success_resets_progress(host, dialogs, monkeypatch, outcome):
    player = connected_player(host, monkeypatch)
    state_before = deepcopy(player.stage.state)
    original = list(host.current_chat.message_list)
    before = (player.controller.phase, player.controller.current_index, player.controller.pending_index)
    if outcome == "save_false":
        host._save_chat.return_value = False
    elif outcome == "save_error":
        host._save_chat.side_effect = OSError("disk unavailable")
    def confirm(*args):
        assert player.controller.reservation is not None
        assert not player.controller.click()
        assert not player.controller.toggle_auto()
        assert not host.playback_available()
        return outcome != "cancel"
    dialogs.confirm.side_effect = confirm
    host.delete_sentence(0)
    reserve = take_command(host)
    assert player.controller.command(reserve)
    commands = remaining_commands(host)
    assert [item["action"] for item in commands] == (["load", "release_editor"] if outcome == "success" else ["release_editor"])
    for command in commands:
        assert player.controller.command(command)
    assert player.controller.reservation is None
    assert not player.controller.auto and host.playback_available()
    assert player.stage.state == state_before
    if outcome == "success":
        assert (player.controller.phase, player.controller.current_index, player.controller.pending_index) == ("ready", None, 0)
        assert player.controller.messages == [original[1].as_dict()]
        assert player.controller.click()
        assert player.started()[-1][1] == original[1].text
        assert player.stage.state["visible_characters"] == ["A", "B"]
        assert player.stage.state["background"] == "default.png"
    else:
        assert (player.controller.phase, player.controller.current_index, player.controller.pending_index) == before
        assert host.current_chat.message_list == original


def test_runtime_rejects_delete_if_stage_started_before_reservation_arrives(host, dialogs, monkeypatch):
    player = connected_player(host, monkeypatch)
    host.delete_sentence(0)
    reserve = take_command(host)
    assert player.controller.click()
    assert not player.controller.command(reserve)
    dialogs.confirm.assert_not_called()
    host._save_chat.assert_not_called()
    assert host._editor_request is None and not host._playback_pending
    assert player.controller.active and player.controller.reservation is None


def test_deleting_only_sentence_loads_empty_script_and_stage_input_stays_idle(host, dialogs, monkeypatch):
    host.current_chat.message_list[:] = host.current_chat.message_list[:1]
    player = Playback(monkeypatch)
    player.controller.send = host.receive_playback_state
    player.controller.command({"action": "load", "session": host._playback_session,
                               "revision": host._playback_revision,
                               "messages": [item.as_dict() for item in host.current_chat.message_list]})
    scene = deepcopy(player.stage.state)
    host.delete_sentence(0)
    assert player.controller.command(take_command(host))
    for command in remaining_commands(host):
        assert player.controller.command(command)
    assert host.current_chat.message_list == player.controller.messages == []
    assert player.controller.phase == "empty" and player.controller.current_index is None
    assert player.controller.pending_index == 0 and player.controller.reservation is None
    assert player.stage.state == scene
    assert not player.controller.click() and not player.controller.toggle_auto()
