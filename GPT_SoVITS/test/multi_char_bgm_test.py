"""场景音乐继承、真实 Qt 音量动画和消息入口；媒体设备由替身隔离。"""
import os
import sys
import wave
from pathlib import Path
from queue import Queue
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtCore import QObject, QVariantAnimation, pyqtSignal
from PyQt5.QtMultimedia import QMediaPlayer
from PyQt5.QtWidgets import QApplication, QPushButton, QDialog

import multi_char_main as main
from live2d_support.multi_char_scene import resolve_bgm


class Media(QObject):
    stateChanged = pyqtSignal(int)
    mediaStatusChanged = pyqtSignal(int)
    error = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.state_value = QMediaPlayer.PlayingState
        self.status = QMediaPlayer.LoadedMedia
        self.ready = True
        self.position = 40000
        self.loudness = 30
        self.stops = 0
        self.pauses = 0
        self.loads = []

    def state(self):
        return self.state_value

    def mediaStatus(self):
        return self.status

    def setVolume(self, value):
        self.loudness = value

    def errorString(self):
        return "无法解码"

    def stop(self):
        self.stops += 1
        self.position = 0
        self.state_value = QMediaPlayer.StoppedState
        self.stateChanged.emit(self.state_value)

    def pause(self):
        self.pauses += 1
        self.state_value = QMediaPlayer.PausedState
        self.stateChanged.emit(self.state_value)

    def play(self):
        self.state_value = QMediaPlayer.PlayingState
        self.stateChanged.emit(self.state_value)


class Playlist:
    def __init__(self, media):
        self.media = media
        self.items = []

    def clear(self):
        self.items.clear()
        self.media.status = QMediaPlayer.NoMedia
        self.media.mediaStatusChanged.emit(self.media.status)

    def addMedia(self, content):
        self.items.append(content.canonicalUrl().toLocalFile())
        return True

    def setCurrentIndex(self, index):
        self.media.loads.append(self.items[index])
        self.media.status = QMediaPlayer.LoadedMedia if self.media.ready else QMediaPlayer.LoadingMedia
        self.media.mediaStatusChanged.emit(self.media.status)


class Host(SimpleNamespace):
    """为 Qt 信号提供可弱引用的主窗口方法宿主。"""


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(app, monkeypatch, tmp_path):
    for name in ("a.wav", "b.wav", "c.wav"):
        (tmp_path / name).write_bytes(b"isolated media")
    monkeypatch.setattr(main, "BGM_DIRECTORY", tmp_path)
    media = Media()
    value = Host(
        bgm_player=media, bgm_playlist=Playlist(media), bgm_fade=QVariantAnimation(),
        _bgm_volume=30, _bgm_factor=1.0, _bgm_phase="", _bgm_target=None,
        _bgm_scene_controlled=False, _current_bgm_path=str(tmp_path / "a.wav"),
        _playback_status={"phase": "ready", "auto": False}, _playback_pending=set(),
        _playback_stage_available=True, _playback_session="theater", _playback_revision=1,
        performance_editor=None, settings_dialog=SimpleNamespace(btn_set_bgm=QPushButton()),
        play_bgm_btn=QPushButton(), message_queue=Queue(), _editor_request=None,
        _recover_bgm_after_failure=Mock(),
    )
    for name in (
        "apply_scene_bgm", "_fade_bgm_to", "_finish_bgm_fade", "_switch_scene_bgm",
        "_try_bgm_fade_in", "_set_bgm_factor", "_fail_scene_bgm", "_load_bgm",
        "_handle_bgm_error", "_handle_bgm_media_status", "bgm_controls_available",
        "update_bgm_controls", "set_bgm_volume", "toggle_bgm", "set_bgm",
        "display_this_turn_text", "on_stage_closed",
    ):
        setattr(value, name, MethodType(getattr(main.ViewerGUI, name), value))
    value.bgm_fade.valueChanged.connect(value._set_bgm_factor)
    value.bgm_fade.finished.connect(value._finish_bgm_fade)
    media.stateChanged.connect(value._try_bgm_fade_in)
    media.mediaStatusChanged.connect(value._handle_bgm_media_status)
    media.error.connect(value._handle_bgm_error)
    yield value
    value.bgm_fade.stop()
    value.play_bgm_btn.deleteLater()
    value.settings_dialog.btn_set_bgm.deleteLater()


