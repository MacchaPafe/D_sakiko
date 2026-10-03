"""V3 模型标识识别及历史模型目录兼容性回归。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from live2d_download.hosted.installed_models import find_installed_v3


def write_model(directory: Path, model_id: str = "winter", suffix: str = ".model3.json") -> Path:
    """创建具有真实核心资源引用的最小模型配置。"""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model.moc3").write_bytes(b"moc")
    textures = directory / "textures"
    textures.mkdir(exist_ok=True)
    (textures / "texture.png").write_bytes(b"texture")
    model = directory / (model_id + suffix)
    model.write_text(json.dumps({"Version": 3, "FileReferences": {
        "Moc": "model.moc3", "Textures": ["textures/texture.png"]}}), encoding="utf-8")
    return model


@pytest.mark.parametrize("relative", ["live2D_model", "live2D_model/首个模型", "extra_model/winter",
                                     "extra_model/高一冬季校服", "extra_model/高一冬季校服_2",
                                     "extra_model/改过名字/内层"])
def test_identifies_model_independently_of_folder_name(tmp_path: Path, relative: str) -> None:
    """默认、额外、改名、编号及嵌套模型均通过配置文件标识识别。"""
    model = write_model(tmp_path / relative)
    assert find_installed_v3(tmp_path, "winter") == model.parent


@pytest.mark.parametrize("damage", ["json", "moc", "texture", "empty_refs"])
def test_damaged_model_is_not_marked_installed(tmp_path: Path, damage: str) -> None:
    """损坏配置和缺少核心资源的模型不能阻止用户重新下载。"""
    model = write_model(tmp_path / "extra_model/校服")
    if damage == "json":
        model.write_text("broken", encoding="utf-8")
    elif damage == "empty_refs":
        model.write_text("{}", encoding="utf-8")
    else:
        (model.parent / ("model.moc3" if damage == "moc" else "textures/texture.png")).unlink()
    assert find_installed_v3(tmp_path, "winter") is None


def test_checks_target_identity_and_skips_staging_or_symlink_folders(tmp_path: Path) -> None:
    """只识别当前接收角色中的真实已发布模型，不扫描其他角色、临时目录或链接。"""
    other = tmp_path / "other"
    model = write_model(other / "extra_model/校服")
    target = tmp_path / "target"
    write_model(target / "extra_model/校服", "summer")
    write_model(target / "extra_model/.staging")
    (target / "extra_model/linked").symlink_to(model.parent, target_is_directory=True)
    assert find_installed_v3(target, "winter") is None
    assert find_installed_v3(other, "winter") == model.parent


def test_uses_valid_copy_when_another_copy_is_broken(tmp_path: Path) -> None:
    """已有多份模型时跳过损坏副本，返回仍可用副本的实际位置。"""
    broken = write_model(tmp_path / "extra_model/校服")
    (broken.parent / "model.moc3").unlink()
    valid = write_model(tmp_path / "extra_model/校服_2", suffix=".MODEL3.JSON")
    assert find_installed_v3(tmp_path, "winter") == valid.parent
