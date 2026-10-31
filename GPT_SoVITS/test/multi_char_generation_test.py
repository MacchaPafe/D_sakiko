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


@pytest.mark.parametrize("valid_audio,save_ok", [(False, True), (True, False), (True, True)])
def test_regeneration_callback_preserves_old_audio_on_failure(gui, tmp_path, valid_audio, save_ok):
    """重生成失败与写盘失败都释放编辑器锁，成功才替换并裁剪保存的编排。"""
    import soundfile as sf
    message = gui.current_chat.message_list[0]
    original_path = message.audio_path
    message.performance_timeline = {"characters": {"A": {"motions": [
        {"at_ms": 500, "resource": {"file_name": "test"}}], "expressions": []}}}
    original_timeline = copy.deepcopy(message.performance_timeline)
    context = (gui.current_chat, message, message.text, original_path, object())
    gui._audio_regeneration = context
    callback = Mock()
    gui._audio_regeneration_callback = callback
    gui.message_queue = Queue()
    gui.performance_editor = object()
    gui.finish_audio_regeneration = MethodType(ViewerGUI.finish_audio_regeneration, gui)
    gui._save_chat.return_value = save_ok
    path = tmp_path / "new.wav"
    if valid_audio:
        sf.write(str(path), [0.0] * 4000, 8000)
    ViewerGUI.replace_audio_path(gui, [str(path)], context)
    callback.assert_called_once()
    assert callback.call_args.args[0] == (valid_audio and save_ok)
    assert gui._audio_regeneration is None
    if valid_audio and save_ok:
        assert message.audio_path == str(path)
        assert message.performance_timeline["characters"]["A"]["motions"] == []
    else:
        assert message.audio_path == original_path
        assert message.performance_timeline == original_timeline


@pytest.mark.parametrize("route", ["default", "custom", "provider_url", "provider"])
@pytest.mark.parametrize("finish_reason, content", [
    ("length", '[{"text":"半句'),
    ("length", None),
    ("stop", '[{"text":"完整台词"}]'),
])
def test_generation_output_budget_and_truncation(monkeypatch, route, finish_reason, content):
    """各接口分支传入统一预算，截断响应通知失败而不交付残缺剧本。"""
    import litellm
    import dp_local_multi_char as module

    config = SimpleNamespace(reload_from_disk=Mock())
    values = {
        "use_default_deepseek_api": route == "default",
        "enable_custom_llm_api_provider": route == "custom",
        "custom_llm_api_model": "test-model",
        "custom_llm_api_key": "test-key",
        "custom_llm_api_url": "https://example.invalid/v1",
        "llm_api_provider": "openai",
        "llm_api_model": {"openai": "test-model"},
        "llm_api_key": {"openai": "test-key"},
        "llm_api_base_url": {"openai": "https://example.invalid/v1"} if route == "provider_url" else {},
        "llm_temperature": 1,
        "llm_top_p": 1,
    }
    for name, value in values.items():
        setattr(config, name, SimpleNamespace(value=value))
    monkeypatch.setattr(module, "d_sakiko_config", config)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    response = litellm.ModelResponse(choices=[{
        "finish_reason": finish_reason,
        "message": {"role": "assistant", "content": content},
    }])
    completion = Mock(return_value=response)
    monkeypatch.setattr(litellm, "completion", completion)
    worker = module.DSLocalAndVoiceGen.__new__(module.DSLocalAndVoiceGen)
    worker.character_list = [SimpleNamespace(character_name=name, character_description="角色描述") for name in ("A", "B")]
    worker.base_prompt = "剧本生成"
    worker.model = "test-key"
    requests, output, notices = Queue(), Queue(), Queue()
    requests.put({"char_index": [0, 1], "user_input": None})
    requests.put("EXIT")
    worker.text_generator(output, requests, notices)
    assert completion.call_args.kwargs["max_tokens"] == 16384
    assert output.get_nowait() == ("error" if finish_reason == "length" else content)
    assert output.empty()
    messages = []
    while not notices.empty():
        messages.append(notices.get_nowait())
    assert any("内容已被截断" in message for message in messages) == (finish_reason == "length")