def play(file="b.wav", fade_out=500, fade_in=500):
    return {"action": "play", "file": file, "fade_out_ms": fade_out, "fade_in_ms": fade_in}


def finish_fade(host):
    host.bgm_fade.setCurrentTime(host.bgm_fade.duration())


def test_resolve_inherited_music_and_stop_without_replaying_history():
    first, second = play("a.wav"), play("b.wav")
    messages = [{"scene_before": {"bgm": first}}, {}, {"scene_before": {"bgm": second}}, {},
                {"scene_before": {"bgm": {"action": "stop", "fade_out_ms": 900}}}, {}]
    assert resolve_bgm(messages, -1) is None
    assert resolve_bgm(messages, 1) == first
    assert resolve_bgm(messages, 3) == second
    assert resolve_bgm(messages, 5)["action"] == "stop"


def test_entry_same_music_keeps_position_and_does_not_fade(host):
    host.apply_scene_bgm(play("a.wav"), entry=True)
    assert host.bgm_player.position == 40000
    assert host.bgm_player.stops == 0 and host.bgm_player.loads == []
    assert host._bgm_phase == "" and host.bgm_player.loudness == 30


def test_continuous_same_music_executes_out_reload_and_in(host):
    host.apply_scene_bgm(play("a.wav"))
    assert host._bgm_phase == "out" and host.bgm_player.loads == []
    host.bgm_fade.setCurrentTime(250)
    assert host.bgm_player.loudness == 15
    finish_fade(host)
    assert host._bgm_phase == "in" and host.bgm_player.position == 0
    assert Path(host.bgm_player.loads[-1]).name == "a.wav"
    assert host.bgm_player.loudness == 0
    finish_fade(host)
    assert host._bgm_phase == "" and host.bgm_player.loudness == 30


def test_entry_uses_fixed_fast_times_without_mutating_saved_settings(host):
    config = play(fade_out=3000, fade_in=3000)
    host.apply_scene_bgm(config, entry=True)
    assert host.bgm_fade.duration() == 200
    finish_fade(host)
    assert host._bgm_phase == "in" and host.bgm_fade.duration() == 200
    finish_fade(host)
    assert config["fade_out_ms"] == config["fade_in_ms"] == 3000


@pytest.mark.parametrize("state", [QMediaPlayer.StoppedState, QMediaPlayer.PausedState])
def test_entry_without_playing_music_skips_out_and_starts_in(host, state):
    host.bgm_player.state_value = state
    host.apply_scene_bgm(play("a.wav"), entry=True)
    assert host._bgm_phase == "in" and host.bgm_fade.duration() == 200
    assert host.bgm_player.position == 0 and host.bgm_player.loudness == 0


def test_stop_reaches_silence_and_keeps_global_volume_for_manual_play(host):
    host.apply_scene_bgm({"action": "stop", "fade_out_ms": 500})
    host.bgm_fade.setCurrentTime(250)
    assert host.bgm_player.loudness == 15
    finish_fade(host)
    assert host.bgm_player.state() == QMediaPlayer.StoppedState
    assert host._bgm_phase == "" and host._bgm_volume == 30
    host.toggle_bgm()
    assert host.bgm_player.state() == QMediaPlayer.PlayingState and host.bgm_player.loudness == 30


@pytest.mark.parametrize("action", ["play", "stop"])
def test_zero_duration_completes_without_stuck_controls(host, action):
    config = play(fade_out=0, fade_in=0) if action == "play" else {"action": "stop", "fade_out_ms": 0}
    host.apply_scene_bgm(config)
    assert host._bgm_phase == "" and host.play_bgm_btn.isEnabled()
    assert host.bgm_player.loudness == host._bgm_volume


