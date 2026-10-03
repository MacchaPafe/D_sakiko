"""读取本地接收角色，并统一新建冲突与默认接收角色的判定。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .catalog import CHARACTERS


@dataclass(frozen=True)
class LocalTarget:
    """记录磁盘角色身份及其是否具备接收模型的基本结构。"""

    identifier: str
    name: str
    usable: bool


def read_targets(project_root: Path) -> list[LocalTarget]:
    """扫描角色名称，保留不可用目录供冲突判断，但不将其作为默认接收角色。"""
    root = project_root / "live2d_related"
    if not root.is_dir():
        return []
    targets: list[LocalTarget] = []
    for folder in sorted(root.iterdir()):
        if folder.name.startswith(".") or not folder.is_dir():
            continue
        try:
            name = (folder / "name.txt").read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError):
            name = ""
        has_model = any(
            path.is_file() and path.name.lower().endswith((".model.json", ".model3.json"))
            for path in (folder / "live2D_model").rglob("*.json")
        ) if not folder.is_symlink() else False
        usable = bool(name) and not folder.is_symlink() and (
            (folder / "character_description.txt").is_file() or has_model
        )
        targets.append(LocalTarget(folder.name, name, usable))
    return targets


def creation_conflicts(project_root: Path, source: str, targets: Sequence[LocalTarget]) -> bool:
    """按预定目录或精确显示名称判断新角色是否与磁盘内容冲突。"""
    folder = project_root / "live2d_related" / source
    return folder.exists() or folder.is_symlink() or any(
        target.name == CHARACTERS[source]["display_name"] for target in targets
    )


def default_target(source: str, targets: Sequence[LocalTarget]) -> Optional[LocalTarget]:
    """只自动选择身份明确且可用的角色，目录与名称指向不同角色时交给用户。"""
    matches = [target for target in targets if target.identifier == source
               or target.name == CHARACTERS[source]["display_name"]]
    if len(matches) == 1 and matches[0].usable:
        return matches[0]
    return None
