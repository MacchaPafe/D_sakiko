"""生成设定窗口与旧剧本保护的回归测试，不请求 LLM 或写入存档。"""
import copy
import json
import os
import sys
from pathlib import Path
from queue import Queue
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtWidgets import QApplication
from chat.chat import Chat, Message
from multi_char_main import MoreInfoDialog, ViewerGUI


@pytest.fixture(scope="module")
def app():
    """复用离屏 Qt 应用。"""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def gui():
    """构造绑定真实生成方法的轻量主界面，保留一条旧编排消息。"""
    message = Message.from_dict({"character_name": "A", "text": "旧剧本", "audio_path": "old.wav",
                                 "performance_timeline": {"characters": {}}})
    chat = Chat(message_list=[message])
    chat.update_theater_meta({"situation": "旧设定"})
    host = SimpleNamespace(
        current_chat=chat, chat_manager=SimpleNamespace(chat_list=[chat]),
        current_char_index=[0, 1], character_list=[SimpleNamespace(character_name=name) for name in ("A", "B")],
        sakiko_state=False, can_generate_audio_for_current_chat=False,
        _generation_context=None, _generation_serial=0, pending_turn_uids=set(),
        generate_btn=Mock(), messages_box=Mock(), display_timer=Mock(), audio_gen_module=SimpleNamespace(),
        qt2dp_queue=Queue(), _save_chat=Mock(return_value=True),
        _build_active_slots_payload=Mock(return_value={"slots": []}),
        refresh_chat_list=Mock(), refresh_chat_display=Mock(), display_live2d_message=Mock(),
        match_speaker_index=lambda name, indices: {"A": 0, "B": 1}.get(name),
        _message_from_turn_dict=Message.from_dict,
    )
    for name in ("submit_generation", "generation_failed", "handle_response", "final_handle_response",
                 "_build_playlist_queue", "_next_turn_uid", "_safe_get_audio_path", "robust_json_parse"):
        setattr(host, name, MethodType(getattr(ViewerGUI, name), host))
    return host


def submit(gui):
    """模拟设定窗口提交，返回可检查关闭状态的窗口替身。"""
    dialog = SimpleNamespace(commit_btn=Mock(), accept=Mock())
    data = copy.deepcopy(gui.current_chat.meta.theater.to_dict())
    data["situation"] = "新设定"
    gui.submit_generation(dialog, gui.current_chat, [0, 1], data)
    return dialog


def test_cancel_discards_only_dialog_draft(app, gui):
    """取消不提交设定、不发请求、不动已展示的剧本音频与编排。"""
    initial = gui.current_chat.meta.theater.to_dict()
    original = gui.current_chat.message_list[0]
    dialog = MoreInfoDialog("A", "B", initial, replacing=True)
    callback = Mock()
    dialog.info_committed.connect(callback)
    dialog.situation_input.setText("取消的修改")
    dialog.reject()
    callback.assert_not_called()
    assert initial["situation"] == "旧设定"
    assert gui.current_chat.meta.theater.situation == "旧设定"
    assert gui.current_chat.message_list[0] is original
    assert gui.qt2dp_queue.empty()
    dialog.deleteLater()


def test_dialog_submit_is_single_shot(app):
    """连续点击开始生成只发一次提交信号。"""
    dialog = MoreInfoDialog("A", "B", {})
    callback = Mock()
    dialog.info_committed.connect(callback)
    dialog.commit_info()
    dialog.commit_info()
    callback.assert_called_once()
    dialog.deleteLater()


def test_main_generate_only_opens_dialog(gui, monkeypatch):
    """主页按钮只能打开带覆盖提示的设定窗口，不提前保存或发送请求。"""
    import multi_char_main
    dialog = Mock()
    factory = Mock(return_value=dialog)
    monkeypatch.setattr(multi_char_main, "MoreInfoDialog", factory)
    original = gui.current_chat.message_list
    ViewerGUI.start_generation(gui)
    dialog.exec_.assert_called_once()
    assert factory.call_args.kwargs["replacing"]
    gui._save_chat.assert_not_called()
    assert gui.current_chat.message_list is original
    assert gui.qt2dp_queue.empty()


def test_submit_preserves_old_script_and_save_failure(gui):
    """设置保存失败时不请求，成功提交后旧剧本仍保留到结果可用。"""
    original = gui.current_chat.message_list
    gui._save_chat.return_value = False
    dialog = submit(gui)
    assert gui.qt2dp_queue.empty()
    assert gui.current_chat.meta.theater.situation == "旧设定"
    dialog.accept.assert_not_called()
    gui._save_chat.return_value = True
    submit(gui)
    assert gui.qt2dp_queue.qsize() == 1
    assert gui.current_chat.message_list is original
    assert gui.current_chat.meta.theater.situation == "新设定"
    submit(gui)
    assert gui.qt2dp_queue.qsize() == 1


@pytest.mark.parametrize("response", ["error", "not json", "[]", "{}", '[{"speaker":"C","text":"错误角色"}]'])
def test_failed_generation_preserves_script(gui, response):
    """LLM 错误、非法结构、空结果和未知角色均保留旧剧本。"""
    original = gui.current_chat.message_list
    submit(gui)
    gui.handle_response(response)
    assert gui.current_chat.message_list is original
    assert gui._generation_context is None
    gui.display_live2d_message.assert_not_called()


@pytest.mark.parametrize("switched,save_ok", [(False, True), (True, True), (False, False)])
def test_result_replaces_only_bound_chat(gui, switched, save_ok):
    """新结果一次写回原目标；切换对话不串写，保存失败回滚旧剧本。"""
    target = gui.current_chat
    original = target.message_list
    submit(gui)
    if switched:
        gui.current_chat = Chat(message_list=[Message.from_dict({"text": "另一段剧本"})])
        gui.chat_manager.chat_list.append(gui.current_chat)
        gui.current_char_index = [1, 0]
    gui._save_chat.return_value = save_ok
    gui.handle_response(json.dumps([{"speaker": "A", "text": "新剧本", "emotion": "normal"}]))
    if save_ok:
        assert target.message_list[0].text == "新剧本"
        assert target.message_list[0].character_name == "A"
        assert target.message_list[0].performance_timeline is None
    else:
        assert target.message_list is original
    if switched:
        assert gui.current_chat.message_list[0].text == "另一段剧本"
    assert gui.display_live2d_message.called == (save_ok and not switched)
    assert not gui.pending_turn_uids