def test_new_music_replaces_pending_switch_from_current_volume(host):
    host.apply_scene_bgm(play())
    host.bgm_fade.setCurrentTime(250)
    host.apply_scene_bgm(play("c.wav"))
    assert host.bgm_player.loudness == 15 and host.bgm_player.loads == []
    finish_fade(host)
    assert [Path(path).name for path in host.bgm_player.loads] == ["c.wav"]
    assert host.bgm_player.loudness == 0
    finish_fade(host)
    assert host.bgm_player.loudness == 30


def test_new_stop_interrupts_fade_in_from_current_volume(host):
    host.apply_scene_bgm(play(fade_out=0, fade_in=1000))
    host.bgm_fade.setCurrentTime(500)
    host.apply_scene_bgm({"action": "stop", "fade_out_ms": 1000})
    assert host.bgm_player.loudness == 15
    host.bgm_fade.setCurrentTime(500)
    assert host.bgm_player.loudness in (7, 8)
    finish_fade(host)
    assert host.bgm_player.state() == QMediaPlayer.StoppedState


def test_entry_same_music_cancels_old_pending_switch_without_restarting(host):
    host.apply_scene_bgm(play())
    host.bgm_fade.setCurrentTime(250)
    host.apply_scene_bgm(play("a.wav"), entry=True)
    assert host.bgm_player.position == 40000 and host.bgm_player.loads == []
    assert host.bgm_player.loudness == 15
    finish_fade(host)
    assert host._bgm_phase == "" and host.bgm_player.loads == [] and host.bgm_player.loudness == 30


def test_entry_same_music_does_not_restart_existing_fade_in(host):
    host.apply_scene_bgm(play(fade_out=0, fade_in=1000))
    host.bgm_fade.setCurrentTime(300)
    loads = host.bgm_player.loads[:]
    host.apply_scene_bgm(play(fade_out=3000, fade_in=3000), entry=True)
    assert host.bgm_fade.duration() == 1000 and host.bgm_fade.currentTime() == 300
    assert host.bgm_player.loudness == 9 and host.bgm_player.loads == loads


def test_volume_change_uses_latest_global_value_during_fade(host):
    host.apply_scene_bgm(play(fade_out=0, fade_in=1000))
    host.bgm_fade.setCurrentTime(500)
    host.set_bgm_volume(80)
    assert host._bgm_volume == 80 and host.bgm_player.loudness == 40
    finish_fade(host)
    assert host.bgm_player.loudness == 80


def test_async_media_loading_keeps_silent_until_ready_then_fades_in(host):
    host.bgm_player.ready = False
    host.apply_scene_bgm(play(fade_out=0))
    assert host._bgm_phase == "loading" and host.bgm_player.loudness == 0
    host.bgm_player.status = QMediaPlayer.LoadedMedia
    host.bgm_player.mediaStatusChanged.emit(QMediaPlayer.LoadedMedia)
    assert host._bgm_phase == "in" and host.bgm_player.loudness == 0
    finish_fade(host)
    assert host.bgm_player.loudness == 30


@pytest.mark.parametrize("phase,auto", [("playing", False), ("transition", False), ("ready", True)])
def test_playback_locks_both_manual_music_entries_but_volume_stays_available(host, phase, auto):
    host._playback_status = {"phase": phase, "auto": auto}
    host.update_bgm_controls()
    assert not host.play_bgm_btn.isEnabled() and not host.settings_dialog.btn_set_bgm.isEnabled()
    host.toggle_bgm()
    host.set_bgm()
    assert host.bgm_player.pauses == 0
    host.set_bgm_volume(60)
    assert host.bgm_player.loudness == 60


def test_music_fade_keeps_buttons_locked_after_sentence_ends_and_stage_closes(host):
    host.apply_scene_bgm(play())
    assert not host.play_bgm_btn.isEnabled() and not host.settings_dialog.btn_set_bgm.isEnabled()
    host._playback_status = {"phase": "playing", "auto": True}
    host.on_stage_closed()
    assert not host.play_bgm_btn.isEnabled()
    finish_fade(host)
    finish_fade(host)
    assert host.play_bgm_btn.isEnabled() and host.settings_dialog.btn_set_bgm.isEnabled()


