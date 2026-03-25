"""第三阶段：将第二阶段原始结果规范化并导入 RAG 数据库。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rag.models import (
    CanonBranch,
    CharacterId,
    CharacterRelationDocument,
    CollectionName,
    LoreEntryDocument,
    ScopeType,
    SeasonId,
    SeriesId,
    StoryEventDocument,
)

from .characters import build_character_catalog
from .schemas import (
    CharacterRelationImportRecord,
    CharacterRelationPayload,
    LoreEntryImportRecord,
    LoreEntryPayload,
    NormalizationIssue,
    SceneAnnotationPass2,
    Stage2AnnotationArtifact,
    Stage2InputArtifact,
    Stage2SceneInput,
    Stage3ImportMetadata,
    Stage3NormalizedImportArtifact,
    StoryEventImportRecord,
    StoryEventPayload,
)


EVENT_BASE_MAP: dict[SeriesId, int] = {
    SeriesId.BANG_DREAM_1: 1000,
    SeriesId.BANG_DREAM_2: 2000,
    SeriesId.BANG_DREAM_3: 3000,
    SeriesId.ITS_MYGO: 4000,
    SeriesId.AVE_MUJICA: 5000
}

EPISODE_EVENT_SLOT_SIZE = 50
RELATION_VISIBLE_TO_DEFAULT = 999999
STORY_EVENT_VISIBLE_TO_DEFAULT = 999999

_CHARACTER_ALIAS_MAP: dict[str, CharacterId] | None = None


def _normalize_lookup_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _clean_text_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        cleaned = _clean_text(str(raw))
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _build_character_alias_map() -> dict[str, CharacterId]:
    alias_map: dict[str, CharacterId] = {}
    for candidate in build_character_catalog():
        try:
            character_id = CharacterId(candidate.character_id)
        except ValueError:
            continue
        for alias in [candidate.display_name, candidate.character_id, *candidate.aliases]:
            alias_map[_normalize_lookup_key(alias)] = character_id
    for character_id in CharacterId:
        alias_map[_normalize_lookup_key(character_id.value)] = character_id
    return alias_map


def resolve_character_id(name: str) -> CharacterId:
    """将角色名称或别名解析为 CharacterId。"""

    global _CHARACTER_ALIAS_MAP
    if _CHARACTER_ALIAS_MAP is None:
        _CHARACTER_ALIAS_MAP = _build_character_alias_map()

    lookup_key = _normalize_lookup_key(name)
    character_id = _CHARACTER_ALIAS_MAP.get(lookup_key)
    if character_id is None:
        raise ValueError(f"无法将角色名映射到 CharacterId: {name!r}")
    return character_id


def _normalize_lore_title(title: str) -> str:
    return _clean_text(title)


def _story_event_episode_base(episode: int, series_id: SeriesId) -> int:
    try:
        event_base = EVENT_BASE_MAP[series_id]
    except KeyError:
        raise ValueError(f"未找到 {series_id} 对应的事件开始值。这不应当发生。请检查你传入的是否是 SeriesId 枚举类的对象。当前对象的类型为：{type(series_id)}")
  
    return event_base + (episode - 1) * EPISODE_EVENT_SLOT_SIZE


def _build_scene_anchor_map(
    input_artifact: Stage2InputArtifact,
    annotation_artifact: Stage2AnnotationArtifact,
    issues: list[NormalizationIssue],
) -> dict[str, int]:
    base = _story_event_episode_base(
        episode=input_artifact.metadata.episode,
        series_id=SeriesId(input_artifact.metadata.series_id),
    )
    cursor = base
    anchors: dict[str, int] = {}
    result_map = {result.scene_id: result for result in annotation_artifact.results}

    for scene in input_artifact.scenes:
        result = result_map.get(scene.scene_id)
        annotation = None if result is None else result.annotation
        story_event_count = 0 if annotation is None else len(annotation.story_events)
        anchors[scene.scene_id] = cursor
        cursor += max(1, story_event_count)

    if cursor > base + EPISODE_EVENT_SLOT_SIZE:
        issues.append(
            NormalizationIssue(
                scene_id="__episode__",
                collection_name=CollectionName.STORY_EVENTS.value,
                candidate_local_id="__time_order__",
                message=(
                    f"当前集规范化后需要 {cursor - base} 个时间槽位，超过每集预留的 "
                    f"{EPISODE_EVENT_SLOT_SIZE} 个。"
                ),
            )
        )
    return anchors


def build_stage3_normalized_import_artifact(
    input_artifact: Stage2InputArtifact,
    annotation_artifact: Stage2AnnotationArtifact,
) -> Stage3NormalizedImportArtifact:
    """将第二阶段原始结果规范化为待入库 artifact。"""

    issues: list[NormalizationIssue] = []
    scene_map = {scene.scene_id: scene for scene in input_artifact.scenes}
    scene_anchor_map = _build_scene_anchor_map(input_artifact, annotation_artifact, issues)

    story_events: list[StoryEventImportRecord] = []
    character_relations: list[CharacterRelationImportRecord] = []
    lore_entries: list[LoreEntryImportRecord] = []

    metadata = Stage3ImportMetadata(
        subtitle_path=input_artifact.metadata.subtitle_path,
        anime_title=input_artifact.metadata.anime_title,
        series_id=SeriesId(input_artifact.metadata.series_id),
        season_id=SeasonId(input_artifact.metadata.season_id),
        canon_branch=CanonBranch(input_artifact.metadata.canon_branch),
        episode=input_artifact.metadata.episode,
        source_stage2_model=annotation_artifact.model,
        source_stage2_template_path=annotation_artifact.template_path,
    )

    for result in annotation_artifact.results:
        scene = scene_map.get(result.scene_id)
        if scene is None:
            issues.append(
                NormalizationIssue(
                    scene_id=result.scene_id,
                    collection_name="scene",
                    candidate_local_id="__scene__",
                    message="stage2_input 中不存在该 scene_id，无法规范化。",
                )
            )
            continue
        if result.annotation is None:
            issues.append(
                NormalizationIssue(
                    scene_id=result.scene_id,
                    collection_name="scene",
                    candidate_local_id="__scene__",
                    message=result.error or "第二阶段没有 annotation，无法规范化。",
                )
            )
            continue

        _normalize_scene_story_events(
            scene=scene,
            annotation=result.annotation,
            metadata=metadata,
            scene_anchor=scene_anchor_map[result.scene_id],
            issues=issues,
            sink=story_events,
        )
        _normalize_scene_character_relations(
            scene=scene,
            annotation=result.annotation,
            metadata=metadata,
            scene_anchor=scene_anchor_map[result.scene_id],
            issues=issues,
            sink=character_relations,
        )
        _normalize_scene_lore_entries(
            scene=scene,
            annotation=result.annotation,
            metadata=metadata,
            issues=issues,
            sink=lore_entries,
        )

    _backfill_character_relation_visible_to(character_relations, issues)

    return Stage3NormalizedImportArtifact(
        metadata=metadata,
        story_events=story_events,
        character_relations=character_relations,
        lore_entries=lore_entries,
        issues=issues,
    )


def _normalize_scene_story_events(
    scene: Stage2SceneInput,
    annotation: SceneAnnotationPass2,
    metadata: Stage3ImportMetadata,
    scene_anchor: int,
    issues: list[NormalizationIssue],
    sink: list[StoryEventImportRecord],
) -> None:
    utterance_ids = {item.u_id for item in scene.utterances}
    screen_text_ids = {item.s_id for item in scene.screen_texts}

    for index, candidate in enumerate(annotation.story_events):
        valid_u_ids, invalid_u_ids = _split_existing_ids(candidate.evidence_u_ids, utterance_ids)
        valid_s_ids, invalid_s_ids = _split_existing_ids(candidate.evidence_s_ids, screen_text_ids)
        _append_invalid_evidence_issues(
            issues=issues,
            scene_id=scene.scene_id,
            collection_name=CollectionName.STORY_EVENTS.value,
            candidate_local_id=candidate.event_local_id,
            invalid_u_ids=invalid_u_ids,
            invalid_s_ids=invalid_s_ids,
        )

        participants: list[CharacterId] = []
        for name in candidate.participants:
            try:
                participants.append(resolve_character_id(name))
            except ValueError as exc:
                issues.append(
                    NormalizationIssue(
                        scene_id=scene.scene_id,
                        collection_name=CollectionName.STORY_EVENTS.value,
                        candidate_local_id=candidate.event_local_id,
                        message=str(exc),
                    )
                )

        try:
            document = StoryEventDocument(
                season_id=metadata.season_id,
                series_id=metadata.series_id,
                episode=metadata.episode,
                time_order=scene_anchor + index,
                visible_from=scene_anchor + index,
                visible_to=STORY_EVENT_VISIBLE_TO_DEFAULT,
                canon_branch=metadata.canon_branch,
                title=_clean_text(candidate.title),
                summary=_clean_text(candidate.summary),
                participants=participants,
                importance=int(candidate.importance),
                tags=_clean_text_list(candidate.tags),
                retrieval_text=_clean_text(candidate.retrieval_text),
            )
        except Exception as exc:
            issues.append(
                NormalizationIssue(
                    scene_id=scene.scene_id,
                    collection_name=CollectionName.STORY_EVENTS.value,
                    candidate_local_id=candidate.event_local_id,
                    message=f"无法规范化为 StoryEventDocument: {type(exc).__name__}: {exc}",
                )
            )
            continue

        sink.append(
            StoryEventImportRecord(
                point_id=(
                    f"story_event:{metadata.series_id.value}:s{metadata.season_id.value}:"
                    f"{scene.scene_id}:{candidate.event_local_id}:{document.time_order}"
                ),
                source_scene_id=scene.scene_id,
                source_local_id=candidate.event_local_id,
                evidence_u_ids=valid_u_ids,
                evidence_s_ids=valid_s_ids,
                confidence=candidate.confidence,
                document=StoryEventPayload.model_validate(document.to_payload()),
            )
        )


def _normalize_scene_character_relations(
    scene: Stage2SceneInput,
    annotation: SceneAnnotationPass2,
    metadata: Stage3ImportMetadata,
    scene_anchor: int,
    issues: list[NormalizationIssue],
    sink: list[CharacterRelationImportRecord],
) -> None:
    utterance_ids = {item.u_id for item in scene.utterances}

    for candidate in annotation.character_relations:
        valid_u_ids, invalid_u_ids = _split_existing_ids(candidate.evidence_u_ids, utterance_ids)
        if invalid_u_ids:
            issues.append(
                NormalizationIssue(
                    scene_id=scene.scene_id,
                    collection_name=CollectionName.CHARACTER_RELATIONS.value,
                    candidate_local_id=candidate.relation_local_id,
                    message=f"发现不存在的 evidence_u_ids: {', '.join(invalid_u_ids)}",
                )
            )

        try:
            subject_character_id = resolve_character_id(candidate.subject_character_name)
            object_character_id = resolve_character_id(candidate.object_character_name)
            document = CharacterRelationDocument(
                subject_character_id=subject_character_id,
                object_character_id=object_character_id,
                season_id=metadata.season_id,
                series_id=metadata.series_id,
                visible_from=scene_anchor,
                visible_to=RELATION_VISIBLE_TO_DEFAULT,
                canon_branch=metadata.canon_branch,
                relation_label=_clean_text(candidate.relation_label),
                state_summary=_clean_text(candidate.state_summary),
                speech_hint=_clean_text(candidate.speech_hint),
                object_character_nickname=_clean_text(candidate.object_character_nickname),
                tags=_clean_text_list(candidate.tags),
                retrieval_text=_clean_text(candidate.retrieval_text),
            )
        except Exception as exc:
            issues.append(
                NormalizationIssue(
                    scene_id=scene.scene_id,
                    collection_name=CollectionName.CHARACTER_RELATIONS.value,
                    candidate_local_id=candidate.relation_local_id,
                    message=f"无法规范化为 CharacterRelationDocument: {type(exc).__name__}: {exc}",
                )
            )
            continue

        sink.append(
            CharacterRelationImportRecord(
                point_id=(
                    f"character_relation:{metadata.series_id.value}:s{metadata.season_id.value}:"
                    f"{scene.scene_id}:{candidate.relation_local_id}:"
                    f"{document.subject_character_id.value}:{document.object_character_id.value}"
                ),
                source_scene_id=scene.scene_id,
                source_local_id=candidate.relation_local_id,
                evidence_u_ids=valid_u_ids,
                confidence=candidate.confidence,
                document=CharacterRelationPayload.model_validate(document.to_payload()),
            )
        )


def _backfill_character_relation_visible_to(
    records: list[CharacterRelationImportRecord],
    issues: list[NormalizationIssue],
) -> None:
    grouped_records: dict[tuple[CharacterId, CharacterId], list[CharacterRelationImportRecord]] = {}
    for record in records:
        key = (record.document.subject_character_id, record.document.object_character_id)
        grouped_records.setdefault(key, []).append(record)

    for (subject_character_id, object_character_id), group in grouped_records.items():
        ordered_group = sorted(
            group,
            key=lambda item: (
                item.document.visible_from,
                item.source_scene_id,
                item.source_local_id,
            ),
        )
        for current_record, next_record in zip(ordered_group, ordered_group[1:]):
            if next_record.document.visible_from <= current_record.document.visible_from:
                issues.append(
                    NormalizationIssue(
                        scene_id=current_record.source_scene_id,
                        collection_name=CollectionName.CHARACTER_RELATIONS.value,
                        candidate_local_id=current_record.source_local_id,
                        message=(
                            "无法回填 visible_to，因为下一条同主客体关系的 visible_from "
                            f"({next_record.document.visible_from}) 不大于当前 visible_from "
                            f"({current_record.document.visible_from})。"
                        ),
                    )
                )
                continue
            current_record.document.visible_to = next_record.document.visible_from - 1


def _normalize_scene_lore_entries(
    scene: Stage2SceneInput,
    annotation: SceneAnnotationPass2,
    metadata: Stage3ImportMetadata,
    issues: list[NormalizationIssue],
    sink: list[LoreEntryImportRecord],
) -> None:
    utterance_ids = {item.u_id for item in scene.utterances}
    screen_text_ids = {item.s_id for item in scene.screen_texts}

    for candidate in annotation.lore_entries:
        valid_u_ids, invalid_u_ids = _split_existing_ids(candidate.evidence_u_ids, utterance_ids)
        valid_s_ids, invalid_s_ids = _split_existing_ids(candidate.evidence_s_ids, screen_text_ids)
        _append_invalid_evidence_issues(
            issues=issues,
            scene_id=scene.scene_id,
            collection_name=CollectionName.LORE_ENTRIES.value,
            candidate_local_id=candidate.lore_local_id,
            invalid_u_ids=invalid_u_ids,
            invalid_s_ids=invalid_s_ids,
        )

        try:
            document = LoreEntryDocument(
                scope_type=ScopeType.SERIES,
                series_ids=[metadata.series_id],
                season_ids=[metadata.season_id],
                visible_from=None,
                visible_to=None,
                canon_branch=metadata.canon_branch,
                title=_normalize_lore_title(candidate.title),
                content=_clean_text(candidate.content),
                retrieval_text=_clean_text(candidate.retrieval_text),
                tags=_clean_text_list(candidate.tags),
            )
        except Exception as exc:
            issues.append(
                NormalizationIssue(
                    scene_id=scene.scene_id,
                    collection_name=CollectionName.LORE_ENTRIES.value,
                    candidate_local_id=candidate.lore_local_id,
                    message=f"无法规范化为 LoreEntryDocument: {type(exc).__name__}: {exc}",
                )
            )
            continue

        sink.append(
            LoreEntryImportRecord(
                point_id=(
                    f"lore_entry:{metadata.series_id.value}:s{metadata.season_id.value}:"
                    f"{scene.scene_id}:{candidate.lore_local_id}:{document.title}"
                ),
                source_scene_id=scene.scene_id,
                source_local_id=candidate.lore_local_id,
                evidence_u_ids=valid_u_ids,
                evidence_s_ids=valid_s_ids,
                confidence=candidate.confidence,
                document=LoreEntryPayload.model_validate(document.to_payload()),
            )
        )


def _split_existing_ids(values: list[str], existing_ids: set[str]) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    invalid: list[str] = []
    for value in values:
        if value in existing_ids:
            valid.append(value)
        else:
            invalid.append(value)
    return valid, invalid


def _append_invalid_evidence_issues(
    issues: list[NormalizationIssue],
    scene_id: str,
    collection_name: str,
    candidate_local_id: str,
    invalid_u_ids: list[str],
    invalid_s_ids: list[str],
) -> None:
    if invalid_u_ids:
        issues.append(
            NormalizationIssue(
                scene_id=scene_id,
                collection_name=collection_name,
                candidate_local_id=candidate_local_id,
                message=f"发现不存在的 evidence_u_ids: {', '.join(invalid_u_ids)}",
            )
        )
    if invalid_s_ids:
        issues.append(
            NormalizationIssue(
                scene_id=scene_id,
                collection_name=collection_name,
                candidate_local_id=candidate_local_id,
                message=f"发现不存在的 evidence_s_ids: {', '.join(invalid_s_ids)}",
            )
        )


def save_stage3_normalized_import_artifact(
    artifact: Stage3NormalizedImportArtifact,
    output_path: str | Path,
) -> None:
    """保存待入库 artifact。"""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_stage3_normalized_import_artifact(input_path: str | Path) -> Stage3NormalizedImportArtifact:
    """读取待入库 artifact。"""

    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return Stage3NormalizedImportArtifact.model_validate(payload)


def backfill_existing_stage3_character_relations(
    artifact: Stage3NormalizedImportArtifact,
) -> Stage3NormalizedImportArtifact:
    """对已有 stage3 artifact 的角色关系时间窗口做原地回填。"""

    _backfill_character_relation_visible_to(artifact.character_relations, artifact.issues)
    return artifact


def load_stage3_artifacts_from_directory(input_dir: str | Path) -> list[tuple[Path, Stage3NormalizedImportArtifact]]:
    """从目录中读取全部 stage3 artifact。"""

    input_dir = Path(input_dir)
    artifacts: list[tuple[Path, Stage3NormalizedImportArtifact]] = []
    for path in sorted(input_dir.glob("*.json")):
        artifacts.append((path, load_stage3_normalized_import_artifact(path)))
    return artifacts


def backfill_stage3_character_relations_across_artifacts(
    artifacts: list[tuple[Path, Stage3NormalizedImportArtifact]],
) -> list[tuple[Path, Stage3NormalizedImportArtifact]]:
    """跨多个 stage3 artifact 回填角色关系时间窗口。"""

    all_records: list[CharacterRelationImportRecord] = []
    for _, artifact in artifacts:
        all_records.extend(artifact.character_relations)

    grouped_records: dict[tuple[CharacterId, CharacterId], list[CharacterRelationImportRecord]] = {}
    for record in all_records:
        key = (record.document.subject_character_id, record.document.object_character_id)
        grouped_records.setdefault(key, []).append(record)

    for group in grouped_records.values():
        ordered_group = sorted(
            group,
            key=lambda item: (
                item.document.visible_from,
                item.document.season_id.value,
                item.source_scene_id,
                item.source_local_id,
            ),
        )
        for current_record, next_record in zip(ordered_group, ordered_group[1:]):
            if next_record.document.visible_from <= current_record.document.visible_from:
                continue
            current_record.document.visible_to = next_record.document.visible_from - 1

    return artifacts


def save_stage3_artifacts_to_directory(
    artifacts: list[tuple[Path, Stage3NormalizedImportArtifact]],
    output_dir: str | Path,
) -> None:
    """将一组 stage3 artifact 写入目录。"""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for source_path, artifact in artifacts:
        save_stage3_normalized_import_artifact(artifact, output_dir / source_path.name)


def upsert_stage3_normalized_import_artifact(
    artifact: Stage3NormalizedImportArtifact,
    service: Any,
) -> dict[str, Any]:
    """将待入库 artifact 写入数据库。"""

    from rag.services import PointRecord

    service.ensure_collections_exist()
    story_report = service.upsert_story_events(
        [
            PointRecord(
                point_id=record.point_id,
                document=StoryEventDocument(**record.document.model_dump()),
            )
            for record in artifact.story_events
        ]
    )
    relation_report = service.upsert_character_relations(
        [
            PointRecord(
                point_id=record.point_id,
                document=CharacterRelationDocument(**record.document.model_dump()),
            )
            for record in artifact.character_relations
        ]
    )
    lore_report = service.upsert_lore_entries(
        [
            PointRecord(
                point_id=record.point_id,
                document=LoreEntryDocument(**record.document.model_dump()),
            )
            for record in artifact.lore_entries
        ]
    )
    return {
        CollectionName.STORY_EVENTS.value: story_report,
        CollectionName.CHARACTER_RELATIONS.value: relation_report,
        CollectionName.LORE_ENTRIES.value: lore_report,
    }


def create_rag_service(
    qdrant_location: str,
    qdrant_connect_type: str,
    embedding_model_path: str,
) -> Any:
    """按给定配置创建 RagService 实例。"""

    from rag.services import QdrantConnectType, QdrantRagService, RagServiceConfig

    service = QdrantRagService(
        RagServiceConfig(
            qdrant_location=qdrant_location,
            qdrant_connect_type=QdrantConnectType(qdrant_connect_type),
            embedding_model_path=embedding_model_path,
        )
    )
    return service
