"""按模型配置标识识别接收角色中已经安装的 V3 模型。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def _has_core_assets(model: Path) -> bool:
    """检查模型配置及其 Moc、贴图引用，避免将空目录或缺少核心资源的模型视为已安装。"""
    try:
        data = json.loads(model.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return False
        references = data.get("FileReferences")
        if not isinstance(references, dict):
            return False
        moc = references.get("Moc")
        textures = references.get("Textures")
        if not isinstance(textures, list) or not textures:
            return False
        return all(isinstance(name, str) and bool(name) and
                   (model.parent / name.replace("\\", "/")).is_file()
                   for name in [moc, *textures])
    except (OSError, ValueError):
        return False


def find_installed_v3(character: Path, model_id: str) -> Optional[Path]:
    """扫描当前接收角色的两个模型目录，返回首个具有对应配置和核心资源的实际模型目录。"""
    if not model_id or character.is_symlink():
        return None
    expected = (model_id + ".model3.json").casefold()
    for root in (character / "live2D_model", character / "extra_model"):
        if root.is_symlink() or not root.is_dir():
            continue
        for directory, folders, filenames in os.walk(root, followlinks=False):
            parent = Path(directory)
            folders[:] = sorted(name for name in folders
                                if not name.startswith(".") and not (parent / name).is_symlink())
            for filename in sorted(filenames):
                model = parent / filename
                if filename.casefold() == expected and not model.is_symlink() and _has_core_assets(model):
                    return parent
    return None
