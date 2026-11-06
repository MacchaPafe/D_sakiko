"""正式播放的 Qt 通信、操作互斥与迟到回执测试，不连接真实舞台或存档。"""
import os
import sys
from copy import deepcopy
from pathlib import Path
from queue import Queue
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtCore import QPoint, QUrl
from PyQt5.QtWidgets import QApplication

from chat.chat import Chat, Message
from multi_char_main import InteractiveChatBrowser, ViewerGUI


@pytest.fixture(scope="module")
def app():
    """为真实菜单和信号提供离屏应用。"""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host():
    """绑定真实主控通信方法，隔离窗口、模型及用户数据。"""
    chat = Chat(message_list=[
        Message.from_dict({"character_name": "A", "text": "前句", "scene_before": {
            "visible_characters": ["A"], "background": "school.png",
        }}),
        Message.from_dict({"character_name": "", "text": "夜色渐深。", "muted": True}),
    ])
    value = SimpleNamespace(
        current_chat=chat, chat_manager=SimpleNamespace(chat_list=[chat]),
        _playback_session=chat.chat_id, _playback_revision=1,
        _playback_status={"phase": "ready", "busy": False, "auto": False,
                          "current_index": 0, "pending_index": 1},
        _playback_pending=set(), _playback_stage_available=True,
        _playback_blocked_reasons=set(), _editor_request=None,
        playback_idle_value=SimpleNamespace(value=True),
        _generation_context=None, _audio_regeneration=None,
        pending_turn_uids=set(), performance_editor=None, generate_thread=None,
        generate_btn=Mock(isEnabled=Mock(return_value=True)),
        to_live2d_change_character_queue=Queue(), to_live2d_module_queue=Queue(),
        message_queue=Queue(), messages_box=Mock(),
        _build_active_slots_payload=Mock(return_value={
            "type": "set_active_slots", "slots": [
                {"character_name": name, "model_json_path": None} for name in ("A", "B")
            ], "preserve_playback": True, "motion_facing_mode": "screen",
        }),
        _prepare_live2d_model_for_switch=Mock(return_value=True),
        _refresh_open_settings_dialog=Mock(),
        setEnabled=Mock(), raise_=Mock(),
        refresh_chat_display=Mock(), refresh_chat_list=Mock(),
        _save_chat=Mock(return_value=True),
    )
    for name in (
        "load_playback_script", "send_playback_command", "playback_available",
        "request_sentence_playback", "start_playback_from", "on_link_clicked",
        "receive_playback_state", "set_playback_block", "performance_editor_finished",
        "open_performance_editor", "_open_reserved_editor", "display_this_turn_text",
        "_expire_editor_request", "on_stage_closed",
    ):
        setattr(value, name, MethodType(getattr(ViewerGUI, name), value))
    value._turn_dict_from_message = Message.as_dict
    return value


def take_command(host):
    """读取唯一新命令，额外命令由各测试显式检查。"""
    return host.to_live2d_change_character_queue.get_nowait()


def test_loading_script_is_waiting_snapshot_and_revision_changes(host):
    """装载包含最新消息但不发自动启动，重新装载使旧版本失效。"""
    host.load_playback_script()
    first = take_command(host)
    assert first["type"] == "multi_char_playback" and first["action"] == "load"
    assert first["messages"] == [message.as_dict() for message in host.current_chat.message_list]
    assert first["session"] == host.current_chat.chat_id
    host.current_chat.message_list[0].text = "保存的新文本"
    host.load_playback_script(preserve_scene=True)
    second = take_command(host)
    assert second["revision"] > first["revision"]
    assert second["preserve_scene"] is True
    assert second["messages"][0]["text"] == "保存的新文本"
    assert first["messages"][0]["text"] == "前句"
    assert host.to_live2d_module_queue.empty()
    assert host.to_live2d_change_character_queue.empty()


