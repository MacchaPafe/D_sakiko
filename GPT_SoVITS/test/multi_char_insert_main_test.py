"""主控添加台词的真实预约、保存事务、菜单和正式进度回归。"""
from copy import deepcopy
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from multi_char_playback_main_test import app, host as playback_host, take_command
from multi_char_delete_main_test import accept, connected_player, invalidate, remaining_commands
from chat.chat import Message
from multi_char_main import InteractiveChatBrowser, ViewerGUI
from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QDialog


@pytest.fixture
def host(playback_host, app, monkeypatch):
    """只绑定真实新增入口；保存始终使用内存回调。"""
    import multi_char_main
    for name in ("insert_sentence", "_insert_reserved_sentence"):
        setattr(playback_host, name, MethodType(getattr(ViewerGUI, name), playback_host))
    playback_host.regenerate_audio = Mock()
    monkeypatch.setattr(multi_char_main.QTimer, "singleShot", Mock())
    return playback_host


@pytest.fixture
def dialog(monkeypatch):
    """替换表单交互，真实消息初始化和持久化事务仍执行生产代码。"""
    import multi_char_main
    from ui_small_theater import multi_char_insert_dialog
    data = {"character_name": "B", "text": "  插入的新台词  ", "translation": "translation",
            "muted": False, "emotion": "normal", "audio_path": ""}
    value = Mock()
    value.exec_.return_value = QDialog.Accepted
    value.message_data.side_effect = lambda: dict(data)
    factory, warning = Mock(return_value=value), Mock()
    monkeypatch.setattr(multi_char_insert_dialog, "MultiCharInsertDialog", factory)
    monkeypatch.setattr(multi_char_main.QMessageBox, "warning", warning)
    return SimpleNamespace(value=value, factory=factory, warning=warning, data=data)


@pytest.mark.parametrize("after", [False, True])
@pytest.mark.parametrize("speaker,muted", [("B", False), ("", True)])
def test_insert_before_or_after_keeps_fields_and_does_not_migrate_anchor_scene(host, dialog, after, speaker, muted):
    original = list(host.current_chat.message_list)
    anchor = original[0]
    anchor.audio_path = "existing.wav"
    anchor.performance_timeline = {"characters": {"A": {"motions": [{"at_ms": 100}], "expressions": []}}}
    before = deepcopy(anchor.as_dict())
    dialog.data.update(character_name=speaker, muted=muted)
    host.insert_sentence(0, after)
    reserve = take_command(host)
    assert reserve["action"] == "reserve_editor"
    dialog.factory.assert_not_called()
    host._save_chat.assert_not_called()
    accept(host, reserve)
    dialog.factory.assert_called_once_with(["A", "B"], "A", host)
    dialog.value.deleteLater.assert_called_once_with()
    inserted = host.current_chat.message_list[int(after)]
    assert inserted is not anchor and inserted.character_name == speaker
    assert inserted.text == dialog.data["text"] and inserted.translation == "translation"
    assert inserted.muted is muted and inserted.audio_path == ""
    assert not inserted.scene_before
    assert set(inserted.performance_timeline["characters"]) == {"A", "B"}
    assert all(set(tracks) == {"motions", "expressions"}
               for tracks in inserted.performance_timeline["characters"].values())
    assert anchor.as_dict() == before
    assert host.current_chat.message_list == ([anchor, inserted, original[1]] if after else [inserted] + original)
    host._save_chat.assert_called_once_with()
    host.regenerate_audio.assert_not_called()
    load, release = remaining_commands(host)
    assert load["action"] == "load" and load["preserve_scene"] is True
    assert load["messages"] == [message.as_dict() for message in host.current_chat.message_list]
    assert release["action"] == "release_editor" and release["reservation_id"] == reserve["request_id"]
    host.refresh_chat_display.assert_called_once_with()
    host.refresh_chat_list.assert_called_once_with()
    assert host.to_live2d_module_queue.empty()


