"""把旧 SeasonId 标注 artifact 迁移到时间线与可空剧情学年。"""

from __future__ import annotations

import json
from pathlib import Path


def migrate_temporal_payload(payload: dict[str, object], timeline_id: str) -> dict[str, object]:
    """迁移一份 Stage 1/2/3 或已发布标注 JSON 的时间字段。"""

    migrated = _clone_json_object(payload)
    metadata = migrated.get("metadata")
    story_year: int | None = None
    if isinstance(metadata, dict):
        raw_year = metadata.pop("season_id", metadata.get("story_year"))
        story_year = raw_year if isinstance(raw_year, int) else None
        metadata["timeline_id"] = timeline_id
        metadata["story_year"] = story_year
    _migrate_scene_container(migrated, timeline_id, story_year)
    _migrate_record_list(migrated.get("story_events"), "story_event", timeline_id, story_year)
    _migrate_record_list(migrated.get("character_relations"), "character_relation", timeline_id, story_year)
    _migrate_record_list(migrated.get("lore_entries"), "lore_entry", timeline_id, story_year)
    _migrate_record_list(migrated.get("character_thoughts"), "character_thought", timeline_id, story_year)
    return migrated


def migrate_temporal_file(input_path: Path, output_path: Path, timeline_id: str) -> None:
    """读取、迁移并格式化保存一份 JSON artifact。"""

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("artifact 顶层必须是对象")
    migrated = migrate_temporal_payload(raw, timeline_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(migrated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def migrate_temporal_file_and_id_map(
    artifact_path: Path,
    id_map_path: Path,
    timeline_id: str,
) -> None:
    """迁移 artifact 的来源 ID，同时保留开发 ID map 中已经分配的 UUID。"""

    raw = json.loads(artifact_path.read_text(encoding="utf-8"))
    raw_id_map = json.loads(id_map_path.read_text(encoding="utf-8")) if id_map_path.exists() else {}
    if not isinstance(raw, dict) or not isinstance(raw_id_map, dict):
        raise TypeError("artifact 与 ID map 顶层必须是对象")
    migrated = migrate_temporal_payload(raw, timeline_id)
    migrated_id_map = {str(key): str(value) for key, value in raw_id_map.items()}
    for collection_name in ("story_events", "character_relations", "lore_entries"):
        before_records = raw.get(collection_name)
        after_records = migrated.get(collection_name)
        if not isinstance(before_records, list) or not isinstance(after_records, list):
            continue
        for before, after in zip(before_records, after_records):
            if not isinstance(before, dict) or not isinstance(after, dict):
                continue
            before_id = before.get("point_id")
            after_id = after.get("point_id")
            if isinstance(before_id, str) and isinstance(after_id, str) and before_id in migrated_id_map:
                migrated_id_map[after_id] = migrated_id_map.pop(before_id)
    artifact_path.write_text(json.dumps(migrated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    id_map_path.write_text(json.dumps(dict(sorted(migrated_id_map.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _clone_json_object(payload: dict[str, object]) -> dict[str, object]:
    """通过 JSON 往返创建只含 JSON 类型的深拷贝。"""

    cloned = json.loads(json.dumps(payload, ensure_ascii=False))
    if not isinstance(cloned, dict):
        raise TypeError("payload 顶层必须是对象")
    return cloned


def _migrate_scene_container(payload: dict[str, object], timeline_id: str, story_year: int | None) -> None:
    """迁移 Stage 1/2 场景列表中的范围字段。"""

    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        return
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        raw_year = scene.pop("season_id", story_year)
        scene["timeline_id"] = timeline_id
        scene["story_year"] = raw_year if isinstance(raw_year, int) else story_year


def _migrate_record_list(records: object, record_kind: str, timeline_id: str, story_year: int | None) -> None:
    """迁移 Stage 3 各集合 record 的 document 字段。"""

    if not isinstance(records, list):
        return
    for record in records:
        if not isinstance(record, dict):
            continue
        point_id = record.get("point_id")
        if isinstance(point_id, str):
            record["point_id"] = _migrate_point_id(point_id, record_kind, timeline_id, story_year)
        document = record.get("document")
        if not isinstance(document, dict):
            continue
        if record_kind == "story_event":
            raw_year = document.pop("season_id", story_year)
            document["timeline_id"] = timeline_id
            document["occurred_story_year"] = raw_year if isinstance(raw_year, int) else story_year
        elif record_kind == "lore_entry":
            raw_years = document.pop("season_ids", document.get("applicable_story_years"))
            document["timeline_id"] = timeline_id
            document["applicable_story_years"] = raw_years if isinstance(raw_years, list) else None
        else:
            document.pop("season_id", None)
            document["timeline_id"] = timeline_id


def _migrate_point_id(point_id: str, record_kind: str, timeline_id: str, story_year: int | None) -> str:
    """把开发 artifact 中带旧 season 片段的来源 ID 改为新时间语义。"""

    parts = point_id.split(":")
    if len(parts) < 4 or not parts[2].startswith("s") or not parts[2][1:].isdigit():
        return point_id
    remainder = parts[3:]
    if record_kind in {"story_event", "lore_entry"}:
        year_label = story_year if story_year is not None else "unknown"
        return ":".join([parts[0], timeline_id, f"y{year_label}", *remainder])
    return ":".join([parts[0], timeline_id, *remainder])
