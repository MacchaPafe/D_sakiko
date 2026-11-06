"""批量补齐复用真实合成线程与保存回填，不连接语音服务或用户存档。"""
import os
import sys
import time
import threading
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import soundfile as sf
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QMessageBox
from multi_char_main import ViewerGUI
from chat.chat import Chat, Message


class FillHost(QWidget):
    """只装配本次流程，实际使用生产方法和 QThread 信号。"""
    for method in ("fill_missing_audio", "_advance_audio_fill", "_record_audio_fill",
                   "_audio_fill_thread_finished", "_finish_audio_fill", "regenerate_audio",
                   "regenerate_audio_continue", "finish_audio_regeneration", "replace_audio_path",
                   "set_playback_block"):
        locals()[method] = getattr(ViewerGUI, method)


def wait_for(condition):
    end = time.monotonic() + 4
    while not condition() and time.monotonic() < end:
        QTest.qWait(10)
    assert condition()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(app, tmp_path, monkeypatch):
    value = FillHost()
    value._audio_fill = None
    value._audio_regeneration = None
    value._playback_blocked_reasons = set()
    value.generate_thread = None
    value.performance_editor = None
    value.settings_dialog = None
    value.sakiko_state = False
    value.current_chat = Chat(message_list=[])
    value.message_queue = Queue()
    value.send_playback_command = Mock()
    value.playback_available = Mock(side_effect=lambda: not value._playback_blocked_reasons)
    value._save_chat = Mock(return_value=True)
    value.refresh_chat_display = Mock()
    value.load_playback_script = Mock()
    value.messages_box = Mock()
    value.display_timer = Mock()
    for name in ("fill_audio_btn", "generate_btn", "edit_script_btn", "chat_manage_panel",
                 "chat_display", "btn_settings"):
        setattr(value, name, QPushButton(value))
    value._find_character_by_name = lambda name: (
        SimpleNamespace(character_name=name, character_folder_name=name.lower(),
                        has_valid_voice_model=lambda: True) if name in ("A", "B") else None)
    value.attempts = []
    value.options_seen = []
    value.fail_texts = set()
    value.gate = threading.Event()
    value.gate.set()

    def synthesize(text, *args, **kwargs):
        value.attempts.append(text)
        value.options_seen.append(dict(kwargs.get("generation_options") or {}))
        value.gate.wait(3)
        if text in value.fail_texts:
            raise RuntimeError("测试合成失败")
        path = tmp_path / (str(len(value.attempts)) + ".wav")
        sf.write(str(path), [0.0] * 800, 8000)
        return str(path)

    value.audio_gen_module = SimpleNamespace(generate_audio_for_character_sync=synthesize)
    value.confirmations = []

    def confirm(box):
        value.confirmations.append(box.text())
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "exec_", confirm)
    monkeypatch.setattr(QMessageBox, "information", Mock())
    yield value
    value.gate.set()
    if value._audio_fill is not None:
        wait_for(lambda: value._audio_fill is None)
    if value.generate_thread is not None:
        value.generate_thread.wait(1000)
    value.deleteLater()


def line(text, **kwargs):
    return Message.from_dict({"character_name": "A", "text": text, **kwargs})


def test_scan_preserves_valid_and_skips_ineligible(host, tmp_path):
    valid = tmp_path / "valid.wav"
    sf.write(str(valid), [0.0] * 800, 8000)
    corrupt = tmp_path / "broken.wav"
    corrupt.write_text("broken")
    host.current_chat.message_list = [
        line("保留", audio_path=str(valid)), line("缺失", audio_path=str(tmp_path / "lost.wav")),
        line("损坏", audio_path=str(corrupt)), line("无声", character_name=""),
        line("静音", muted=True), line("无模型", character_name="C"), line("新句")]
    original = host.current_chat.message_list[0].as_dict()
    host.fill_missing_audio()
    wait_for(lambda: host._audio_fill is None)
    assert host.attempts == ["缺失", "损坏", "新句"]
    assert host.current_chat.message_list[0].as_dict() == original
    assert "待补齐 3 句，已有音频 1 句，跳过 3 句" in host.confirmations[0]
    assert "成功 3 句，失败 0 句，跳过 3 句" in QMessageBox.information.call_args.args[2]
    assert host._save_chat.call_count == 3
    assert not any(call.args[0] == "single" for call in host.send_playback_command.call_args_list)
    assert not host._playback_blocked_reasons
    host.load_playback_script.assert_called_once_with(preserve_scene=True)


def test_failure_continues_and_retry_only_fills_remaining(host):
    host.current_chat.message_list = [line("成功一"), line("失败"), line("成功二")]
    host.fail_texts.add("失败")
    host.fill_missing_audio()
    wait_for(lambda: host._audio_fill is None)
    assert "成功 2 句，失败 1 句" in QMessageBox.information.call_args.args[2]
    assert host._save_chat.call_count == 2
    host.attempts.clear()
    host.fail_texts.clear()
    host.fill_missing_audio()
    wait_for(lambda: host._audio_fill is None)
    assert host.attempts == ["失败"]


