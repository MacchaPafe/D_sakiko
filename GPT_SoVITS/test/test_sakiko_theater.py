"""小剧场形态切换、独立模型和播放边界。"""

from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt5.QtWidgets import QApplication, QMenu, QWidget

from chat.chat import Chat
from multi_char_main import SettingsDialog, ViewerGUI


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def theater(chat):
    return SimpleNamespace(
        current_chat=chat,
        character_list=[SimpleNamespace(character_name="祥子", character_folder_name="sakiko"),
                        SimpleNamespace(character_name="爱音", character_folder_name="anon")],
        current_char_index=[0, 1], sakiko_state=True,
        generate_btn=SimpleNamespace(isEnabled=lambda: True), pending_turn_uids=set(),
        playback_idle_value=SimpleNamespace(value=True), message_queue=Queue(),
        _save_chat=Mock(return_value=True), set_two_char_names=Mock(),
        audio_gen_module=SimpleNamespace(sakiko_which_state=True),
        sync_live2d_active_slots=Mock(), two_char_names=["祥子", "爱音"],
        _character_name_from_index=lambda index: ("祥子", "爱音")[index],
        _model_paths_support_motion_facing=lambda paths: True,
    )


def test_theater_form_switch_updates_model_voice_and_preserves_other_form():
    chat = Chat(meta={"live2d_form_models": {"sakiko": {"black": "black-v2", "white": "white-v3"}}})
    gui = theater(chat)
    ViewerGUI.convert_sakiko_state(gui)
    assert chat.get_character_form() == "white"
    assert gui.sakiko_state is False
    assert gui.audio_gen_module.sakiko_which_state is False
    assert gui.sync_live2d_active_slots.call_count == 1
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
    monkeypatch.setattr(multi_char_main.character, "GetCharacterAttributes", lambda: SimpleNamespace(character_class_list=[]))
    parent = QWidget()
    for name in ("_change_character", "config_more_info", "convert_sakiko_state", "set_bgm"):
        setattr(parent, name, Mock())
    parent.get_current_l2d_fps = lambda: 60
    parent.motion_facing_mode_button_text = lambda: "对话朝向：正常"
    parent.is_motion_facing_mode_available = lambda: False
    parent.create_mask_menu = lambda: QMenu(parent)
    parent.bgm_player = SimpleNamespace(setVolume=Mock())
    dialog = SettingsDialog(parent, ["祥子", "爱音"], None, SimpleNamespace(speed=1, pause_second=0.5))
    try:
        assert not dialog.btn_character_0_model.isHidden()
        assert not dialog.btn_character_1_model.isHidden()
        assert dialog.btn_sakiko_model.menu() is not None
    finally:
        dialog.close()
        parent.close()
