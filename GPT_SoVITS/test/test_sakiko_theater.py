"""小剧场形态切换、独立模型和播放边界。"""

from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication, QGroupBox, QMenu, QWidget

from chat.chat import Chat
from multi_char_main import SettingsDialog, ViewerGUI


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def theater(chat):
    gui = SimpleNamespace(
        current_chat=chat,
        character_list=[SimpleNamespace(character_name="祥子", character_folder_name="sakiko"),
                        SimpleNamespace(character_name="爱音", character_folder_name="anon")],
        current_char_index=[0, 1], sakiko_state=True,
        generate_btn=SimpleNamespace(isEnabled=lambda: True), pending_turn_uids=set(),
        playback_idle_value=SimpleNamespace(value=True), message_queue=Queue(),
        _save_chat=Mock(return_value=True), set_two_char_names=Mock(),
        audio_gen_module=SimpleNamespace(sakiko_which_state=True),
        sync_live2d_active_slots=Mock(), load_playback_script=Mock(), two_char_names=["祥子", "爱音"],
        _character_name_from_index=lambda index: ("祥子", "爱音")[index],
        _model_paths_support_motion_facing=lambda paths: True,
    )
    gui.playback_available = lambda: gui.playback_idle_value.value
    return gui


def test_theater_form_switch_updates_model_voice_and_preserves_other_form():
    chat = Chat(meta={"live2d_form_models": {"sakiko": {"black": "black-v2", "white": "white-v3"}}})
    gui = theater(chat)
    ViewerGUI.convert_sakiko_state(gui)
    assert chat.get_character_form() == "white"
    assert gui.sakiko_state is False
    assert gui.audio_gen_module.sakiko_which_state is False
    gui.load_playback_script.assert_called_once_with()
    payload = ViewerGUI._build_active_slots_payload(gui)
    assert payload["slots"][0]["model_json_path"] == "white-v3"
    assert payload["slots"][0]["sakiko_state"] is False
    assert chat.meta.live2d_form_models["sakiko"]["black"] == "black-v2"


def test_theater_cannot_change_form_during_last_audio_or_failed_save():
    chat = Chat(meta={})
    gui = theater(chat)
    gui.playback_idle_value.value = False
    ViewerGUI.convert_sakiko_state(gui)
    gui._save_chat.assert_not_called()
    assert chat.get_character_form() == "black"
    gui.playback_idle_value.value = True
    gui._save_chat.return_value = False
    ViewerGUI.convert_sakiko_state(gui)
    assert chat.meta.character_forms == {}
    gui.sync_live2d_active_slots.assert_not_called()


def test_theater_settings_exposes_sakiko_model_configuration(app, monkeypatch):
    import multi_char_main
    monkeypatch.setattr(multi_char_main.character, "GetCharacterAttributes", lambda: SimpleNamespace(
        character_class_list=theater(Chat()).character_list))
    save = Mock()
    config = SimpleNamespace(multi_char_subtitle_mode=SimpleNamespace(value="translation"), set=save)
    monkeypatch.setattr(multi_char_main, "d_sakiko_config", config)
    parent = QWidget()
    parent.to_live2d_change_character_queue = Queue()
    for name in ("convert_sakiko_state", "set_bgm"):
        setattr(parent, name, Mock())
    parent.get_current_l2d_fps = lambda: 60
    parent.motion_facing_mode_button_text = lambda: "对话朝向：正常"
    parent.is_motion_facing_mode_available = lambda: False
    parent.create_mask_menu = lambda: QMenu(parent)
    parent._bgm_volume = 50
    parent.set_bgm_volume = Mock()
    dialog = SettingsDialog(parent, ["祥子", "爱音"], None, SimpleNamespace(speed=1, pause_second=0.5))
    try:
        assert list(dialog.model_buttons) == ["sakiko", "anon"]
        assert dialog.btn_sakiko_model.menu() is not None
        assert "角色管理" not in [group.title() for group in dialog.findChildren(QGroupBox)]
        assert dialog.subtitle_mode_group.checkedButton() is dialog.subtitle_buttons["translation"]
        assert [button.text() for button in dialog.subtitle_buttons.values()] == ["仅原文", "仅翻译", "原文与翻译"]
        dialog.show()
        app.processEvents()
        assert all(button.palette().color(QPalette.ButtonText).name() == multi_char_main.THEME_COLOR.lower()
                   for button in dialog.subtitle_buttons.values())
        highlight = dialog.subtitle_modes.highlight.grab().toImage()
        assert highlight.pixelColor(highlight.rect().center()).name() == "#ffffff"
        dialog.subtitle_buttons["original"].click()
        save.assert_called_once_with(config.multi_char_subtitle_mode, "original")
        assert parent.to_live2d_change_character_queue.get_nowait() == {
            "type": "set_subtitle_mode", "mode": "original"}
    finally:
        dialog.close()
        parent.close()
