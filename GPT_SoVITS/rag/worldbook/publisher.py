"""把审核后的 Stage 3 artifact 发布为正式世界书包。"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from .hashing import file_sha256
from .models import ContentFileRecord, EntryType, WorldbookEntry, WorldbookManifest


_COLLECTION_TO_ENTRY_TYPE: dict[str, EntryType] = {
    "story_events": "story_event",
    "character_relations": "character_relation",
    "lore_entries": "lore_entry",
}


def publish_worldbook_package(
    artifact_path: Path,
    package_dir: Path,
    package_id: str,
    package_version: str,
    display_name: str,
    timeline_id: str,
    id_map_path: Path,
    allocate_new_ids: bool = False,
) -> WorldbookManifest:
    """清除开发证据并输出 manifest、内容分片和稳定 ID map。"""

    raw = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("Stage 3 artifact 顶层必须是对象")
    id_map = _load_id_map(id_map_path)
    content_dir = package_dir / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    content_records: list[ContentFileRecord] = []
    for collection_name, entry_type in _COLLECTION_TO_ENTRY_TYPE.items():
        records = raw.get(collection_name, [])
        if not isinstance(records, list):
            raise TypeError(f"{collection_name} 必须是列表")
        entries: list[WorldbookEntry] = []
        for raw_record in records:
            if not isinstance(raw_record, dict):
                raise TypeError(f"{collection_name} 中的 record 必须是对象")
            source_key = str(raw_record.get("point_id", "")).strip()
            document = raw_record.get("document")
            if not source_key or not isinstance(document, dict):
                raise ValueError(f"{collection_name} record 缺少 point_id 或 document")
            entry_id = id_map.get(source_key)
            if entry_id is None:
                if not allocate_new_ids:
                    raise ValueError(f"来源 {source_key} 尚未分配稳定 entry ID")
                entry_id = uuid4()
                id_map[source_key] = entry_id
            entries.append(WorldbookEntry(entry_id=entry_id, entry_type=entry_type, schema_version=0, content=document))
        relative_path = f"content/{collection_name}.json"
        output_path = package_dir / relative_path
        output_path.write_text(json.dumps({"entries": [entry.model_dump(mode="json") for entry in entries]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        content_records.append(ContentFileRecord(path=relative_path, sha256=file_sha256(output_path), entry_type=entry_type))
    manifest = WorldbookManifest(
        package_id=package_id,
        package_version=package_version,
        display_name=display_name,
        package_type="season",
        timeline_id=timeline_id,
        content_files=content_records,
    )
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "manifest.json").write_text(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _save_id_map(id_map_path, id_map)
    return manifest


def _load_id_map(path: Path) -> dict[str, UUID]:
    """读取开发侧来源键到稳定 UUID 的映射。"""

    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("entry ID map 必须是对象")
    return {str(key): UUID(str(value)) for key, value in raw.items()}


def _save_id_map(path: Path, id_map: dict[str, UUID]) -> None:
    """保存开发侧稳定 ID map。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: str(value) for key, value in sorted(id_map.items())}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