@pytest.mark.parametrize("automatic,action", [(False, "single"), (True, "auto_from")])
def test_idle_sentence_request_uses_latest_script_without_changing_storage(host, automatic, action):
    """两种入口发送明确模式、目标下标与最新完整场景，且不改动用户剧本。"""
    host.current_chat.message_list[1].scene_before = {"visible_characters": []}
    before = [message.as_dict() for message in host.current_chat.message_list]
    host.request_sentence_playback(1, automatic=automatic)
    command = take_command(host)
    assert command["action"] == action and command["index"] == 1
    assert command["messages"] == before
    assert command["messages"][1]["character_name"] == ""
    assert command["messages"][1]["muted"] is True
    assert [message.as_dict() for message in host.current_chat.message_list] == before
    assert host.to_live2d_module_queue.empty()


@pytest.mark.parametrize("phase", ["transition", "playing"])
@pytest.mark.parametrize("automatic", [False, True])
def test_busy_sentence_requests_are_rejected_without_queueing(host, phase, automatic):
    """音频或过渡忙碌时直接拒绝，不维护稍后执行的播放命令。"""
    host._playback_status.update(phase=phase, busy=True)
    host.playback_idle_value.value = False
    host.request_sentence_playback(0, automatic=automatic)
    assert host.to_live2d_change_character_queue.empty()
    assert host.to_live2d_module_queue.empty()
    assert not host._playback_pending


@pytest.mark.parametrize("block", ["pending", "generation", "audio", "editor", "closed"])
def test_non_playback_occupancy_also_blocks_sentence_requests(host, block):
    """正在装载、生成、合成或编排时不能趁舞台上一帧空闲发起回放。"""
    if block == "pending":
        host._playback_pending.add("unacknowledged")
    elif block == "generation":
        host._generation_context = {"chat": host.current_chat}
    elif block == "audio":
        host._audio_regeneration = (host.current_chat,)
    elif block == "editor":
        host.performance_editor = object()
    else:
        host._playback_stage_available = False
    host.request_sentence_playback(0)
    assert host.to_live2d_change_character_queue.empty()


@pytest.mark.parametrize("mismatch", ["session", "revision"])
def test_stale_status_cannot_unlock_or_mutate_current_script(host, mismatch):
    """旧剧本空闲回执不能清除当前请求、改变当前状态或追加消息。"""
    host._playback_status["busy"] = True
    host._playback_pending.add("new-request")
    before = deepcopy(host._playback_status)
    messages = list(host.current_chat.message_list)
    receipt = {
        "type": "multi_char_playback", "session": host._playback_session,
        "revision": host._playback_revision, "busy": False, "phase": "ready",
        "request_id": "new-request", "action": "single", "accepted": True,
    }
    receipt[mismatch] = "other-chat" if mismatch == "session" else host._playback_revision - 1
    host.display_this_turn_text(receipt)
    assert host._playback_status == before
    assert host._playback_pending == {"new-request"}
    assert host.current_chat.message_list == messages


def test_formal_turn_receipt_never_appends_to_chat(host):
    """正式台词回执只描述运行状态，不能被旧正文去重路径写回剧本。"""
    before = list(host.current_chat.message_list)
    host.display_this_turn_text({
        "type": "multi_char_playback_turn", "session": host._playback_session,
        "revision": host._playback_revision, "text": "不是新增消息", "character_name": "A",
    })
    assert host.current_chat.message_list == before


def test_editor_exit_loads_saved_changes_only_and_preserves_restored_scene(host):
    """退出重新序列化插句等已保存数据，首句待播但不覆盖刚恢复的舞台。"""
    host.current_chat.message_list.insert(1, Message.from_dict({"text": "新插入的句子", "muted": True}))
    host.performance_editor_finished(host.current_chat)
    commands = []
    while not host.to_live2d_change_character_queue.empty():
        commands.append(take_command(host))
    loads = [command for command in commands if command["action"] == "load"]
    assert len(loads) == 1
    assert loads[0]["preserve_scene"] is True
    assert [message["text"] for message in loads[0]["messages"]] == ["前句", "新插入的句子", "夜色渐深。"]
    assert not any(command["action"] in ("single", "auto_from") for command in commands)


