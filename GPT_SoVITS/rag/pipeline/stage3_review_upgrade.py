"""一次性把旧 Story/Lore RAG 与 point ID map 升级到审核 Schema。"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from rag.worldbook.build_models import IdentityMapRecord, WorldbookIdentityMap
from rag.worldbook.identity_map import load_identity_map, save_identity_map

from .review_migration import build_source_fingerprint, canonical_json_sha256, safely_write_json_model
from .schemas import Stage3NormalizedImportArtifact
from .stage3_document_models import LoreEntryReviewRecord, Stage3DocumentReviewArtifact, StoryEventReviewRecord


def upgrade_stage3_review_schema(
    package_id: str,
    artifact_paths: list[str | Path],
    stage2_input_paths: list[str | Path],
    annotation_paths: list[str | Path],
    old_id_map_path: str | Path,
    new_id_map_path: str | Path,
    apply: bool = False,
) -> tuple[list[Stage3DocumentReviewArtifact], WorldbookIdentityMap]:
    """转换旧产物并把已有正式 UUID 迁移到新 candidate identity。"""

    if not (
        len(artifact_paths) == len(stage2_input_paths) == len(annotation_paths)
    ):
        raise ValueError("artifact、stage2 input 与 annotation 数量必须一致")
    old_id_map = _load_legacy_id_map(Path(old_id_map_path))
    new_map_path = Path(new_id_map_path)
    try:
        new_map = load_identity_map(new_map_path, package_id)
    except ValueError:
        if new_map_path.resolve() != Path(old_id_map_path).resolve():
            raise
        new_map = WorldbookIdentityMap(package_id=package_id)
    converted: list[Stage3DocumentReviewArtifact] = []
    for artifact_path, stage2_path, annotation_path in zip(
        artifact_paths,
        stage2_input_paths,
        annotation_paths,
    ):
        raw_text = Path(artifact_path).read_text(encoding="utf-8")
        try:
            current = Stage3DocumentReviewArtifact.model_validate_json(raw_text)
        except ValidationError:
            try:
                legacy = Stage3NormalizedImportArtifact.model_validate_json(raw_text)
            except ValidationError as exc:
                raise ValueError(f"无法识别待升级 Stage 3 artifact: {artifact_path}") from exc
            current = _convert_legacy_artifact(legacy, Path(stage2_path), Path(annotation_path))
        _migrate_existing_entry_ids(current, old_id_map, new_map)
        converted.append(current)
    if apply:
        for path, artifact in zip(artifact_paths, converted):
            safely_write_json_model(artifact, path)
        save_identity_map(new_map_path, new_map)
    return converted, new_map


def _convert_legacy_artifact(
    legacy: Stage3NormalizedImportArtifact,
    stage2_input_path: Path,
    annotation_path: Path,
) -> Stage3DocumentReviewArtifact:
    """把一份旧三表 artifact 投影为未审核 Story/Lore Review。"""

    episode = legacy.metadata.episode
    story_records = [
        StoryEventReviewRecord(
            candidate_id=f"story_candidate:{uuid4()}",
            legacy_source_id=record.point_id,
            source_scene_id=record.source_scene_id,
            source_local_id=record.source_local_id,
            evidence_u_ids=sorted(set(record.evidence_u_ids)),
            evidence_s_ids=sorted(set(record.evidence_s_ids)),
            confidence=record.confidence,
            generated_document=record.document,
            review_basis_sha256=canonical_json_sha256(
                {
                    "source_scene_id": record.source_scene_id,
                    "source_local_id": record.source_local_id,
                    "evidence_u_ids": sorted(set(record.evidence_u_ids)),
                    "evidence_s_ids": sorted(set(record.evidence_s_ids)),
                    "generated_document": record.document.model_dump(mode="json"),
                }
            ),
        )
        for record in legacy.story_events
    ]
    lore_records = [
        LoreEntryReviewRecord(
            candidate_id=f"lore_candidate:{uuid4()}",
            legacy_source_id=record.point_id,
            source_scene_id=record.source_scene_id,
            source_local_id=record.source_local_id,
            evidence_u_ids=sorted(set(record.evidence_u_ids)),
            evidence_s_ids=sorted(set(record.evidence_s_ids)),
            confidence=record.confidence,
            generated_document=record.document,
            review_basis_sha256=canonical_json_sha256(
                {
                    "source_scene_id": record.source_scene_id,
                    "source_local_id": record.source_local_id,
                    "evidence_u_ids": sorted(set(record.evidence_u_ids)),
                    "evidence_s_ids": sorted(set(record.evidence_s_ids)),
                    "generated_document": record.document.model_dump(mode="json"),
                }
            ),
        )
        for record in legacy.lore_entries
    ]
    return Stage3DocumentReviewArtifact(
        metadata=legacy.metadata,
        direct_sources=[
            build_source_fingerprint("stage2_input", stage2_input_path, episode),
            build_source_fingerprint("stage2a_annotation", annotation_path, episode),
        ],
        story_events=story_records,
        lore_entries=lore_records,
        issues=legacy.issues,
    )


def _load_legacy_id_map(path: Path) -> dict[str, UUID]:
    """读取旧 point_id 到正式 UUID 的扁平映射。"""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("旧 ID map 顶层必须是对象")
    result: dict[str, UUID] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            try:
                result[str(key)] = UUID(value)
            except ValueError:
                continue
    return result


def _migrate_existing_entry_ids(
    artifact: Stage3DocumentReviewArtifact,
    legacy_map: dict[str, UUID],
    new_map: WorldbookIdentityMap,
) -> None:
    """把 legacy_source_id 已有正式 UUID 绑定到新的 candidate ID。"""

    for record in [*artifact.story_events, *artifact.lore_entries]:
        if record.legacy_source_id is None:
            continue
        existing_uuid = legacy_map.get(record.legacy_source_id)
        if existing_uuid is None:
            continue
        current = new_map.identities.get(record.candidate_id)
        if current is not None and current.entry_id != existing_uuid:
            raise ValueError(f"candidate ID 已映射到不同 UUID: {record.candidate_id}")
        owner = next(
            (
                identity_key
                for identity_key, mapped in new_map.identities.items()
                if mapped.entry_id == existing_uuid and identity_key != record.candidate_id
            ),
            None,
        )
        if owner is not None:
            raise ValueError(f"旧 UUID 同时对应多个新身份: {owner}, {record.candidate_id}")
        new_map.identities[record.candidate_id] = IdentityMapRecord(entry_id=existing_uuid)
