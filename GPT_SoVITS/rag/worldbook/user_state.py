"""包级用户状态的原子文件仓库。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import UUID

from .models import WorldbookEntry, WorldbookOverride, WorldbookTombstone, WorldbookUserState


class WorldbookUserStateRepository:
    """读取并原子保存每个包的 Override、扩展和隐藏状态。"""

    def __init__(self, root: Path) -> None:
        """保存用户状态根目录。"""

        self._root = root

    def load(self, package_id: str) -> WorldbookUserState:
        """读取包状态；文件不存在时返回空状态。"""

        path = self._path(package_id)
        if not path.exists():
            return WorldbookUserState(package_id=package_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        state = WorldbookUserState.model_validate(payload)
        if state.package_id != package_id:
            raise ValueError("用户状态 package_id 与文件名不一致")
        return state

    def save(self, state: WorldbookUserState) -> None:
        """使用同目录临时文件、fsync 和 replace 原子保存状态。"""

        self._root.mkdir(parents=True, exist_ok=True)
        target = self._path(state.package_id)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=self._root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state.model_dump(mode="json"), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def put_override(self, package_id: str, override: WorldbookOverride) -> WorldbookUserState:
        """新增或替换一条 Override。"""

        state = self.load(package_id)
        state.overrides = [item for item in state.overrides if item.entry_id != override.entry_id] + [override]
        self.save(state)
        return state

    def remove_override(self, package_id: str, entry_id: UUID) -> WorldbookUserState:
        """删除一条 Override。"""

        state = self.load(package_id)
        state.overrides = [item for item in state.overrides if item.entry_id != entry_id]
        self.save(state)
        return state

    def hide(self, package_id: str, tombstone: WorldbookTombstone) -> WorldbookUserState:
        """隐藏一条官方条目。"""

        state = self.load(package_id)
        state.tombstones = [item for item in state.tombstones if item.entry_id != tombstone.entry_id] + [tombstone]
        self.save(state)
        return state

    def restore(self, package_id: str, entry_id: UUID) -> WorldbookUserState:
        """恢复一条已隐藏官方条目。"""

        state = self.load(package_id)
        state.tombstones = [item for item in state.tombstones if item.entry_id != entry_id]
        self.save(state)
        return state

    def put_extension(self, package_id: str, entry: WorldbookEntry) -> WorldbookUserState:
        """新增或更新一条用户扩展。"""

        state = self.load(package_id)
        state.extensions = [item for item in state.extensions if item.entry_id != entry.entry_id] + [entry]
        self.save(state)
        return state

    def delete_extension(self, package_id: str, entry_id: UUID) -> WorldbookUserState:
        """永久删除一条用户扩展。"""

        state = self.load(package_id)
        state.extensions = [item for item in state.extensions if item.entry_id != entry_id]
        self.save(state)
        return state

    def _path(self, package_id: str) -> Path:
        """生成安全的包状态文件路径。"""

        if not package_id or any(character in package_id for character in "/\\"):
            raise ValueError("package_id 不能包含路径分隔符")
        return self._root / f"{package_id}.json"
