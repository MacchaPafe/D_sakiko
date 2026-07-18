"""把跨集关系聚合结果组装为 Relation Type 级审核序列。"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from .review_migration import blocked_removed_ids, canonical_json_sha256, safely_write_json_model, write_migration_report
from .review_models import (
    MigrationCounts,
    ReviewMigrationReport,
    SourceFingerprint,
    copy_completed_review,
)
from .schemas import (
    CharacterRelationStateReviewRecord,
    RelationObservationReviewRecord,
    Stage3RelationAggregationArtifact,
)
from .stage3_relation_models import (
    RelationStateDraft,
    RelationTypeReviewRecord,
    Stage3RelationReviewArtifact,
    UnmergedRelationObservationDecision,
)


def _state_draft(record: CharacterRelationStateReviewRecord) -> RelationStateDraft | None:
    """把旧单 State 记录转换为新序列中的状态草稿。"""

    document = record.document
    if document is None or record.validation_errors:
        return None
    summary = document.summary.strip()
    retrieval_text = summary
    if document.speech_hint.strip():
        retrieval_text = f"{summary} 说话方式：{document.speech_hint.strip()}"
    return RelationStateDraft(
        relation_state_id=f"relation_state:{uuid4()}",
        supporting_observation_ids=sorted(set(record.supporting_observation_ids)),
        state_summary=summary,
        speech_hint=document.speech_hint.strip(),
        object_character_nickname=document.object_character_nickname.strip(),
        visible_from=document.visible_from,
        visible_to=document.visible_to,
        retrieval_text=retrieval_text,
    )


def _sequence_basis(
    record: RelationTypeReviewRecord,
    observations_by_id: dict[str, RelationObservationReviewRecord],
) -> str:
    """计算 Relation Type 的证据与机器序列审核摘要。"""

    observation_payloads: list[object] = []
    for observation_id in sorted(set(record.covered_observation_ids)):
        observation = observations_by_id[observation_id]
        observation_payloads.append(observation.model_dump(mode="json"))
    sequence_payload = [
        {
            "supporting_observation_ids": sorted(set(state.supporting_observation_ids)),
            "state_summary": state.state_summary,
            "speech_hint": state.speech_hint,
            "object_character_nickname": state.object_character_nickname,
            "visible_from": state.visible_from,
            "visible_to": state.visible_to,
            "tags": state.tags,
            "retrieval_text": state.retrieval_text,
        }
        for state in record.generated_sequence
    ]
    return canonical_json_sha256(
        {
            "subject_character_id": record.subject_character_id,
            "object_character_id": record.object_character_id,
            "semantic_label": record.semantic_label,
            "observations": observation_payloads,
            "generated_sequence": sequence_payload,
        }
    )


def _unmerged_basis(observation: BaseModel, reason: str) -> str:
    """计算未归并 Observation 的审核基础摘要。"""

    return canonical_json_sha256(
        {"observation": observation.model_dump(mode="json"), "generated_reason": reason}
    )


def _inherit_state_ids(
    current: list[RelationStateDraft],
    previous: list[RelationStateDraft],
) -> None:
    """按完全相同的证据集合继承 Relation State 开发侧身份。"""

    previous_by_evidence = {
        tuple(sorted(set(state.supporting_observation_ids))): state
        for state in previous
    }
    for state in current:
        old = previous_by_evidence.get(tuple(sorted(set(state.supporting_observation_ids))))
        if old is not None:
            state.relation_state_id = old.relation_state_id


def build_stage3_relation_review_artifact(
    legacy: Stage3RelationAggregationArtifact,
    direct_sources: list[SourceFingerprint],
    previous: Stage3RelationReviewArtifact | None = None,
    output_path: str = "",
    allowed_removed_ids: set[str] | None = None,
    allow_all_removed: bool = False,
) -> tuple[Stage3RelationReviewArtifact, ReviewMigrationReport]:
    """从现有聚合内部结果构建 Relation Type 级审核产物。"""

    observations_by_id = {item.observation_id: item for item in legacy.observations}
    grouped: dict[tuple[str, str, str], list[CharacterRelationStateReviewRecord]] = defaultdict(list)
    for state in legacy.character_relation_states:
        if state.document is None:
            continue
        document = state.document
        grouped[
            (
                document.subject_character_id.value,
                document.object_character_id.value,
                document.relation_type_key,
            )
        ].append(state)
    relation_types: list[RelationTypeReviewRecord] = []
    for key, records in sorted(grouped.items()):
        ordered_records = sorted(
            records,
            key=lambda item: item.document.visible_from if item.document is not None else 0,
        )
        sequence = [draft for record in ordered_records if (draft := _state_draft(record)) is not None]
        if not sequence:
            continue
        first_document = ordered_records[0].document
        assert first_document is not None
        covered = sorted(
            {
                observation_id
                for state in sequence
                for observation_id in state.supporting_observation_ids
            }
        )
        risk_level = "high" if any(record.risk_level == "high" for record in ordered_records) else "low"
        risk_reasons = sorted({reason for record in ordered_records for reason in record.risk_reasons})
        relation_type = RelationTypeReviewRecord(
            relation_type_id=f"relation_type:{uuid4()}",
            subject_character_id=first_document.subject_character_id,
            object_character_id=first_document.object_character_id,
            series_id=legacy.metadata.series_id,
            timeline_id=legacy.metadata.timeline_id,
            canon_branch=first_document.canon_branch,
            semantic_label=key[2],
            covered_observation_ids=covered,
            generated_sequence=sequence,
            risk_level=risk_level,
            risk_reasons=risk_reasons,
            review_basis_sha256="0" * 64,
        )
        relation_type.review_basis_sha256 = _sequence_basis(relation_type, observations_by_id)
        relation_types.append(relation_type)
    unmerged = [
        UnmergedRelationObservationDecision(
            observation_id=item.observation_id,
            generated_reason=item.reason,
            suggested_disposition="exclude",
            review_basis_sha256=_unmerged_basis(observations_by_id[item.observation_id], item.reason),
            risk_level=item.risk_level,
            risk_reasons=item.risk_reasons,
        )
        for item in legacy.unmerged_observations
        if item.observation_id in observations_by_id
    ]
    report = ReviewMigrationReport(
        artifact_type="stage3_relation_review",
        output_path=output_path,
        succeeded=True,
    )
    previous_type_by_key: dict[tuple[str, str, str], RelationTypeReviewRecord] = {}
    previous_unmerged_by_id: dict[str, UnmergedRelationObservationDecision] = {}
    if previous is not None:
        if (
            previous.metadata.series_id != legacy.metadata.series_id
            or previous.metadata.timeline_id != legacy.metadata.timeline_id
            or previous.metadata.canon_branch != legacy.metadata.canon_branch
            or previous.metadata.episodes != legacy.metadata.episodes
        ):
            raise ValueError("previous Relation Review 的范围与当前全量输入不兼容")
        previous_type_by_key = {
            (
                item.subject_character_id.value,
                item.object_character_id.value,
                item.semantic_label,
            ): item
            for item in previous.relation_types
        }
        previous_unmerged_by_id = {item.observation_id: item for item in previous.unmerged_observations}
    matched_ids: set[str] = set()
    disappeared_state_ids: set[str] = set()
    for item in relation_types:
        old = previous_type_by_key.get(
            (
                item.subject_character_id.value,
                item.object_character_id.value,
                item.semantic_label,
            )
        )
        if old is None:
            report.added_ids.append(item.relation_type_id)
            continue
        item.relation_type_id = old.relation_type_id
        item.previous_review_reference = old.relation_type_id
        _inherit_state_ids(item.generated_sequence, old.generated_sequence)
        current_state_ids = {state.relation_state_id for state in item.generated_sequence}
        disappeared_state_ids.update(
            state.relation_state_id
            for state in old.effective_sequence()
            if state.relation_state_id not in current_state_ids
        )
        item.review_basis_sha256 = _sequence_basis(item, observations_by_id)
        matched_ids.add(old.relation_type_id)
        if item.review_basis_sha256 == old.review_basis_sha256:
            copy_completed_review(item, old)
            item.reviewed_sequence = old.reviewed_sequence
            report.migrated_ids.append(item.relation_type_id)
        else:
            item.previous_reviewed_sequence = old.reviewed_sequence
            report.reset_ids.append(item.relation_type_id)
    for item in unmerged:
        old = previous_unmerged_by_id.get(item.observation_id)
        if old is None:
            continue
        if item.review_basis_sha256 == old.review_basis_sha256:
            copy_completed_review(item, old)
            report.migrated_ids.append(item.observation_id)
        else:
            report.reset_ids.append(item.observation_id)
    if previous is not None:
        report.disappeared_ids = sorted(
            {
                item.relation_type_id
                for item in previous.relation_types
                if item.relation_type_id not in matched_ids
            }
            | disappeared_state_ids
        )
    report.counts = MigrationCounts(
        old_candidates=0 if previous is None else len(previous.relation_types),
        new_candidates=len(relation_types),
        migrated_reviews=len(report.migrated_ids),
        reset_reviews=len(report.reset_ids),
        added_candidates=len(report.added_ids),
        disappeared_candidates=len(report.disappeared_ids),
        pending_identities=len(report.pending_identity_ids),
    )
    artifact = Stage3RelationReviewArtifact(
        metadata=legacy.metadata,
        direct_sources=direct_sources,
        aggregation_model=legacy.aggregation_model,
        observations=legacy.observations,
        relation_types=relation_types,
        unmerged_observations=unmerged,
        issues=legacy.issues,
    )
    if previous is not None:
        completed_publish_types = {
            item.relation_type_id: item
            for item in previous.relation_types
            if item.review_status == "completed" and item.disposition == "publish"
        }
        protected_ids = set(completed_publish_types)
        for item in completed_publish_types.values():
            protected_ids.update(state.relation_state_id for state in item.effective_sequence())
        effective_allowed = set(allowed_removed_ids or set())
        for relation_type_id in effective_allowed & completed_publish_types.keys():
            effective_allowed.update(
                state.relation_state_id
                for state in completed_publish_types[relation_type_id].effective_sequence()
            )
        report.blocked_removed_ids = blocked_removed_ids(
            protected_ids,
            set(report.disappeared_ids),
            effective_allowed,
            allow_all_removed,
        )
        if report.blocked_removed_ids:
            report.succeeded = False
            report.blocked_reason = "已发布 Relation Type/State 消失但未取得本次删除许可"
            if output_path:
                write_migration_report(report, output_path)
            raise ValueError(report.blocked_reason)
    return artifact, report


def load_stage3_relation_review_artifact(path: str | Path) -> Stage3RelationReviewArtifact:
    """读取严格的全量 Relation Review。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return Stage3RelationReviewArtifact.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("输入不是新版全量 Relation Review") from exc


def save_stage3_relation_review_artifact(
    artifact: Stage3RelationReviewArtifact,
    report: ReviewMigrationReport,
    output_path: str | Path,
) -> None:
    """安全保存 Relation Review 及本次迁移报告。"""

    safely_write_json_model(artifact, output_path)
    write_migration_report(report, output_path)
