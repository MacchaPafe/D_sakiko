"""V2 首次模型安装、跨版本换装及已有角色资源保留回归。"""
import json
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

from live2d_download.hosted.catalog import CHARACTERS, Selection
from live2d_download.hosted.legacy import LegacyService
from ui_constants import AddCostume


@pytest.fixture
def resources(tmp_path, monkeypatch):
    """隔离旧安装器的相对路径，并准备可加载的资源引用布局。"""
    app = tmp_path / "GPT_SoVITS"
    cache = app / ".model_download_cache/costume"
    for directory in (cache, cache / "textures", cache / "motions", cache / "expressions"):
        directory.mkdir(parents=True, exist_ok=True)
    (cache / "new.moc").write_bytes(b"moc")
    (cache / "new.physics.json").write_text("{}")
    (cache / "textures/texture.png").write_bytes(b"texture")
    (cache / "motions/idle01.mtn").write_text("motion")
    (cache / "expressions/idle01.exp.json").write_text("{}")
    character = tmp_path / "live2d_related/custom"
    character.mkdir(parents=True)
    (character / "name.txt").write_text("用户角色", encoding="utf-8")
    (character / "character_description.txt").write_text("用户人设", encoding="utf-8")
    voice = tmp_path / "reference_audio/custom"
    voice.mkdir(parents=True)
    (voice / "QT_style.json").write_text("用户配色", encoding="utf-8")
    (voice / "voice.pth").write_bytes(b"voice")
    monkeypatch.chdir(app)
    return tmp_path, character, cache


def assert_model(path):
    """检查生成配置只引用实际复制的模型、动作、表情和物理文件。"""
    model = next(path.glob("*.model.json"))
    data = json.loads(model.read_text(encoding="utf-8"))
    references = [data["model"], *data["textures"]]
    if "physics" in data:
        references.append(data["physics"])
    references.extend(entry["file"] for entry in data["expressions"])
    references.extend(entry["file"] for entries in data["motions"].values() for entry in entries)
    assert all((path / file).is_file() for file in references)
    return data


@pytest.mark.parametrize("empty_default", [False, True])
def test_first_model_becomes_default_and_preserves_character(resources, empty_default):
    root, character, _ = resources
    if empty_default:
        (character / "live2D_model").mkdir()
        (character / "live2D_model/notes.txt").write_text("保留", encoding="utf-8")
    result = Path(AddCostume.add_costume_for_existed_char("custom", "costume", "常服"))
    assert result.parent == character / "live2D_model"
    assert_model(result)
    assert not (character / "extra_model").exists()
    assert (character / "name.txt").read_text(encoding="utf-8") == "用户角色"
    assert (character / "character_description.txt").read_text(encoding="utf-8") == "用户人设"
    assert (root / "reference_audio/custom/QT_style.json").read_text(encoding="utf-8") == "用户配色"
    assert (root / "reference_audio/custom/voice.pth").read_bytes() == b"voice"
    if empty_default:
        assert (character / "live2D_model/notes.txt").read_text(encoding="utf-8") == "保留"
    repeated = Path(AddCostume.add_costume_for_existed_char("custom", "costume", "常服"))
    assert repeated.parent == character / "extra_model"
    assert_model(repeated)
    assert_model(result)


@pytest.mark.parametrize("nested", [False, True])
def test_v2_default_template_keeps_custom_motion_groups(resources, nested):
    _, character, cache = resources
    default = character / "live2D_model"
    if nested:
        default /= "已导入模型"
    default.mkdir(parents=True)
    (default / "custom.mtn").write_text("custom")
    (default / "custom.exp.json").write_text("{}")
    data = {"model": "old.moc", "textures": ["old.png"],
            "physics": "old.physics.json", "physics_v2": {"file": "old.physics.json"},
            "motions": {"custom": [{"file": "custom.mtn"}]},
            "expressions": [{"name": "custom", "file": "custom.exp.json"}]}
    original = json.dumps(data)
    (default / "old.model.json").write_text(original)
    (cache / "new.physics.json").unlink()
    result = Path(AddCostume.add_costume_for_existed_char("custom", "costume", "常服"))
    assert result.parent == character / "extra_model"
    installed = assert_model(result)
    assert installed["motions"] == data["motions"]
    assert installed["expressions"] == data["expressions"]
    assert "physics" not in installed and "physics_v2" not in installed
    assert (default / "old.model.json").read_text() == original


def test_v3_default_gets_independent_v2_extra_and_unique_names(resources):
    _, character, _ = resources
    default = character / "live2D_model/v3"
    default.mkdir(parents=True)
    (default / "old.model3.json").write_text("{}")
    first = Path(AddCostume.add_costume_for_existed_char("custom", "costume", "常服"))
    original = (first / "3.model.json").read_bytes()
    (first / "user.txt").write_text("user")
    second = Path(AddCostume.add_costume_for_existed_char("custom", "costume", "常服"))
    assert first.parent == second.parent == character / "extra_model"
    assert first != second
    assert_model(first)
    assert_model(second)
    assert (first / "3.model.json").read_bytes() == original
    assert (first / "user.txt").read_text() == "user"
    assert (default / "old.model3.json").read_text() == "{}"


def test_static_model_does_not_reference_missing_expressions_or_motions(resources):
    _, _, cache = resources
    (cache / "motions/idle01.mtn").unlink()
    (cache / "expressions/idle01.exp.json").unlink()
    result = Path(AddCostume.add_costume_for_existed_char("custom", "costume", "静态模型"))
    data = assert_model(result)
    assert data["expressions"] == []
    assert all(entries == [] for entries in data["motions"].values())


def test_failed_copy_leaves_no_published_model(resources, monkeypatch):
    _, character, _ = resources

    def fail_copy(*args, **kwargs):
        raise OSError("模拟复制失败")

    monkeypatch.setattr("ui_constants.shutil.copy2", fail_copy)
    with pytest.raises(OSError, match="模拟复制失败"):
        AddCostume.add_costume_for_existed_char("custom", "costume", "常服")
    assert list((character / "live2D_model").iterdir()) == []
    assert (character / "character_description.txt").read_text(encoding="utf-8") == "用户人设"


def test_legacy_service_returns_first_default_install_path(resources, monkeypatch):
    root, character, cache = resources
    monkeypatch.setattr("live2d_download.hosted.legacy.PROJECT_ROOT", root)
    monkeypatch.setattr("live2d_download.hosted.legacy.APP_ROOT", root / "GPT_SoVITS")
    monkeypatch.setattr("live2d_download.hosted.legacy.BestdoriClient", Mock())
    monkeypatch.setattr("live2d_download.hosted.legacy.Live2dDownloader", Mock())
    result = LegacyService().download(
        "costume", "常服", Selection("existing", "tomori", "custom"), Event(), lambda *_: None)
    assert result.resolve().parent == character / "live2D_model"
    assert_model(result)
    assert not cache.exists()


def test_new_character_still_gets_default_model(resources):
    root, _, _ = resources
    display_name = CHARACTERS["tomori"]["display_name"]
    AddCostume.add_costume_for_new_character(display_name, "tomori", "costume")
    character = root / "live2d_related/tomori"
    assert_model(character / "live2D_model")
    assert (character / "name.txt").read_text(encoding="utf-8") == display_name
    assert (character / "character_description.txt").read_text(encoding="utf-8").strip()
