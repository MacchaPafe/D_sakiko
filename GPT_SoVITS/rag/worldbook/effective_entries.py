"""把只读官方内容与包级用户状态合并为有效条目。"""

from __future__ import annotations

from .hashing import canonical_json_sha256
from .models import EffectiveWorldbookEntry, ValidationIssue, WorldbookEntry, WorldbookUserState


def entry_revision(entry: WorldbookEntry) -> str:
    """计算不受 JSON 排版影响的稳定条目 revision。"""

    return canonical_json_sha256(entry.model_dump(mode="json"))


def merge_effective_entries(
    package_id: str,
    official_entries: list[WorldbookEntry],
    user_state: WorldbookUserState,
) -> tuple[list[EffectiveWorldbookEntry], list[ValidationIssue]]:
    """应用 tombstone、Override 和扩展，并隔离不兼容条目。"""

    issues: list[ValidationIssue] = []
    tombstones = {item.entry_id: item for item in user_state.tombstones}
    overrides = {item.entry_id: item for item in user_state.overrides}
    official_map = {entry.entry_id: entry for entry in official_entries}
    effective: list[EffectiveWorldbookEntry] = []
    for official in official_entries:
        base_revision = entry_revision(official)
        if official.entry_id in tombstones:
            continue
        override = overrides.get(official.entry_id)
        if override is None:
            effective.append(EffectiveWorldbookEntry(package_id=package_id, entry=official, revision=base_revision, source="official"))
            continue
        if override.entry_type != official.entry_type:
            issues.append(ValidationIssue(code="incompatible_override", message="Override 不得改变 entry_type", package_id=package_id, entry_id=official.entry_id))
            continue
        replacement = WorldbookEntry(entry_id=official.entry_id, entry_type=override.entry_type, schema_version=override.schema_version, content=override.content)
        effective.append(EffectiveWorldbookEntry(package_id=package_id, entry=replacement, revision=entry_revision(replacement), source="override", base_conflict=override.base_revision != base_revision))
    known_ids = set(official_map)
    for extension in user_state.extensions:
        if extension.entry_id in known_ids:
            issues.append(ValidationIssue(code="duplicate_extension_id", message="用户扩展 ID 与官方条目重复", package_id=package_id, entry_id=extension.entry_id))
            continue
        known_ids.add(extension.entry_id)
        effective.append(EffectiveWorldbookEntry(package_id=package_id, entry=extension, revision=entry_revision(extension), source="extension"))
    return effective, issues