@pytest.mark.parametrize("outcome", ["cancel", "blank", "save_false", "save_error"])
def test_insert_cancel_blank_or_failed_save_preserves_script_and_releases(host, dialog, outcome):
    original, revision = list(host.current_chat.message_list), host._playback_revision
    if outcome == "cancel":
        dialog.value.exec_.return_value = QDialog.Rejected
    elif outcome == "blank":
        dialog.data["text"] = " \n\t "
    elif outcome == "save_false":
        host._save_chat.return_value = False
    else:
        host._save_chat.side_effect = OSError("disk unavailable")
    host.insert_sentence(0, False)
    reserve = take_command(host)
    accept(host, reserve)
    assert host.current_chat.message_list == original
    assert host._playback_revision == revision
    dialog.value.deleteLater.assert_called_once_with()
    commands = remaining_commands(host)
    assert len(commands) == 1 and commands[0]["action"] == "release_editor"
    assert commands[0]["reservation_id"] == reserve["request_id"]
    assert host._save_chat.call_count == (outcome in ("save_false", "save_error"))
    assert dialog.warning.call_count == (outcome == "save_error")
    host.refresh_chat_display.assert_not_called()
    host.refresh_chat_list.assert_not_called()


def test_insert_only_opens_dialog_after_matching_accepted_reservation(host, dialog):
    host.insert_sentence(1, True)
    reserve = take_command(host)
    accept(host, reserve, revision=reserve["revision"] - 1)
    dialog.factory.assert_not_called()
    assert host._editor_request is not None
    accept(host, reserve, accepted=False)
    dialog.factory.assert_not_called()
    host._save_chat.assert_not_called()
    assert not host._playback_pending and host._editor_request is None
    assert host.to_live2d_change_character_queue.empty()


def test_expired_insert_ack_cannot_reopen_dialog(host, dialog):
    host.insert_sentence(0, True)
    reserve = take_command(host)
    host._expire_editor_request(reserve["request_id"])
    assert take_command(host)["reservation_id"] == reserve["request_id"]
    accept(host, reserve)
    dialog.factory.assert_not_called()
    host._save_chat.assert_not_called()
    assert take_command(host)["reservation_id"] == reserve["request_id"]


@pytest.mark.parametrize("reason", ["chat", "message", "generation", "audio", "worker", "blocked"])
@pytest.mark.parametrize("when", ["before_ack", "in_dialog"])
def test_invalidated_anchor_or_tasks_prevent_insert(host, dialog, reason, when):
    chat = host.current_chat
    host.insert_sentence(0, False)
    reserve = take_command(host)
    if when == "before_ack":
        invalidate(host, reason)
    else:
        def confirm():
            invalidate(host, reason)
            return QDialog.Accepted
        dialog.value.exec_.side_effect = confirm
    accept(host, reserve)
    assert len(chat.message_list) == 2
    host._save_chat.assert_not_called()
    assert dialog.factory.call_count == (when == "in_dialog")
    assert take_command(host)["reservation_id"] == reserve["request_id"]
    assert host.to_live2d_change_character_queue.empty()


@pytest.mark.parametrize("reason", ["stage", "revision", "session"])
def test_stage_or_script_identity_changes_during_form_cancel_insert(host, dialog, reason):
    original = list(host.current_chat.message_list)
    def confirm():
        invalidate(host, reason)
        return QDialog.Accepted
    dialog.value.exec_.side_effect = confirm
    host.insert_sentence(0, True)
    reserve = take_command(host)
    accept(host, reserve)
    assert host.current_chat.message_list == original
    host._save_chat.assert_not_called()
    host.refresh_chat_display.assert_not_called()
    commands = remaining_commands(host)
    assert len([item for item in commands if item["action"] == "load"]) == (reason == "revision")
    if reason != "stage":
        assert commands[-1]["reservation_id"] == reserve["request_id"]
    else:
        assert commands == []


@pytest.mark.parametrize("busy", ["playing", "transition", "generation", "audio", "pending", "stage"])
def test_insert_entry_rechecks_playback_and_task_occupancy(host, dialog, busy):
    if busy in ("playing", "transition"):
        host._playback_status.update(phase=busy, busy=True)
    elif busy == "pending":
        host._playback_pending.add("loading")
    else:
        invalidate(host, busy)
    host.insert_sentence(0, False)
    assert host.to_live2d_change_character_queue.empty()
    dialog.factory.assert_not_called()


