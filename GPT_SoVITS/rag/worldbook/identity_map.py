"""开发侧稳定身份到正式 UUID 的生命周期管理。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from uuid import UUID, uuid4, uuid5

from pydantic import ValidationError

from .build_models import BuildIdentityChange, IdentityMapRecord, WorldbookIdentityMap


_PROVISIONAL_NAMESPACE = UUID("958e4c93-f9a7-4d64-a09c-a6a0a2e7b731")


def load_identity_map(path: Path, package_id: str) -> WorldbookIdentityMap:
    """读取结构化 ID map；不存在时返回空映射。"""

    if not path.exists():
        return WorldbookIdentityMap(package_id=package_id)
    try:
        artifact = WorldbookIdentityMap.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ValueError(f"无效的世界书 ID map: {path}") from exc
    if artifact.package_id != package_id:
        raise ValueError("ID map 的 package_id 与 build spec 不一致")
    return artifact


def save_identity_map(path: Path, identity_map: WorldbookIdentityMap) -> None:
    """使用同目录临时文件安全保存 ID map。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        identity_map.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


class IdentityResolver:
    """在纯验证或正式发布模式下解析身份 UUID。"""

    def __init__(
        self,
        identity_map: WorldbookIdentityMap,
        allocate: bool,
        reactivate_ids: set[str] | None = None,
        reactivate_all: bool = False,
    ) -> None:
        """保存解析模式与一次性重新激活许可。"""

        self.identity_map = identity_map
        self.allocate = allocate
        self.reactivate_ids = reactivate_ids or set()
        self.reactivate_all = reactivate_all
        self.used_keys: set[str] = set()
        self.changes: list[BuildIdentityChange] = []

    def resolve(self, identity_key: str) -> UUID:
        """返回现有 UUID，或按当前模式分配正式／临时 UUID。"""

        self.used_keys.add(identity_key)
        record = self.identity_map.identities.get(identity_key)
        if record is None:
            entry_id = uuid4() if self.allocate else uuid5(_PROVISIONAL_NAMESPACE, identity_key)
            self.changes.append(
                BuildIdentityChange(
                    identity_key=identity_key,
                    change="allocate",
                    entry_id=entry_id if self.allocate else None,
                    provisional=not self.allocate,
                )
            )
            if self.allocate:
                self.identity_map.identities[identity_key] = IdentityMapRecord(entry_id=entry_id)
            return entry_id
        if record.status == "retired":
            raise ValueError(f"身份已 retired，不能自动恢复: {identity_key}")
        if record.status == "inactive":
            if not (self.reactivate_all or identity_key in self.reactivate_ids):
                raise ValueError(f"身份需要显式重新激活: {identity_key}")
            self.changes.append(
                BuildIdentityChange(identity_key=identity_key, change="reactivate", entry_id=record.entry_id)
            )
            if self.allocate:
                record.status = "active"
        return record.entry_id

    def finalize_removed(self, allowed_removed_ids: set[str], allow_all_removed: bool) -> None:
        """检查并停用本次不再发布的 active 身份。"""

        removed = sorted(
            key
            for key, record in self.identity_map.identities.items()
            if record.status == "active" and key not in self.used_keys
        )
        blocked = [] if allow_all_removed else [key for key in removed if key not in allowed_removed_ids]
        if blocked:
            raise ValueError("以下 active 身份将消失，需明确允许删除: " + ", ".join(blocked))
        for identity_key in removed:
            record = self.identity_map.identities[identity_key]
            self.changes.append(
                BuildIdentityChange(identity_key=identity_key, change="deactivate", entry_id=record.entry_id)
            )
            if self.allocate:
                record.status = "inactive"