def test_stale_editor_exit_cannot_replace_current_chat(host):
    """迟到的旧编辑器退出不把另一份剧本重新送到当前舞台。"""
    host.performance_editor_finished(Chat(message_list=[Message.from_dict({"text": "旧剧本"})]))
    assert host.to_live2d_change_character_queue.empty()


def test_auto_from_menu_has_busy_gate_and_callback_rechecks_state(host, app, monkeypatch):
    """菜单打开后舞台开始播放，旧菜单点选仍被主控拒绝。"""
    import multi_char_main
    browser = InteractiveChatBrowser()
    browser.playback_available = host.playback_available
    browser.auto_from_signal.connect(lambda index: host.start_playback_from(index))
    monkeypatch.setattr(browser, "anchorAt", lambda point: "replay:1")
    seen = []

    def choose(menu, position):
        """检查实际菜单名称和初始可用性，随后模拟舞台抢先进入播放。"""
        action = next(action for action in menu.actions() if action.text() == "从此句自动播放")
        seen.append(action.isEnabled())
        host._playback_status.update(phase="playing", busy=True)
        host.playback_idle_value.value = False
        return action

    monkeypatch.setattr(multi_char_main.QMenu, "exec_", choose)
    browser.contextMenuEvent(SimpleNamespace(pos=lambda: QPoint(4, 4)))
    assert seen == [True]
    assert host.to_live2d_change_character_queue.empty()
    browser.contextMenuEvent(SimpleNamespace(pos=lambda: QPoint(4, 4)))
    assert seen == [True, False]
    browser.close()


def test_editor_opens_only_after_matching_reservation(host, app, monkeypatch):
    """请求发出时不先创建窗口，当前舞台确认占用后才绑定原消息进入编排。"""
    from ui_small_theater import multi_char_editor
    factory = Mock()
    monkeypatch.setattr(multi_char_editor, "MultiCharEditor", factory)
    host.open_performance_editor(1)
    request = take_command(host)
    assert request["action"] == "reserve_editor"
    factory.assert_not_called()
    host.receive_playback_state(dict(request, accepted=True, phase="ready", busy=True))
    factory.assert_called_once()
    assert factory.call_args.args[1] is host.current_chat
    assert factory.call_args.args[2] is host.current_chat.message_list[1]
    assert host.performance_editor is factory.return_value


@pytest.mark.parametrize("changed", ["chat", "message", "generation"])
def test_reservation_cannot_open_invalidated_editor(host, app, monkeypatch, changed):
    """预留期间换会话、删除消息或开始生成，回执不能再打开旧编辑器。"""
    from ui_small_theater import multi_char_editor
    factory = Mock()
    monkeypatch.setattr(multi_char_editor, "MultiCharEditor", factory)
    host.open_performance_editor(1)
    request = take_command(host)
    if changed == "chat":
        host.current_chat = Chat(message_list=[Message.from_dict({"text": "另一份"})])
    elif changed == "message":
        host.current_chat.message_list.pop()
    else:
        host._generation_context = {"chat": host.current_chat}
    host.receive_playback_state(dict(request, accepted=True, busy=True))
    factory.assert_not_called()
    release = take_command(host)
    assert release["action"] == "release_editor"
    assert release["reservation_id"] == request["request_id"]


def test_expired_reservation_rejects_late_acceptance(host, app, monkeypatch):
    """舞台迟到确认不能复活已超时的编辑器申请，释放命令绑定原申请。"""
    from ui_small_theater import multi_char_editor
    factory = Mock()
    monkeypatch.setattr(multi_char_editor, "MultiCharEditor", factory)
    host.open_performance_editor(0)
    request = take_command(host)
    host._expire_editor_request(request["request_id"])
    assert host._editor_request is None
    release = take_command(host)
    assert release["action"] == "release_editor"
    assert release["reservation_id"] == request["request_id"]
    host.receive_playback_state(dict(request, accepted=True, busy=True))
    factory.assert_not_called()
    release = take_command(host)
    assert release["reservation_id"] == request["request_id"]