def test_save_failure_stops_and_rolls_back_even_when_finished_reenters(host):
    first = line("保存失败", audio_path="missing.wav", performance_timeline={"characters": {"A": {
        "motions": [{"at_ms": 500, "resource": {"logical_id": "x"}}], "expressions": []}}})
    original = first.as_dict()
    host.current_chat.message_list = [first, line("不应开始")]

    def fail_save():
        # 模拟保存失败提示框的嵌套事件循环先处理 QThread.finished。
        host.generate_thread.wait(1000)
        QApplication.processEvents()
        assert host.attempts == ["保存失败"]
        return False

    host._save_chat.side_effect = fail_save
    host.fill_missing_audio()
    wait_for(lambda: host._audio_fill is None)
    assert first.as_dict() == original
    assert host.attempts == ["保存失败"]
    assert QMessageBox.information.call_args.args[1] == "音频补齐已停止"
    assert "成功 0 句，失败 1 句" in QMessageBox.information.call_args.args[2]
    assert "剩余 1 句未处理" in QMessageBox.information.call_args.args[2]
    assert not host._playback_blocked_reasons


def test_existing_trim_and_scene_rules_and_single_autoplay_unchanged(host):
    message = line("裁剪", scene_before={"visible_characters": ["A"]}, performance_timeline={
        "characters": {"A": {"motions": [{"at_ms": 500, "resource": {"logical_id": "x"}}],
                              "expressions": []}}})
    host.current_chat.message_list = [message]
    host.fill_missing_audio()
    wait_for(lambda: host._audio_fill is None)
    assert message.performance_timeline["characters"]["A"]["motions"] == []
    assert message.scene_before == {"visible_characters": ["A"]}
    host.send_playback_command.reset_mock()
    assert host.regenerate_audio(0)
    wait_for(lambda: host._audio_regeneration is None)
    host.generate_thread.wait(1000)
    assert any(call.args[0] == "single" for call in host.send_playback_command.call_args_list)


def test_cancel_and_no_candidates_do_not_start(host, monkeypatch):
    host.current_chat.message_list = [line("取消")]
    monkeypatch.setattr(QMessageBox, "exec_", lambda box: QMessageBox.No)
    host.fill_missing_audio()
    assert host._audio_fill is None
    assert not host.attempts
    host.current_chat.message_list = [line("无角色", character_name="")]
    host.fill_missing_audio()
    assert "没有可补齐的音频" in QMessageBox.information.call_args.args[2]
    host.send_playback_command.assert_not_called()


def test_changed_chat_while_confirming_is_rejected(host, monkeypatch):
    host.current_chat.message_list = [line("原文")]

    def confirm(box):
        host.current_chat.message_list[0].text = "已修改"
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "exec_", confirm)
    host.fill_missing_audio()
    assert host._audio_fill is None
    assert not host.attempts


def test_whole_batch_lock_blocks_entry_points_and_restores_controls(host):
    host.current_chat.message_list = [line("等待"), line("下一句")]
    host.gate.clear()
    host.btn_settings.setEnabled(False)
    host.fill_missing_audio()
    task = host._audio_fill
    assert task is not None
    assert not host.chat_manage_panel.isEnabled()
    assert not host.generate_btn.isEnabled()
    assert not host.chat_display.isEnabled()
    assert not host.playback_available()
    assert not host.regenerate_audio(1)
    original_chat = host.current_chat
    ViewerGUI.switch_chat(host, Chat())
    ViewerGUI.create_new_chat(host)
    ViewerGUI.delete_current_chat(host)
    ViewerGUI.start_generation(host)
    ViewerGUI.open_settings_dialog(host)
    ViewerGUI.open_editor(host, 0)
    event = Mock()
    ViewerGUI.closeEvent(host, event)
    event.ignore.assert_called_once()
    assert host.current_chat is original_chat
    host.gate.set()
    wait_for(lambda: host._audio_fill is None)
    assert host.chat_manage_panel.isEnabled()
    assert host.generate_btn.isEnabled()
    assert host.chat_display.isEnabled()
    assert not host.btn_settings.isEnabled()


def test_batch_and_next_single_use_latest_options_for_actual_speaker(host):
    """批量与单句均按角色 ID 取值；修改后下一次合成使用新值。"""
    host._theater_voice_options = {
        "a": {"speed_factor": 0.8, "fragment_interval": 0.2},
        "b": {"speed_factor": 1.1, "fragment_interval": 0.6},
    }
    host.current_chat.message_list = [line("A台词"), line("B台词", character_name="B")]
    host.fill_missing_audio()
    wait_for(lambda: host._audio_fill is None)
    assert host.options_seen == [host._theater_voice_options["a"], host._theater_voice_options["b"]]
    host._theater_voice_options["a"].update(speed_factor=1.2, fragment_interval=0.4)
    assert host.regenerate_audio(0)
    wait_for(lambda: host._audio_regeneration is None)
    host.generate_thread.wait(1000)
    assert host.options_seen[-1] == {"speed_factor": 1.2, "fragment_interval": 0.4}
    assert host.options_seen[0] == {"speed_factor": 0.8, "fragment_interval": 0.2}
