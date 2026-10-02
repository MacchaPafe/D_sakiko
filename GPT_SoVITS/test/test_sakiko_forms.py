"""双形态存档、工具与模型级摘戴动作的行为回归。"""

import json
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from chat.chat import Chat
from chat.chat_meta import ChatMeta
from chat.tool_calling import ToolRegistry, ToolCallRequest, register_live2d_tools
from live2d_support.character_forms import explicit_model
from live2d_support.mask_actions import mask_actions, play_mask_action, save_mask_actions
from live2d_support.model_catalog import Live2DModelCatalog
from live2d_support.performance_catalog import projected_model_document, performance_config_path, shared_config_path


def write_model(root, version="v3"):
    root.mkdir(parents=True, exist_ok=True)
    suffix = ".motion3.json" if version == "v3" else ".mtn"
    for name in ("put_on", "take_off", "idle"):
        (root / (name + suffix)).write_text('{}')
    (root / "model.moc3").write_bytes(b'model')
    path = root / ("model.model3.json" if version == "v3" else "model.model.json")
    data = {"Version": 3, "FileReferences": {"Moc": "model.moc3", "Textures": [],
             "Motions": {"IDLE": [{"File": "idle.motion3.json"}]}}} if version == "v3" else {
             "model": "model.moc3", "textures": [], "motions": {"IDLE": [{"file": "idle.mtn"}]}}
    path.write_text(json.dumps(data))
    return path


def test_legacy_white_override_migrates_without_overwriting_new_selection():
    meta = ChatMeta.from_dict({"live2d_models": {"祥子": "old", "爱音": "other"},
                              "live2d_form_models": {"sakiko": {"white": "new"}},
                              "future_data": {"keep": True}})
    assert meta.live2d_models == {"爱音": "other"}
    assert meta.live2d_form_models["sakiko"] == {"white": "new"}
    assert ChatMeta.from_dict(meta.to_dict()).to_dict() == meta.to_dict()
    assert meta.extra["future_data"] == {"keep": True}
    assert ChatMeta.from_dict({"live2d_models": {"祥子": "old"}}).live2d_form_models == {"sakiko": {"white": "old"}}


def test_form_models_and_reset_are_independent(tmp_path, monkeypatch):
    import live2d_support.character_forms as forms
    monkeypatch.setattr(forms, "default_form_model", lambda form: "default-" + form)
    black = write_model(tmp_path / "black")
    white = write_model(tmp_path / "white")
    chat = Chat(meta={})
    assert chat.get_character_form() == "black"
    chat.update_custom_live2d_model_meta("祥子", str(black))
    chat.set_character_form("white")
    chat.update_custom_live2d_model_meta("祥子", str(white))
    restored = Chat(meta=chat.meta.to_dict())
    assert restored.get_character_form() == "white"
    assert restored.get_custom_live2d_model_meta("祥子") == str(white)
    restored.clear_custom_live2d_model_meta("祥子")
    assert restored.get_custom_live2d_model_meta("祥子") == "default-white"
    restored.set_character_form("black")
    black.unlink()
    assert restored.get_custom_live2d_model_meta("祥子") == str(black)
    assert explicit_model(restored.meta, "祥子", "sakiko", "white") is None
    assert chat.get_character_form() == "white"


def test_catalog_selects_each_default_and_shared_extras(tmp_path):
    root = tmp_path / "live2d_related"
    white = write_model(root / "sakiko/live2D_model", "v2")
    black = write_model(root / "sakiko/live2D_model_costume")
    extra = write_model(root / "sakiko/extra_model/dress")
    catalog = Live2DModelCatalog(root)
    assert [item.model_json_path for item in catalog.list_options("sakiko", form="black")] == [black, extra]
    assert [item.model_json_path for item in catalog.list_options("sakiko", form="white")] == [white, extra]
    assert catalog.find_by_path("sakiko", black, form="black").is_default
    assert catalog.find_by_path("sakiko", extra, form="white").is_default is False
    assert shared_config_path(black) == root / "sakiko/performance.shared.json"


