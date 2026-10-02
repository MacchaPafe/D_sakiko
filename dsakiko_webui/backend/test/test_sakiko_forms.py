"""WebUI 形态切换、过期选项与保存失败的集成回归。"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "GPT_SoVITS"))

from chat.chat import Chat
from test.test_sakiko_forms import write_model
from live2d_support.mask_actions import save_mask_actions
from dsakiko_webui.backend import assets as assets_module
from dsakiko_webui.backend.assets import AssetRegistry
from dsakiko_webui.backend.runtime import HeadlessRuntime
from dsakiko_webui.backend.live2d_presentation import Live2DPresentationResolver
from dsakiko_webui.backend.protocol import ProtocolError
from GPT_SoVITS.live2d_support.model_catalog import Live2DModelCatalog


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "live2d_related"
    white = write_model(root / "sakiko/live2D_model", "v2")
    black = write_model(root / "sakiko/live2D_model_costume")
    extra = write_model(root / "sakiko/extra_model/dress", "v2")
    monkeypatch.setattr(assets_module, "LIVE2D_ROOT", root)
    assets = AssetRegistry()
    subject = HeadlessRuntime(assets)
    subject.live2d_model_catalog = Live2DModelCatalog(root, tmp_path)
    subject.live2d_presentations = Live2DPresentationResolver(assets, tmp_path, root, Path(__file__).resolve().parents[3] / "GPT_SoVITS")
    character = SimpleNamespace(character_folder_name="sakiko", character_name="祥子", live2d_json=str(white))
    chat = Chat(meta={})
    chat.get_character_name = lambda: "祥子"
    subject.dp_chat = SimpleNamespace(current_chat=chat, current_chat_id=chat.chat_id, sakiko_state=True)
    subject.character_by_name = {"祥子": character}
    subject.chat_manager = SimpleNamespace(save=Mock())
    subject.status = "ready"
    yield subject, chat, white, black, extra
    subject.uploads.close()


def test_form_change_updates_presentation_and_stale_sheet_cannot_write(runtime):
    subject, chat, white, black, extra = runtime
    listing, _ = subject._get_live2d_model_options({"chat_id": chat.chat_id})
    assert listing["current_form"] == "black"
    choice = next(item for item in listing["options"] if item["name"] == "dress")
    result, events = subject._set_character_form({"chat_id": chat.chat_id, "form": "white"})
    assert result["current_form"] == "white"
    assert subject.dp_chat.sakiko_state is False
    assert events[0]["data"]["presentation"]["version"] == "v2"
    assert events[0]["data"]["presentation"]["current_form"] == "white"
    with pytest.raises(ProtocolError) as error:
        subject._select_live2d_model({"chat_id": chat.chat_id, "form": "black", "option_id": choice["option_id"]})
    assert error.value.code == "LIVE2D_OPTIONS_STALE"
    subject._select_live2d_model({"chat_id": chat.chat_id, "form": "white", "option_id": choice["option_id"]})
    assert chat.meta.live2d_form_models == {"sakiko": {"white": str(extra)}}
    subject._set_character_form({"chat_id": chat.chat_id, "form": "black"})
    assert subject.live2d_presentations.resolve(chat, subject.character_by_name["祥子"]).version == "v3"


def test_save_failure_and_busy_form_change_preserve_state(runtime):
    subject, chat, *_ = runtime
    subject.chat_manager.save.side_effect = OSError("disk full")
    with pytest.raises(ProtocolError) as error:
        subject._set_character_form({"chat_id": chat.chat_id, "form": "white"})
    assert error.value.code == "LIVE2D_SAVE_FAILED"
    assert chat.get_character_form() == "black"
    assert subject.dp_chat.sakiko_state is True
    subject.phase = "generating"
    with pytest.raises(ProtocolError) as error:
        subject._set_character_form({"chat_id": chat.chat_id, "form": "white"})
    assert error.value.code == "CHAT_BUSY"


def test_model_save_failure_rolls_back_only_current_form(runtime):
    subject, chat, white, black, extra = runtime
    chat.meta.live2d_form_models = {"sakiko": {"white": str(white)}}
    listing, _ = subject._get_live2d_model_options({"chat_id": chat.chat_id})
    choice = next(item for item in listing["options"] if item["name"] == "dress")
    subject.chat_manager.save.side_effect = OSError("disk full")
    with pytest.raises(ProtocolError):
        subject._select_live2d_model({"chat_id": chat.chat_id, "form": "black", "option_id": choice["option_id"]})
    assert chat.meta.live2d_form_models == {"sakiko": {"white": str(white)}}


def test_mask_off_only_has_index_zero_and_white_disables_operations(runtime):
    subject, chat, white, black, _ = runtime
    save_mask_actions(str(black), {"off": "take_off.motion3.json"})
    presentation = subject.live2d_presentations.resolve(chat, subject.character_by_name["祥子"])
    assert presentation.mask_action_indices == {"off": 0}
    _, events = subject._mask_action({"chat_id": chat.chat_id, "action": "off"})
    assert events[0]["data"]["index"] == 0
    assert events[0]["data"]["target_id"] == presentation.target_id
    with pytest.raises(ProtocolError):
        subject._mask_action({"chat_id": chat.chat_id, "action": "on"})
    previous_revision = presentation.revision
    save_mask_actions(str(black), {"on": "put_on.motion3.json", "off": "take_off.motion3.json"})
    assert subject.live2d_presentations.resolve(chat, subject.character_by_name["祥子"]).revision != previous_revision
    subject._set_character_form({"chat_id": chat.chat_id, "form": "white"})
    with pytest.raises(ProtocolError):
        subject._mask_action({"chat_id": chat.chat_id, "action": "off"})