@pytest.mark.parametrize("after", [False, True])
def test_insert_menu_emits_correct_relative_position(host, dialog, app, monkeypatch, after):
    import multi_char_main
    browser = InteractiveChatBrowser()
    browser.playback_available = host.playback_available
    browser.insert_signal.connect(lambda index, following: host.insert_sentence(index, following))
    monkeypatch.setattr(browser, "anchorAt", lambda point: "replay:1")
    def choose(menu, position):
        submenu = next(action.menu() for action in menu.actions() if action.text() == "添加台词")
        assert submenu.isEnabled()
        assert [action.text() for action in submenu.actions()] == ["本句之前", "本句之后"]
        return submenu.actions()[int(after)]
    monkeypatch.setattr(multi_char_main.QMenu, "exec_", choose)
    try:
        browser.contextMenuEvent(SimpleNamespace(pos=lambda: QPoint(4, 4)))
        assert take_command(host)["action"] == "reserve_editor"
        assert host._editor_request["after"] is after
        assert host._editor_request["message"] is host.current_chat.message_list[1]
        dialog.factory.assert_not_called()
    finally:
        browser.close()


def test_insert_menu_disables_when_busy_and_rechecks_old_enabled_action(host, dialog, app, monkeypatch):
    import multi_char_main
    browser = InteractiveChatBrowser()
    browser.playback_available = host.playback_available
    browser.insert_signal.connect(lambda index, after: host.insert_sentence(index, after))
    monkeypatch.setattr(browser, "anchorAt", lambda point: "replay:0")
    seen = []
    def choose(menu, position):
        submenu = next(action.menu() for action in menu.actions() if action.text() == "添加台词")
        seen.append(submenu.isEnabled())
        host._playback_status.update(phase="playing", busy=True)
        return submenu.actions()[0]
    monkeypatch.setattr(multi_char_main.QMenu, "exec_", choose)
    try:
        browser.contextMenuEvent(SimpleNamespace(pos=lambda: QPoint(4, 4)))
        browser.contextMenuEvent(SimpleNamespace(pos=lambda: QPoint(4, 4)))
        assert seen == [True, False]
        assert host.to_live2d_change_character_queue.empty()
        dialog.factory.assert_not_called()
    finally:
        browser.close()


@pytest.mark.parametrize("outcome", ["success", "cancel", "save_false", "save_error"])
def test_actual_stage_reservation_blocks_input_and_only_success_resets_progress(host, dialog, monkeypatch, outcome):
    player = connected_player(host, monkeypatch)
    scene = deepcopy(player.stage.state)
    progress = (player.controller.phase, player.controller.current_index, player.controller.pending_index)
    original = list(host.current_chat.message_list)
    if outcome == "save_false":
        host._save_chat.return_value = False
    elif outcome == "save_error":
        host._save_chat.side_effect = OSError("disk unavailable")
    def confirm():
        assert player.controller.reservation is not None
        assert not player.controller.click() and not player.controller.toggle_auto()
        assert not host.playback_available()
        return QDialog.Rejected if outcome == "cancel" else QDialog.Accepted
    dialog.value.exec_.side_effect = confirm
    host.insert_sentence(0, False)
    assert player.controller.command(take_command(host))
    commands = remaining_commands(host)
    assert [item["action"] for item in commands] == (["load", "release_editor"] if outcome == "success" else ["release_editor"])
    for command in commands:
        assert player.controller.command(command)
    assert player.controller.reservation is None and host.playback_available()
    assert not player.controller.auto and player.stage.state == scene
    if outcome == "success":
        assert (player.controller.phase, player.controller.current_index, player.controller.pending_index) == ("ready", None, 0)
        assert player.controller.messages[0]["text"] == dialog.data["text"]
        assert len(player.controller.messages) == 3
    else:
        assert (player.controller.phase, player.controller.current_index, player.controller.pending_index) == progress
        assert host.current_chat.message_list == original
    host.regenerate_audio.assert_not_called()