@pytest.mark.parametrize("version,suffix", [("v2", ".mtn"), ("v3", ".motion3.json")])
def test_mask_actions_are_explicit_and_failed_playback_does_not_flip_state(tmp_path, version, suffix):
    path = write_model(tmp_path, version)
    assert mask_actions(str(path)) == {}
    save_mask_actions(str(path), {"on": "put_on" + suffix, "off": "take_off" + suffix})
    model = SimpleNamespace(model_json_path=str(path), StartMotionFile=Mock(return_value=True))
    player = SimpleNamespace(if_sakiko=True, sakiko_state=True, if_mask=False,
                             _reset_long_audio_motion_loop=Mock(), onStartCallback=Mock(), onFinishCallback=Mock())
    assert play_mask_action(player, model, "on")
    assert player.if_mask is True
    assert model.StartMotionFile.call_args.args[0] == str(tmp_path / ("put_on" + suffix))
    model.StartMotionFile.return_value = False
    assert not play_mask_action(player, model, "off")
    assert player.if_mask is True
    player.sakiko_state = False
    model.StartMotionFile.reset_mock()
    assert not play_mask_action(player, model, "on")
    model.StartMotionFile.assert_not_called()
    save_mask_actions(str(path), {})
    assert mask_actions(str(path)) == {}


def test_mask_rejects_external_files_and_only_missing_channel_is_disabled(tmp_path):
    path = write_model(tmp_path)
    save_mask_actions(str(path), {"off": "take_off.motion3.json"})
    before = performance_config_path(path).read_text()
    with pytest.raises(ValueError):
        save_mask_actions(str(path), {"on": "../other.motion3.json"})
    assert performance_config_path(path).read_text() == before
    projected = projected_model_document(path)
    assert projected["FileReferences"]["Motions"]["__dsakiko_mask__"] == [{"File": "take_off.motion3.json"}]
    (path.parent / "take_off.motion3.json").unlink()
    assert mask_actions(str(path)) == {}


def test_tool_lists_current_form_and_propagates_save_failure(tmp_path, monkeypatch):
    import live2d_support.model_catalog as module
    root = tmp_path / "live2d_related"
    black = write_model(root / "sakiko/live2D_model_costume", "v2")
    white = write_model(root / "sakiko/live2D_model", "v2")
    catalog = Live2DModelCatalog(root)
    monkeypatch.setattr(module, "Live2DModelCatalog", lambda *_args: catalog)
    registry = ToolRegistry()
    state = {"form": "black"}
    change = Mock(return_value={"ok": False, "error": "save failed"})
    register_live2d_tools(registry, lambda: "sakiko", change, lambda: state["form"])
    def execute(name, args):
        return json.loads(registry.execute(ToolCallRequest("call", name, args)).model_content)
    listing = execute("fetch_all_live2d_models", {})
    assert listing["current_form"] == "black"
    assert listing["models"][0]["model_json_path"] == str(black)
    result = execute("change_character_live2d", {"model_json_path": str(black)})
    assert result == {"ok": False, "error": "save failed"}
    state["form"] = "white"
    assert execute("fetch_all_live2d_models", {})["models"][0]["model_json_path"] == str(white)
    change.reset_mock()
    assert execute("change_character_live2d", {"model_json_path": str(black)})["ok"] is False
    change.assert_not_called()


@pytest.mark.parametrize("saved", [True, False])
def test_desktop_form_command_resolves_model_and_rolls_back_save_failure(tmp_path, monkeypatch, saved):
    import dp_local2
    monkeypatch.setattr(dp_local2.time, "sleep", lambda seconds: None)
    black = write_model(tmp_path / "black", "v2")
    white = write_model(tmp_path / "white")
    chat = Chat(meta={"live2d_form_models": {"sakiko": {"black": str(black), "white": str(white)}}})
    subject = dp_local2.DSLocalAndVoiceGen.__new__(dp_local2.DSLocalAndVoiceGen)
    subject.current_chat_id = chat.chat_id
    subject.chat_manager = SimpleNamespace(get_chat_by_id=lambda chat_id: chat,
                                          save=Mock(side_effect=None if saved else OSError("write failed")))
    subject.if_sakiko = True
    subject.sakiko_state = True
    queues = [Queue() for _ in range(5)]
    subject._handle_non_message_command({"type": "legacy_command", "command": "conv", "chat_id": chat.chat_id}, *queues)
    if saved:
        target = queues[3].get_nowait()
        assert target["model_json"] == str(white)
        assert target["sakiko_state"] is False
        assert chat.get_character_form() == "white"
        assert subject.sakiko_state is False
        assert queues[2].empty()
    else:
        assert chat.meta.character_forms == {}
        assert subject.sakiko_state is True
        assert queues[3].empty()
    assert chat.meta.live2d_form_models["sakiko"] == {"black": str(black), "white": str(white)}