def test_stage_exit_clears_inflight_editor_request(host, app):
    """舞台退出使在途编排申请失效，不留下无法释放的本地忙碌请求。"""
    host.open_performance_editor(0)
    assert host._editor_request and host._playback_pending
    host.on_stage_closed()
    assert host._editor_request is None and not host._playback_pending
    assert not host.playback_available()


def test_real_gui_initializes_formal_load_before_bgm_without_user_storage(app, monkeypatch):
    """真实构造顺序能装载已有剧本并移除旧回放按钮，不读取用户存档或音频。"""
    import multi_char_main
    from PyQt5.QtWidgets import QPushButton
    from chat.chat import ChatType, SmallTheaterPromptGenerator

    chat = Chat(type_=ChatType.SMALL_THEATER, prompt_generator=SmallTheaterPromptGenerator(["A", "B"]),
                message_list=[Message.from_dict({"character_name": "A", "text": "已保存剧本"})])
    manager = SimpleNamespace(chat_list=[chat], storage_notices=[], save=Mock())
    monkeypatch.setattr(multi_char_main, "get_chat_manager", lambda **kwargs: manager)
    for kind in (multi_char_main.DisplayTalkText, multi_char_main.CommunicateThreadMessages,
                 multi_char_main.CommunicateThreadDP2QT):
        monkeypatch.setattr(kind, "start", lambda self: None)
    monkeypatch.setattr(ViewerGUI, "_build_active_slots_payload", lambda self, **kwargs: {
        "type": "set_active_slots", "slots": [{"character_name": name, "model_json_path": None}
                                              for name in ("A", "B")],
        "preserve_playback": True,
    })
    monkeypatch.setattr(ViewerGUI, "_load_bgm", lambda *args: True)
    monkeypatch.setattr(ViewerGUI, "closeEvent", lambda self, event: event.accept())
    chars = [SimpleNamespace(character_name=name, character_folder_name=name,
                             has_valid_voice_model=lambda: False) for name in ("A", "B")]
    commands = Queue()
    gui = ViewerGUI(chars, Queue(), Queue(), Queue(), SimpleNamespace(), Queue(), commands, Queue())
    try:
        command = commands.get_nowait()
        assert command["action"] == "load" and command["messages"][0]["text"] == "已保存剧本"
        assert command["session"] == chat.chat_id
        assert commands.empty()
        assert "回放当前对话" not in [button.text() for button in gui.findChildren(QPushButton)]
        manager.save.assert_not_called()
    finally:
        gui.close()
        gui.deleteLater()
        app.processEvents()


def test_failed_new_load_ack_reaches_qt_and_allows_retry(host):
    """新版本装载失败也应答该版本，Qt 不能因丢弃旧版本回执永久锁住操作。"""
    from live2d_support.multi_char_playback import MultiCharPlayback

    stage = SimpleNamespace(
        state={"visible_characters": ["A", "B"], "background": "default.png"},
        base_background="default.png", cancel=Mock(), show=Mock(), new_playback=Mock(),
        resolve=Mock(return_value={"visible_characters": ["A", "B"], "background": "default.png"}),
    )
    performance = SimpleNamespace(playing=False, paused=False, cursor=None)
    controller = MultiCharPlayback(stage, performance, Mock(), Mock(), Mock(), host.receive_playback_state)
    controller.command({"action": "load", "messages": [], "session": host._playback_session,
                        "revision": host._playback_revision})
    host.load_playback_script()
    command = take_command(host)
    command["messages"] = "损坏的传输内容"
    assert controller.command(command) is False
    assert not host._playback_pending
    assert host._playback_status.get("error")
    assert controller.matches(host._playback_session, host._playback_revision)

    host.load_playback_script()
    assert controller.command(take_command(host)) is True
    assert not host._playback_pending
    assert host._playback_status["phase"] == "ready"
