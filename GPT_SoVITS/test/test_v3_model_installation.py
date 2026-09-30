"""V3 安装的布局、演出资源、失败回滚与 V2 隔离回归。"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import stat
from threading import Event
from unittest.mock import Mock, patch
import zipfile

import pytest

from live2d_download.hosted.catalog import Selection
from live2d_download.hosted.installer import V3Installer
from live2d_download.hosted.service import Resource, DownloadError, Cancelled
from live2d_support.performance_catalog import load_performance_catalog


def package(tmp_path, *, description="包内角色描述", broken=False, extra=None):
    """构造带嵌套目录、同名贴图及独立演出配置的 V3 包。"""
    motions = ["mtn_idle01_C", "mtn_kime01_C", "mtn_kime01_L", "mtn_kime01_R", "mtn_smile01_C",
               "mtn_thinking01_C", "mtn_nod01_C", "mtn_nf_left01_C", "mtn_angry01_C"]
    assets = [{"File": "motions/" + name + ".motion3.json"} for name in motions]
    assets[1].update(Sound="sounds/test.wav", FadeInTime=0.25)
    data = {"Version": 3, "FileReferences": {
        "Moc": "missing.moc3" if broken else "model.moc3",
        "Textures": ["textures/texture.png", "other/texture.png"], "Physics": "physics/physics.json",
        "Expressions": [{"Name": "smile", "File": "expressions/exp_smile01.exp3.json"}],
        "Motions": {"Default": assets}}}
    sidecar = {"series": "custom", "motions": {"mtn_kime01": "摆出自信姿势"},
               "expressions": {"smile": "微笑"}, "bindings": {"motions/mtn_nod01_C.motion3.json": "nod"},
               "presets": [{"id": "greeting", "motion": "mtn_kime01", "expression": "smile"}],
               "custom_expressions": {"smile": "expressions/exp_smile01.exp3.json"}}
    files = {"model.model3.json": json.dumps(data), "model.moc3": "moc", "textures/texture.png": "first",
             "other/texture.png": "second", "physics/physics.json": "{}", "sounds/test.wav": "wave",
             "expressions/exp_smile01.exp3.json": '{"Parameters": []}',
             "model.model3.performance.json": json.dumps(sidecar), "notes/readme.txt": "保留包内说明"}
    for name in motions + ["mtn_unknown01_C"]:
        files["motions/" + name + ".motion3.json"] = '{"Meta": {"Loop": false}, "Curves": []}'
    if description is not None:
        files["character_description.txt"] = description
    files.update(extra or {})
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name, content in files.items():
            z.writestr("wrapper/" + name, content.encode("utf-8"))
    return archive


def resource(character="viola", title="春季常服"):
    """返回无需网络的安装来源快照。"""
    return Resource("model", f"ournotes/{character}/model/r2.zip", title, character, model_id="model")


def assert_model(path):
    """检查引用真实存在、平铺后贴图不覆盖、标准动作和演出描述完整。"""
    assert all(p.is_file() for p in path.iterdir())
    assert not (path / "character_description.txt").exists()
    data = json.loads((path / "model.model3.json").read_text(encoding="utf-8"))
    refs = data["FileReferences"]
    assert set((path / file).read_text() for file in refs["Textures"]) == {"first", "second"}
    for file in [refs["Moc"], refs["Physics"], *refs["Textures"], *[x["File"] for x in refs["Expressions"]]]:
        assert "/" not in file and (path / file).is_file()
    assert {x["File"] for x in refs["Motions"]["IDLE"]} == {
        "mtn_kime01_C.motion3.json", "mtn_smile01_C.motion3.json", "mtn_nf_left01_C.motion3.json"}
    assert [x["File"] for x in refs["Motions"]["idle_motion"]] == ["mtn_idle01_C.motion3.json"]
    assert [x["File"] for x in refs["Motions"]["idle_motion_C"]] == ["mtn_idle01_C.motion3.json"]
    assert {x["File"] for x in refs["Motions"]["text_generating"]} == {"mtn_thinking01_C.motion3.json"}
    assert {x["File"] for x in refs["Motions"]["talking_motion"]} == {"mtn_nod01_C.motion3.json"}
    for name, entries in refs["Motions"].items():
        if not name.endswith(("_L", "_R")):
            assert not any(x["File"].endswith(("_L.motion3.json", "_R.motion3.json")) for x in entries)
        for entry in entries:
            for key in ("File", "Sound"):
                if key in entry:
                    assert (path / entry[key]).is_file()
    catalog = load_performance_catalog(path / "model.model3.json")
    assert "mtn_unknown01" in catalog.motions
    assert "nod" in catalog.motions
    assert catalog.descriptions["motions"]["mtn_kime01"] == "摆出自信姿势"
    assert catalog.descriptions["expressions"]["smile"] == "微笑"
    assert len(catalog.valid_presets()) == 1
    assert catalog.config["custom_expressions"]["smile"] == "exp_smile01.exp3.json"


def test_new_character_flatten_references_and_description(tmp_path):
    archive = package(tmp_path)
    original = archive.read_bytes()
    result = V3Installer(None, tmp_path).install_archive(archive, resource(), Selection("new", "viola"), Event())
    assert result.new_character and not result.description_missing
    assert (result.path / "name.txt").read_text(encoding="utf-8") == "薇欧拉"
    assert (result.path / "character_description.txt").read_text(encoding="utf-8") == "包内角色描述"
    assert_model(result.path / "live2D_model")
    assert archive.read_bytes() == original
    assert not list((tmp_path / "live2d_related").glob(".v3-install-*"))


@pytest.mark.parametrize("has_default", [False, True])
def test_existing_default_or_extra_preserves_identity_and_description(tmp_path, has_default):
    character = tmp_path / "live2d_related/custom"
    character.mkdir(parents=True)
    (character / "name.txt").write_text("本地身份", encoding="utf-8")
    (character / "character_description.txt").write_text("用户修改的人设", encoding="utf-8")
    if has_default:
        default = character / "live2D_model"
        default.mkdir()
        (default / "old.model.json").write_text("{}")
    installer = V3Installer(None, tmp_path)
    archive = package(tmp_path)
    result = installer.install_archive(archive, resource(), Selection("existing", "viola", "custom"), Event())
    assert result.path.parent.name == ("extra_model" if has_default else "live2D_model")
    assert (character / "character_description.txt").read_text(encoding="utf-8") == "用户修改的人设"
    assert (character / "name.txt").read_text(encoding="utf-8") == "本地身份"
    assert_model(result.path)
    again = installer.install_archive(archive, resource(), Selection("existing", "viola", "custom"), Event())
    assert again.path != result.path
    assert_model(result.path)


@pytest.mark.parametrize("description", [None, ""])
def test_absent_description_does_not_generate_persona(tmp_path, description):
    result = V3Installer(None, tmp_path).install_archive(
        package(tmp_path, description=description), resource(), Selection("new", "viola"), Event())
    assert result.description_missing
    if description is None:
        assert not (result.path / "character_description.txt").exists()


@pytest.mark.parametrize("identity, copied", [("viola", True), ("custom", False)])
def test_only_same_identity_receives_missing_description(tmp_path, identity, copied):
    character = tmp_path / "live2d_related" / identity
    character.mkdir(parents=True)
    result = V3Installer(None, tmp_path).install_archive(
        package(tmp_path), resource(), Selection("existing", "viola", identity), Event())
    assert (character / "character_description.txt").exists() is copied
    assert result.description_missing is not copied


def test_missing_asset_rolls_back_before_publishing(tmp_path):
    with pytest.raises(DownloadError, match="缺少引用"):
        V3Installer(None, tmp_path).install_archive(package(tmp_path, broken=True), resource(), Selection("new", "viola"), Event())
    assert list((tmp_path / "live2d_related").iterdir()) == []


def test_cancellation_after_normalization_never_publishes(tmp_path):
    from live2d_support.model_normalizer import normalize_model3_for_project
    event = Event()

    def cancel_after(*args, **kwargs):
        result = normalize_model3_for_project(*args, **kwargs)
        event.set()
        return result

    with patch("live2d_download.hosted.installer.normalize_model3_for_project", side_effect=cancel_after):
        with pytest.raises(Cancelled):
            V3Installer(None, tmp_path).install_archive(package(tmp_path), resource(), Selection("new", "viola"), event)
    assert list((tmp_path / "live2d_related").iterdir()) == []


@pytest.mark.parametrize("bad_path", ["../escape", "C:/escape", "foo/../../escape", "a:stream", "CON.txt"])
def test_unsafe_zip_does_not_escape(tmp_path, bad_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(bad_path, "bad")
    with pytest.raises(DownloadError, match="路径"):
        V3Installer(None, tmp_path).install_archive(archive, resource(), Selection("new", "viola"), Event())
    assert not (tmp_path / "escape").exists()
    assert list((tmp_path / "live2d_related").iterdir()) == []


def test_symlink_and_multiple_models_are_rejected(tmp_path):
    archive = package(tmp_path, extra={"second.model3.json": "{}"})
    installer = V3Installer(None, tmp_path)
    with pytest.raises(DownloadError, match="唯一"):
        installer.install_archive(archive, resource(), Selection("new", "viola"), Event())
    with zipfile.ZipFile(archive, "w") as z:
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "../outside")
    with pytest.raises(DownloadError, match="链接"):
        installer.install_archive(archive, resource(), Selection("new", "viola"), Event())


def test_duplicate_new_character_stops_before_downloading(tmp_path):
    character = tmp_path / "live2d_related/viola"
    character.mkdir(parents=True)
    service = Mock()
    with pytest.raises(DownloadError, match="已存在"):
        V3Installer(service, tmp_path).download(resource(), Selection("new", "viola"), tmp_path / "cache", Event(), lambda *_: None)
    service.download.assert_not_called()


def test_static_model_with_no_motions_is_installable(tmp_path):
    """静态模型仍可安装，不能因为动作缺失伪造路径或破坏标准组结构。"""
    archive = tmp_path / "static.zip"
    data = {"Version": 3, "FileReferences": {"Moc": "model.moc3", "Textures": ["texture.png"]}}
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("model.MODEL3.JSON", json.dumps(data))
        z.writestr("model.moc3", "moc")
        z.writestr("texture.png", "png")
    result = V3Installer(None, tmp_path).install_archive(archive, resource(), Selection("new", "viola"), Event())
    normalized = json.loads((result.path / "live2D_model/model.model3.json").read_text(encoding="utf-8"))
    assert normalized["FileReferences"]["Motions"]
    assert all(entries == [] for entries in normalized["FileReferences"]["Motions"].values())


@pytest.mark.parametrize("names, expected", [
    (["mtn_smile01_C", "mtn_idle02_C", "mtn_idle01_C", "mtn_idle01"], "mtn_idle01_C"),
    (["mtn_smile01_C", "mtn_idle02_C", "mtn_idle01"], "mtn_idle02_C"),
    (["mtn_smile01_C", "mtn_idle01"], "mtn_idle01"),
    (["mtn_smile02_C", "mtn_smile01_C", "mtn_idle01_L"], "mtn_smile01_C"),
    (["mtn_kime02_C", "mtn_kime01_C"], "mtn_kime01_C"),
    (["mtn_idle01_L", "mtn_smile01_R"], None),
    (["ab12cd34_mtn_idle01_C", "mtn_smile01_C"], "ab12cd34_mtn_idle01_C"),
])
def test_downloaded_basic_idle_selects_one_with_center_idle_priority(tmp_path, names, expected):
    """基础待机按 idle C、无方向 idle、smile 和已有兜底顺序选择，左右方向仍隔离。"""
    from live2d_support.model_normalizer import normalize_model3_for_project
    files = [name + ".motion3.json" for name in names]
    for file in files:
        (tmp_path / file).write_text('{"Curves": []}', encoding="utf-8")
    path = tmp_path / "sample.model3.json"
    path.write_text(json.dumps({"FileReferences": {"Motions": {"Default": [{"File": file} for file in files]}}}), encoding="utf-8")
    normalize_model3_for_project(str(path), downloaded=True)
    groups = json.loads(path.read_text(encoding="utf-8"))["FileReferences"]["Motions"]
    assert [item["File"] for item in groups["idle_motion"]] == ([expected + ".motion3.json"] if expected else [])
    for group, entries in groups.items():
        if group.startswith("idle_motion"):
            assert len(entries) <= 1
    if "idle_motion_L" in groups:
        assert all(entry["File"].endswith("_L.motion3.json") for entry in groups["idle_motion_L"])
    if "idle_motion_R" in groups:
        assert all(entry["File"].endswith("_R.motion3.json") for entry in groups["idle_motion_R"])


def test_publish_failure_removes_new_description_and_preserves_existing_character(tmp_path):
    """最后发布失败时，连同本次新补的角色描述一起撤回。"""
    character = tmp_path / "live2d_related/viola"
    character.mkdir(parents=True)
    (character / "name.txt").write_text("薇欧拉", encoding="utf-8")
    archive = package(tmp_path)
    rename = Path.rename

    def fail_publish(path, target):
        if path.name == "model" and Path(target).parent.parent == character:
            raise OSError("模拟目标目录写入失败")
        return rename(path, target)

    with patch.object(Path, "rename", fail_publish), pytest.raises(DownloadError):
        V3Installer(None, tmp_path).install_archive(archive, resource(), Selection("existing", "viola", "viola"), Event())
    assert not (character / "character_description.txt").exists()
    assert (character / "name.txt").read_text(encoding="utf-8") == "薇欧拉"
    assert not list(character.rglob("*.model3.json"))


@pytest.mark.parametrize("broken", [False, True])
def test_download_install_cleans_only_own_cache(tmp_path, broken):
    archive = package(tmp_path, broken=broken)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "unrelated.zip").write_bytes(b"user-data")

    def downloaded(_resource, folder, _event, _progress):
        target = folder / "r2.zip"
        shutil.copy2(archive, target)
        return target

    installer = V3Installer(Mock(download=downloaded), tmp_path)
    if broken:
        with pytest.raises(DownloadError):
            installer.download(resource(), Selection("new", "viola"), cache, Event(), lambda *_: None)
    else:
        assert installer.download(resource(), Selection("new", "viola"), cache, Event(), lambda *_: None).path.is_dir()
    assert list(cache.iterdir()) == [cache / "unrelated.zip"]