def test_missing_scene_music_stops_reports_and_never_falls_back(host):
    host.apply_scene_bgm(play("missing.wav", fade_out=0))
    assert host._bgm_phase == "" and host.bgm_player.state() == QMediaPlayer.StoppedState
    assert "missing.wav" in host.message_queue.get_nowait()
    host._recover_bgm_after_failure.assert_not_called()


def test_scene_decode_failure_reports_once_and_unlocks_controls(host):
    host.apply_scene_bgm(play(fade_out=0))
    host.bgm_player.status = QMediaPlayer.InvalidMedia
    host.bgm_player.mediaStatusChanged.emit(QMediaPlayer.InvalidMedia)
    host.bgm_player.error.emit(QMediaPlayer.FormatError)
    assert host._bgm_phase == "" and host.bgm_player.state() == QMediaPlayer.StoppedState
    assert host.play_bgm_btn.isEnabled() and host.message_queue.qsize() == 1
    host._recover_bgm_after_failure.assert_not_called()


def test_bgm_receipts_require_current_script_and_current_preview_session(host):
    host.apply_scene_bgm = Mock()
    payload = {"type": "multi_char_bgm", "session": "theater", "revision": 1,
               "config": play(), "entry": True}
    host.display_this_turn_text(dict(payload, revision=0))
    host.display_this_turn_text(dict(payload, session="old"))
    host.display_this_turn_text(dict(payload, preview_session="closed-preview"))
    host.apply_scene_bgm.assert_not_called()
    host.performance_editor = SimpleNamespace(session_id="preview")
    host.display_this_turn_text(dict(payload, preview_session="preview"))
    host.apply_scene_bgm.assert_called_once_with(payload["config"], True)


@pytest.mark.parametrize("selection_only", [True, False])
def test_music_picker_keeps_scene_selection_separate_from_global_setting(app, monkeypatch, tmp_path, selection_only):
    import ui_qt.components.custom_bgm_dialog as module
    monkeypatch.setattr(module, "BGM_DIRECTORY", tmp_path)
    config_set = Mock()
    monkeypatch.setattr(module.d_sakiko_config, "set", config_set)
    path = tmp_path / "select.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0" * 3200)
    dialog = module.CustomBGMDialog(current_path=str(path), selection_only=selection_only)
    try:
        dialog.show()
        dialog.replace_bgm(path)
        assert dialog.selected_path == str(path)
        if selection_only:
            assert dialog.result() == QDialog.Accepted and not dialog.isVisible()
            config_set.assert_not_called()
        else:
            assert dialog.isVisible()
            config_set.assert_called_once_with(module.d_sakiko_config.multi_char_background_music_path, str(path))
    finally:
        dialog.close()
        dialog.deleteLater()


def test_cancel_manual_picker_keeps_current_scene_music_not_default(host, monkeypatch):
    dialog = SimpleNamespace(exec_=lambda: QDialog.Rejected, selected_path=None)
    create = Mock(return_value=dialog)
    monkeypatch.setattr(main, "CustomBGMDialog", create)
    config_set = Mock()
    monkeypatch.setattr(main.d_sakiko_config, "set", config_set)
    original = host._current_bgm_path
    host.set_bgm()
    assert create.call_args.kwargs["current_path"] == original
    assert host.bgm_player.position == 40000 and host.bgm_player.loads == []
    assert host._current_bgm_path == original and host.bgm_player.state() == QMediaPlayer.PlayingState
    config_set.assert_not_called()


def test_manual_picker_applies_selected_music_after_dialog_closes(host, monkeypatch):
    selected = str(Path(host._current_bgm_path).with_name("b.wav"))
    dialog = SimpleNamespace(exec_=lambda: QDialog.Accepted, selected_path=selected)
    monkeypatch.setattr(main, "CustomBGMDialog", Mock(return_value=dialog))
    config_set = Mock()
    monkeypatch.setattr(main.d_sakiko_config, "set", config_set)
    host.set_bgm()
    assert host._current_bgm_path == selected and host.bgm_player.state() == QMediaPlayer.PlayingState
    config_set.assert_not_called()
