"""按角色跨集聚合 Character Thought Update 为长期 Thread。"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
from typing import Callable
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError

from .llm_client import LiteLLMConfig, LiteLLMJsonClient
from .prompt_package import (
    PreparedPrompt,
    PromptPackageError,
    PromptPackageManifest,
    create_prompt_package,
    load_prompt_package_manifest,
    parse_json_response,
    read_prompt_package_responses,
)
from .review_migration import blocked_removed_ids, canonical_json_sha256, safely_write_json_model, write_migration_report
from .review_models import (
    MigrationCounts,
    ReviewMigrationReport,
    SourceFingerprint,
    copy_completed_review,
)
from .schemas import (
    LoreEntryImportRecord,
    NormalizationIssue,
    Stage2BAnnotationArtifact,
    Stage2InputArtifact,
    Stage3NormalizedImportArtifact,
    StoryEventImportRecord,
)
from .stage3_document_models import Stage3DocumentReviewArtifact
from .stage3_thought_models import (
    CharacterThoughtAggregationResponse,
    Stage3ThoughtReviewArtifact,
    ThoughtAggregationMetadata,
    ThoughtStateDraft,
    ThoughtThreadReviewRecord,
    ThoughtTransitionDraft,
    ThoughtUpdateEvidence,
    UnassignedThoughtUpdateDecision,
)
from .stage3_thought_pipeline import _prepare_thought_pipeline


THOUGHT_VISIBLE_TO_DEFAULT = 999999
DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "thought_aggregation_pass.jinja"


def prepare_stage3_thought_prompt_package(
    input_artifacts: list[Stage2InputArtifact],
    stage2b_artifacts: list[Stage2BAnnotationArtifact],
    rag_artifacts: list[Stage3DocumentReviewArtifact],
    input_paths: list[str | Path],
    stage2b_paths: list[str | Path],
    rag_paths: list[str | Path],
    output_dir: str | Path,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
) -> PromptPackageManifest:
    """按角色渲染完整集数范围的 Thought 聚合 Prompt Package。"""

    _validate_common_scope(input_artifacts, stage2b_artifacts, rag_artifacts)
    if not (
        len(input_paths) == len(input_artifacts)
        and len(stage2b_paths) == len(stage2b_artifacts)
        and len(rag_paths) == len(rag_artifacts)
    ):
        raise ValueError("Thought Prompt Package artifact 与来源路径数量必须一致")
    updates, event_facts, _ = _collect_updates(input_artifacts, stage2b_artifacts, rag_artifacts)
    grouped: dict[str, list[ThoughtUpdateEvidence]] = defaultdict(list)
    for update in updates:
        grouped[update.character_id.value].append(update)
    story_events = _thought_prompt_story_events(rag_artifacts)
    resolved_template = Path(template_path).resolve()
    prompts = [
        PreparedPrompt(
            task_id=f"thought_character:{character_id}",
            prompt=render_thought_aggregation_prompt(
                character_id,
                character_updates,
                story_events,
                event_facts,
                resolved_template,
            ),
            context={
                "character_id": character_id,
                "update_count": str(len(character_updates)),
            },
        )
        for character_id, character_updates in sorted(grouped.items())
    ]
    return create_prompt_package(
        output_dir=output_dir,
        stage_kind="stage3_thought_aggregation",
        prompts=prompts,
        source_paths=[*input_paths, *stage2b_paths, *rag_paths],
        template_paths=[resolved_template],
        parameters={
            "input_paths": json.dumps([str(Path(path).resolve()) for path in input_paths]),
            "stage2b_paths": json.dumps([str(Path(path).resolve()) for path in stage2b_paths]),
            "rag_paths": json.dumps([str(Path(path).resolve()) for path in rag_paths]),
            "template_path": str(resolved_template),
        },
    )


def assemble_stage3_thought_prompt_package(
    manifest_path: str | Path,
    model_label: str = "codex-workspace",
    allow_partial: bool = False,
    allow_stale: bool = False,
    previous: Stage3ThoughtReviewArtifact | None = None,
    output_path: str = "",
    allowed_removed_ids: set[str] | None = None,
    allow_all_removed: bool = False,
) -> tuple[Stage3ThoughtReviewArtifact, ReviewMigrationReport]:
    """校验按角色 response 并组装唯一的全量 Thought Review。"""

    manifest = load_prompt_package_manifest(manifest_path)
    if manifest.stage_kind != "stage3_thought_aggregation":
        raise PromptPackageError("manifest 不是 Stage 3 Thought Aggregation 任务包")
    input_paths = _manifest_paths(manifest, "input_paths")
    stage2b_paths = _manifest_paths(manifest, "stage2b_paths")
    rag_paths = _manifest_paths(manifest, "rag_paths")
    from .stage2_input_builder import load_stage2_input_artifact
    from .stage2b_thought_extraction import load_stage2b_annotation_artifact
    from .stage3_document_review import load_stage3_document_review_artifact

    input_artifacts = [load_stage2_input_artifact(path) for path in input_paths]
    stage2b_artifacts = [load_stage2b_annotation_artifact(path) for path in stage2b_paths]
    rag_artifacts = [load_stage3_document_review_artifact(path) for path in rag_paths]
    _validate_common_scope(input_artifacts, stage2b_artifacts, rag_artifacts)
    updates, event_facts, issues = _collect_updates(input_artifacts, stage2b_artifacts, rag_artifacts)
    updates_by_id = {item.update_id: item for item in updates}
    grouped: dict[str, list[ThoughtUpdateEvidence]] = defaultdict(list)
    for update in updates:
        grouped[update.character_id.value].append(update)
    story_events = _thought_prompt_story_events(rag_artifacts)
    valid_event_ids = {str(item["candidate_id"]) for item in story_events}
    valid_fact_ids = {str(item.get("fact_id", "")) for item in event_facts}
    bundle = read_prompt_package_responses(
        manifest_path,
        allow_partial=allow_partial,
        allow_stale=allow_stale,
    )
    threads: list[ThoughtThreadReviewRecord] = []
    unassigned: list[UnassignedThoughtUpdateDecision] = []
    assigned_ids: set[str] = set()
    reference = input_artifacts[0].metadata
    for task in manifest.tasks:
        character_id = task.context.get("character_id", "")
        raw_response = bundle.responses.get(task.task_id)
        if character_id not in grouped or raw_response is None:
            issues.append(
                NormalizationIssue(
                    scene_id="__aggregate__",
                    collection_name="character_thoughts",
                    candidate_local_id=character_id or task.task_id,
                    message="Thought Prompt Package 缺少角色输入或 response。",
                )
            )
            continue
        try:
            response = CharacterThoughtAggregationResponse.model_validate(
                parse_json_response(raw_response)
            )
        except (ValueError, ValidationError) as exc:
            issues.append(
                NormalizationIssue(
                    scene_id="__aggregate__",
                    collection_name="character_thoughts",
                    candidate_local_id=character_id,
                    message=f"Thought 聚合 response 无效: {type(exc).__name__}: {exc}",
                )
            )
            continue
        if response.character_id.value != character_id:
            issues.append(
                NormalizationIssue(
                    scene_id="__aggregate__",
                    collection_name="character_thoughts",
                    candidate_local_id=character_id,
                    message="Thought 聚合 response 的角色与任务不一致。",
                )
            )
            continue
        character_threads, character_unassigned = _build_character_threads(
            response,
            updates_by_id,
            valid_event_ids,
            valid_fact_ids,
            assigned_ids,
            issues,
            reference.series_id,
            reference.timeline_id,
            reference.canon_branch,
        )
        threads.extend(character_threads)
        unassigned.extend(character_unassigned)
    direct_sources: list[SourceFingerprint] = []
    for input_path, stage2b_path, rag_path, input_artifact in zip(
        input_paths,
        stage2b_paths,
        rag_paths,
        input_artifacts,
    ):
        episode = input_artifact.metadata.episode
        from .review_migration import build_source_fingerprint

        direct_sources.extend(
            [
                build_source_fingerprint("stage2_input", input_path, episode),
                build_source_fingerprint("stage2b_annotation", stage2b_path, episode),
                build_source_fingerprint("stage3_rag", rag_path, episode),
            ]
        )
    return _finish_thought_artifact(
        input_artifacts,
        updates,
        event_facts,
        issues,
        threads,
        unassigned,
        assigned_ids,
        direct_sources,
        model_label,
        previous,
        output_path,
        allowed_removed_ids,
        allow_all_removed,
    )


def _thought_prompt_story_events(
    rag_artifacts: list[Stage3DocumentReviewArtifact],
) -> list[dict[str, object]]:
    """投影 Thought 聚合 Prompt 可引用的 Story Event 候选。"""

    return [
        {
            "candidate_id": record.candidate_id,
            "source_scene_id": record.source_scene_id,
            "source_local_id": record.source_local_id,
            "title": record.effective_document().title,
            "summary": record.effective_document().summary,
        }
        for artifact in rag_artifacts
        for record in artifact.story_events
        if record.disposition not in {"reject", "exclude"}
    ]


def _manifest_paths(manifest: PromptPackageManifest, key: str) -> list[str]:
    """从 Prompt Package 参数读取绝对来源路径列表。"""

    raw = manifest.parameters.get(key)
    if raw is None:
        raise PromptPackageError(f"Thought manifest 缺少参数: {key}")
    parsed = json.loads(raw)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise PromptPackageError(f"Thought manifest 参数不是字符串路径列表: {key}")
    return parsed


def _finish_thought_artifact(
    input_artifacts: list[Stage2InputArtifact],
    updates: list[ThoughtUpdateEvidence],
    event_facts: list[dict[str, object]],
    issues: list[NormalizationIssue],
    threads: list[ThoughtThreadReviewRecord],
    unassigned: list[UnassignedThoughtUpdateDecision],
    assigned_ids: set[str],
    direct_sources: list[SourceFingerprint],
    aggregation_model: str,
    previous: Stage3ThoughtReviewArtifact | None,
    output_path: str,
    allowed_removed_ids: set[str] | None,
    allow_all_removed: bool,
) -> tuple[Stage3ThoughtReviewArtifact, ReviewMigrationReport]:
    """补齐未覆盖 Update，迁移上一版审核并执行消失保护。"""

    for update in updates:
        if update.update_id in assigned_ids:
            continue
        if any(item.update_id == update.update_id for item in unassigned):
            continue
        unassigned.append(
            UnassignedThoughtUpdateDecision(
                update_id=update.update_id,
                kind="unresolved",
                generated_reason="Thought 聚合 response 未覆盖该 Update。",
                risk_level="high",
                review_basis_sha256=canonical_json_sha256(
                    {"update": update.model_dump(mode="json"), "kind": "unresolved"}
                ),
            )
        )
    reference = input_artifacts[0].metadata
    artifact = Stage3ThoughtReviewArtifact(
        metadata=ThoughtAggregationMetadata(
            anime_title=reference.anime_title,
            series_id=reference.series_id,
            timeline_id=reference.timeline_id,
            story_year=reference.story_year,
            canon_branch=reference.canon_branch,
            episodes=sorted(item.metadata.episode for item in input_artifacts),
        ),
        direct_sources=direct_sources,
        aggregation_model=aggregation_model,
        updates=updates,
        event_facts=event_facts,
        threads=threads,
        unassigned_updates=unassigned,
        issues=issues,
    )
    report = ReviewMigrationReport(
        artifact_type="stage3_thought_review",
        output_path=output_path,
        succeeded=True,
    )
    _migrate_thought_review(artifact, previous, report)
    report.counts = MigrationCounts(
        old_candidates=0 if previous is None else len(previous.threads),
        new_candidates=len(artifact.threads),
        migrated_reviews=len(report.migrated_ids),
        reset_reviews=len(report.reset_ids),
        added_candidates=len(report.added_ids),
        disappeared_candidates=len(report.disappeared_ids),
        pending_identities=len(report.pending_identity_ids),
    )
    if previous is not None:
        completed_publish_threads = {
            item.thought_thread_id: item
            for item in previous.threads
            if item.review_status == "completed" and item.disposition == "publish"
        }
        protected_ids = set(completed_publish_threads)
        for item in completed_publish_threads.values():
            protected_ids.update(state.thought_state_id for state in item.effective_sequence())
        effective_allowed = set(allowed_removed_ids or set())
        for thread_id in effective_allowed & completed_publish_threads.keys():
            effective_allowed.update(
                state.thought_state_id
                for state in completed_publish_threads[thread_id].effective_sequence()
            )
        report.blocked_removed_ids = blocked_removed_ids(
            protected_ids,
            set(report.disappeared_ids),
            effective_allowed,
            allow_all_removed,
        )
        if report.blocked_removed_ids:
            report.succeeded = False
            report.blocked_reason = "已发布 Thought Thread/State 消失但未取得本次删除许可"
            if output_path:
                write_migration_report(report, output_path)
            raise ValueError(report.blocked_reason)
    return artifact, report


def _stable_update_id(update: object) -> str:
    """根据 Update 的稳定来源身份生成可读前缀摘要 ID。"""

    payload = json.dumps(update, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"thought_update:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _validate_common_scope(
    input_artifacts: list[Stage2InputArtifact],
    stage2b_artifacts: list[Stage2BAnnotationArtifact],
    rag_artifacts: list[Stage3DocumentReviewArtifact],
) -> None:
    """校验跨集 Thought 输入数量、范围和 episode 一一对应。"""

    if not input_artifacts:
        raise ValueError("Thought 全量聚合至少需要一集输入")
    if not (len(input_artifacts) == len(stage2b_artifacts) == len(rag_artifacts)):
        raise ValueError("Stage 2 Input、Stage 2B 与 RAG Review 数量必须一致")
    reference = input_artifacts[0].metadata
    seen_episodes: set[int] = set()
    for input_artifact, stage2b_artifact, rag_artifact in zip(
        input_artifacts,
        stage2b_artifacts,
        rag_artifacts,
    ):
        metadata = input_artifact.metadata
        if (
            metadata.series_id != reference.series_id
            or metadata.timeline_id != reference.timeline_id
            or metadata.canon_branch != reference.canon_branch
        ):
            raise ValueError("Thought 全量聚合只接受同一系列、时间线与剧情分支")
        if metadata.episode in seen_episodes:
            raise ValueError(f"Thought 全量聚合发现重复 episode: {metadata.episode}")
        seen_episodes.add(metadata.episode)
        if stage2b_artifact.metadata.episode != metadata.episode or rag_artifact.metadata.episode != metadata.episode:
            raise ValueError("Thought 输入的 episode 配对不一致")


def _legacy_story_artifact(rag_artifact: Stage3DocumentReviewArtifact) -> Stage3NormalizedImportArtifact:
    """把新 Story Review 投影为现有确定性链接器所需的内部结构。"""

    story_events = [
        StoryEventImportRecord(
            point_id=record.candidate_id,
            source_scene_id=record.source_scene_id,
            source_local_id=record.source_local_id,
            evidence_u_ids=record.evidence_u_ids,
            evidence_s_ids=record.evidence_s_ids,
            confidence=record.confidence,
            document=record.effective_document(),
        )
        for record in rag_artifact.story_events
        if record.disposition not in {"reject", "exclude"}
    ]
    lore_entries = [
        LoreEntryImportRecord(
            point_id=record.candidate_id,
            source_scene_id=record.source_scene_id,
            source_local_id=record.source_local_id,
            evidence_u_ids=record.evidence_u_ids,
            evidence_s_ids=record.evidence_s_ids,
            confidence=record.confidence,
            document=record.effective_document(),
        )
        for record in rag_artifact.lore_entries
        if record.disposition not in {"reject", "exclude"}
    ]
    return Stage3NormalizedImportArtifact(
        metadata=rag_artifact.metadata,
        story_events=story_events,
        lore_entries=lore_entries,
        issues=rag_artifact.issues,
    )


def _collect_updates(
    input_artifacts: list[Stage2InputArtifact],
    stage2b_artifacts: list[Stage2BAnnotationArtifact],
    rag_artifacts: list[Stage3DocumentReviewArtifact],
) -> tuple[list[ThoughtUpdateEvidence], list[dict[str, object]], list[NormalizationIssue]]:
    """复用确定性链接前处理并汇总跨集 Update 与 Event Fact。"""

    updates: list[ThoughtUpdateEvidence] = []
    event_facts: list[dict[str, object]] = []
    issues: list[NormalizationIssue] = []
    for input_artifact, stage2b_artifact, rag_artifact in zip(
        input_artifacts,
        stage2b_artifacts,
        rag_artifacts,
    ):
        preparation = _prepare_thought_pipeline(
            input_artifact,
            stage2b_artifact,
            _legacy_story_artifact(rag_artifact),
        )
        issues.extend(preparation.issues)
        event_facts.extend(fact.model_dump(mode="json") for fact in preparation.facts)
        for linked in preparation.linked_updates:
            stable_payload = {
                "series_id": input_artifact.metadata.series_id,
                "timeline_id": input_artifact.metadata.timeline_id,
                "canon_branch": input_artifact.metadata.canon_branch,
                "source_scene_id": linked.source_scene_id,
                "source_local_id": linked.source_local_id,
                "character_id": linked.character_id.value,
            }
            updates.append(
                ThoughtUpdateEvidence(
                    update_id=_stable_update_id(stable_payload),
                    source_scene_id=linked.source_scene_id,
                    source_local_id=linked.source_local_id,
                    character_id=linked.character_id,
                    thought_text=linked.thought_text,
                    subject_kind=linked.subject_kind,
                    subject_text=linked.subject_text,
                    epistemic_status=linked.epistemic_status,
                    provisional_update_type=linked.provisional_update_type,
                    evidence_strength=linked.evidence_strength,
                    evidence_u_ids=linked.evidence_u_ids,
                    inference_note=linked.inference_note,
                    ambiguity_notes=linked.ambiguity_notes,
                    extraction_confidence=linked.extraction_confidence,
                    event_candidate_id=linked.about_event_id,
                    event_fact_id=linked.about_fact_id,
                    evidence_time=linked.evidence_time,
                )
            )
    return sorted(updates, key=lambda item: (item.evidence_time, item.update_id)), event_facts, issues


def render_thought_aggregation_prompt(
    character_id: str,
    updates: list[ThoughtUpdateEvidence],
    story_events: list[dict[str, object]],
    event_facts: list[dict[str, object]],
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
) -> str:
    """渲染一个角色完整集数范围的 Thought 聚合 Prompt。"""

    resolved_template = Path(template_path)
    environment = Environment(
        loader=FileSystemLoader(str(resolved_template.parent)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    template = environment.get_template(resolved_template.name)
    return template.render(
        character_id=character_id,
        updates_json=json.dumps([item.model_dump(mode="json") for item in updates], ensure_ascii=False, indent=2),
        story_events_json=json.dumps(story_events, ensure_ascii=False, indent=2),
        event_facts_json=json.dumps(event_facts, ensure_ascii=False, indent=2),
    )


def _thread_basis(
    thread: ThoughtThreadReviewRecord,
    updates_by_id: dict[str, ThoughtUpdateEvidence],
) -> str:
    """计算 Thought Thread 的完整证据和机器序列审核摘要。"""

    update_payloads = [
        updates_by_id[update_id].model_dump(mode="json")
        for update_id in sorted(set(thread.covered_update_ids))
    ]
    sequence_payloads = [
        {
            "transition": state.transition,
            "supporting_update_ids": sorted(set(state.supporting_update_ids)),
            "thought_text": state.thought_text,
            "epistemic_status": state.epistemic_status,
            "visible_from": state.visible_from,
            "visible_to": state.visible_to,
            "story_event_candidate_ids": sorted(set(state.story_event_candidate_ids)),
            "event_fact_ids": sorted(set(state.event_fact_ids)),
            "tags": state.tags,
            "retrieval_text": state.retrieval_text,
        }
        for state in thread.generated_sequence
    ]
    return canonical_json_sha256(
        {
            "character_id": thread.character_id,
            "canonical_subject": thread.canonical_subject,
            "thought_aspect": thread.thought_aspect,
            "updates": update_payloads,
            "transitions": [item.model_dump(mode="json") for item in thread.transitions],
            "generated_sequence": sequence_payloads,
        }
    )


def _build_character_threads(
    response: CharacterThoughtAggregationResponse,
    updates_by_id: dict[str, ThoughtUpdateEvidence],
    valid_event_ids: set[str],
    valid_fact_ids: set[str],
    assigned_ids: set[str],
    issues: list[NormalizationIssue],
    series_id: str,
    timeline_id: str,
    canon_branch: str,
) -> tuple[list[ThoughtThreadReviewRecord], list[UnassignedThoughtUpdateDecision]]:
    """校验一个角色的 LLM response 并投影时间区间。"""

    threads: list[ThoughtThreadReviewRecord] = []
    unassigned: list[UnassignedThoughtUpdateDecision] = []
    seen_thread_keys: set[tuple[str, str]] = set()
    for proposal in response.threads:
        thread_key = (proposal.canonical_subject.strip(), proposal.thought_aspect.strip())
        if thread_key in seen_thread_keys:
            issues.append(
                NormalizationIssue(
                    scene_id="__aggregate__",
                    collection_name="character_thoughts",
                    candidate_local_id=response.character_id.value,
                    message=f"LLM 重复创建 Thought Thread: {thread_key[0]} / {thread_key[1]}",
                )
            )
        seen_thread_keys.add(thread_key)
        sequence: list[ThoughtStateDraft] = []
        transitions: list[ThoughtTransitionDraft] = []
        covered: list[str] = []
        active: ThoughtStateDraft | None = None
        for state_proposal in proposal.states:
            valid_update_ids: list[str] = []
            for update_id in dict.fromkeys(state_proposal.supporting_update_ids):
                update = updates_by_id.get(update_id)
                if update is None or update.character_id != response.character_id:
                    issues.append(
                        NormalizationIssue(
                            scene_id="__aggregate__",
                            collection_name="character_thoughts",
                            candidate_local_id=update_id,
                            message="Thought Thread 引用了不存在或属于其他角色的 Update。",
                        )
                    )
                    continue
                if update_id in assigned_ids:
                    issues.append(
                        NormalizationIssue(
                            scene_id=update.source_scene_id,
                            collection_name="character_thoughts",
                            candidate_local_id=update_id,
                            message="Thought Update 被重复分配。",
                        )
                    )
                    continue
                assigned_ids.add(update_id)
                valid_update_ids.append(update_id)
            if not valid_update_ids:
                continue
            covered.extend(valid_update_ids)
            evidence_time = min(updates_by_id[update_id].evidence_time for update_id in valid_update_ids)
            transitions.append(
                ThoughtTransitionDraft(
                    transition=state_proposal.transition,
                    supporting_update_ids=valid_update_ids,
                    evidence_time=evidence_time,
                )
            )
            valid_story_ids = sorted(set(state_proposal.story_event_candidate_ids) & valid_event_ids)
            valid_event_fact_ids = sorted(set(state_proposal.event_fact_ids) & valid_fact_ids)
            if state_proposal.transition == "reaffirmed" and active is not None:
                active.supporting_update_ids = sorted(set(active.supporting_update_ids) | set(valid_update_ids))
                active.story_event_candidate_ids = sorted(set(active.story_event_candidate_ids) | set(valid_story_ids))
                active.event_fact_ids = sorted(set(active.event_fact_ids) | set(valid_event_fact_ids))
                continue
            if state_proposal.transition == "retracted":
                if active is not None:
                    active.visible_to = max(active.visible_from, evidence_time - 1)
                    active = None
                continue
            if active is not None:
                active.visible_to = max(active.visible_from, evidence_time - 1)
            transition = "acquired" if not sequence else "revised"
            thought_text = state_proposal.thought_text.strip()
            retrieval_text = state_proposal.retrieval_text.strip() or thought_text
            if not thought_text or not retrieval_text:
                issues.append(
                    NormalizationIssue(
                        scene_id=updates_by_id[valid_update_ids[0]].source_scene_id,
                        collection_name="character_thoughts",
                        candidate_local_id=valid_update_ids[0],
                        message="Thought State 缺少自包含文本或检索文本。",
                    )
                )
                continue
            active = ThoughtStateDraft(
                thought_state_id=f"thought_state:{uuid4()}",
                transition=transition,
                supporting_update_ids=valid_update_ids,
                thought_text=thought_text,
                epistemic_status=state_proposal.epistemic_status,
                visible_from=evidence_time,
                visible_to=THOUGHT_VISIBLE_TO_DEFAULT,
                story_event_candidate_ids=valid_story_ids,
                event_fact_ids=valid_event_fact_ids,
                tags=state_proposal.tags,
                retrieval_text=retrieval_text,
            )
            sequence.append(active)
        if not covered:
            continue
        risk_level = "high" if any(updates_by_id[item].evidence_strength == "inferred" for item in covered) else "low"
        thread = ThoughtThreadReviewRecord(
            thought_thread_id=f"thought_thread:{uuid4()}",
            character_id=response.character_id,
            series_id=series_id,
            timeline_id=timeline_id,
            canon_branch=canon_branch,
            canonical_subject=thread_key[0],
            thought_aspect=thread_key[1],
            covered_update_ids=sorted(set(covered)),
            transitions=transitions,
            generated_sequence=sequence,
            risk_level=risk_level,
            review_basis_sha256="0" * 64,
        )
        thread.review_basis_sha256 = _thread_basis(thread, updates_by_id)
        threads.append(thread)
    for proposal in response.unassigned_updates:
        update = updates_by_id.get(proposal.update_id)
        if update is None or update.character_id != response.character_id:
            continue
        if proposal.update_id in assigned_ids:
            issues.append(
                NormalizationIssue(
                    scene_id=update.source_scene_id,
                    collection_name="character_thoughts",
                    candidate_local_id=proposal.update_id,
                    message="Thought Update 同时出现在 Thread 和 unassigned_updates。",
                )
            )
            continue
        assigned_ids.add(proposal.update_id)
        suggested = "exclude" if proposal.kind == "excluded" else None
        unassigned.append(
            UnassignedThoughtUpdateDecision(
                update_id=proposal.update_id,
                kind=proposal.kind,
                generated_reason=proposal.reason,
                suggested_disposition=suggested,
                risk_level="high" if proposal.kind == "unresolved" else "medium",
                review_basis_sha256=canonical_json_sha256(
                    {
                        "update": update.model_dump(mode="json"),
                        "kind": proposal.kind,
                        "reason": proposal.reason,
                    }
                ),
            )
        )
    return threads, unassigned


def _inherit_thought_state_ids(
    current: list[ThoughtStateDraft],
    previous: list[ThoughtStateDraft],
) -> None:
    """按完全相同支持 Update 集合继承 Thought State 身份。"""

    previous_by_updates = {
        tuple(sorted(set(state.supporting_update_ids))): state
        for state in previous
    }
    for state in current:
        old = previous_by_updates.get(tuple(sorted(set(state.supporting_update_ids))))
        if old is not None:
            state.thought_state_id = old.thought_state_id


def _migrate_thought_review(
    artifact: Stage3ThoughtReviewArtifact,
    previous: Stage3ThoughtReviewArtifact | None,
    report: ReviewMigrationReport,
) -> None:
    """迁移 Thread、State 和未归属 Update 的上一版审核。"""

    if previous is None:
        report.added_ids = [item.thought_thread_id for item in artifact.threads]
        return
    if (
        previous.metadata.series_id != artifact.metadata.series_id
        or previous.metadata.timeline_id != artifact.metadata.timeline_id
        or previous.metadata.canon_branch != artifact.metadata.canon_branch
        or previous.metadata.episodes != artifact.metadata.episodes
    ):
        raise ValueError("previous Thought Review 的范围与当前全量输入不兼容")
    previous_by_key = {
        (item.character_id.value, item.canonical_subject, item.thought_aspect): item
        for item in previous.threads
    }
    matched: set[str] = set()
    updates_by_id = {item.update_id: item for item in artifact.updates}
    for thread in artifact.threads:
        old = previous_by_key.get(
            (thread.character_id.value, thread.canonical_subject, thread.thought_aspect)
        )
        if old is None:
            report.added_ids.append(thread.thought_thread_id)
            continue
        thread.thought_thread_id = old.thought_thread_id
        thread.previous_review_reference = old.thought_thread_id
        _inherit_thought_state_ids(thread.generated_sequence, old.generated_sequence)
        current_state_ids = {state.thought_state_id for state in thread.generated_sequence}
        report.disappeared_ids.extend(
            state.thought_state_id
            for state in old.effective_sequence()
            if state.thought_state_id not in current_state_ids
        )
        thread.review_basis_sha256 = _thread_basis(thread, updates_by_id)
        matched.add(old.thought_thread_id)
        if thread.review_basis_sha256 == old.review_basis_sha256:
            copy_completed_review(thread, old)
            thread.reviewed_sequence = old.reviewed_sequence
            report.migrated_ids.append(thread.thought_thread_id)
        else:
            thread.previous_reviewed_sequence = old.reviewed_sequence
            report.reset_ids.append(thread.thought_thread_id)
    previous_unassigned = {item.update_id: item for item in previous.unassigned_updates}
    for decision in artifact.unassigned_updates:
        old = previous_unassigned.get(decision.update_id)
        if old is not None and old.review_basis_sha256 == decision.review_basis_sha256:
            copy_completed_review(decision, old)
            report.migrated_ids.append(decision.update_id)
    report.disappeared_ids = sorted(
        set(report.disappeared_ids)
        | {
            item.thought_thread_id
            for item in previous.threads
            if item.thought_thread_id not in matched
        }
    )


def build_stage3_thought_review_artifact(
    input_artifacts: list[Stage2InputArtifact],
    stage2b_artifacts: list[Stage2BAnnotationArtifact],
    rag_artifacts: list[Stage3DocumentReviewArtifact],
    direct_sources: list[SourceFingerprint],
    llm_config: LiteLLMConfig,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    previous: Stage3ThoughtReviewArtifact | None = None,
    status_callback: Callable[[str], None] | None = None,
    output_path: str = "",
    allowed_removed_ids: set[str] | None = None,
    allow_all_removed: bool = False,
) -> tuple[Stage3ThoughtReviewArtifact, ReviewMigrationReport]:
    """按角色调用一次 LLM，构建完整范围的长期 Thought Review。"""

    _validate_common_scope(input_artifacts, stage2b_artifacts, rag_artifacts)
    updates, event_facts, issues = _collect_updates(input_artifacts, stage2b_artifacts, rag_artifacts)
    updates_by_id = {item.update_id: item for item in updates}
    grouped: dict[str, list[ThoughtUpdateEvidence]] = defaultdict(list)
    for update in updates:
        grouped[update.character_id.value].append(update)
    story_events = [
        {
            "candidate_id": record.candidate_id,
            "source_scene_id": record.source_scene_id,
            "source_local_id": record.source_local_id,
            "title": record.effective_document().title,
            "summary": record.effective_document().summary,
        }
        for artifact in rag_artifacts
        for record in artifact.story_events
        if record.disposition not in {"reject", "exclude"}
    ]
    valid_event_ids = {str(item["candidate_id"]) for item in story_events}
    valid_fact_ids = {str(item.get("fact_id", "")) for item in event_facts}
    client = LiteLLMJsonClient(llm_config)
    threads: list[ThoughtThreadReviewRecord] = []
    unassigned: list[UnassignedThoughtUpdateDecision] = []
    assigned_ids: set[str] = set()
    for index, (character_id, character_updates) in enumerate(sorted(grouped.items()), start=1):
        if status_callback is not None:
            status_callback(f"[{index}/{len(grouped)}] 正在聚合角色 {character_id} 的长期观点")
        prompt = render_thought_aggregation_prompt(
            character_id,
            character_updates,
            story_events,
            event_facts,
            template_path,
        )
        try:
            response = CharacterThoughtAggregationResponse.model_validate(
                client.complete_json(prompt).parsed_payload
            )
        except (ValueError, ValidationError) as exc:
            issues.append(
                NormalizationIssue(
                    scene_id="__aggregate__",
                    collection_name="character_thoughts",
                    candidate_local_id=character_id,
                    message=f"Thought 聚合 LLM 输出无效: {type(exc).__name__}: {exc}",
                )
            )
            continue
        if response.character_id.value != character_id:
            issues.append(
                NormalizationIssue(
                    scene_id="__aggregate__",
                    collection_name="character_thoughts",
                    candidate_local_id=character_id,
                    message="Thought 聚合响应角色与任务角色不一致。",
                )
            )
            continue
        character_threads, character_unassigned = _build_character_threads(
            response,
            updates_by_id,
            valid_event_ids,
            valid_fact_ids,
            assigned_ids,
            issues,
            input_artifacts[0].metadata.series_id,
            input_artifacts[0].metadata.timeline_id,
            input_artifacts[0].metadata.canon_branch,
        )
        threads.extend(character_threads)
        unassigned.extend(character_unassigned)
    for update in updates:
        if update.update_id in assigned_ids:
            continue
        unassigned.append(
            UnassignedThoughtUpdateDecision(
                update_id=update.update_id,
                kind="unresolved",
                generated_reason="LLM 响应未覆盖该 Update。",
                risk_level="high",
                review_basis_sha256=canonical_json_sha256(
                    {"update": update.model_dump(mode="json"), "kind": "unresolved"}
                ),
            )
        )
    reference = input_artifacts[0].metadata
    artifact = Stage3ThoughtReviewArtifact(
        metadata=ThoughtAggregationMetadata(
            anime_title=reference.anime_title,
            series_id=reference.series_id,
            timeline_id=reference.timeline_id,
            story_year=reference.story_year,
            canon_branch=reference.canon_branch,
            episodes=sorted(item.metadata.episode for item in input_artifacts),
        ),
        direct_sources=direct_sources,
        aggregation_model=llm_config.model,
        updates=updates,
        event_facts=event_facts,
        threads=threads,
        unassigned_updates=unassigned,
        issues=issues,
    )
    report = ReviewMigrationReport(
        artifact_type="stage3_thought_review",
        output_path=output_path,
        succeeded=True,
    )
    _migrate_thought_review(artifact, previous, report)
    report.counts = MigrationCounts(
        old_candidates=0 if previous is None else len(previous.threads),
        new_candidates=len(artifact.threads),
        migrated_reviews=len(report.migrated_ids),
        reset_reviews=len(report.reset_ids),
        added_candidates=len(report.added_ids),
        disappeared_candidates=len(report.disappeared_ids),
        pending_identities=len(report.pending_identity_ids),
    )
    if previous is not None:
        completed_publish_threads = {
            item.thought_thread_id: item
            for item in previous.threads
            if item.review_status == "completed" and item.disposition == "publish"
        }
        protected_ids = set(completed_publish_threads)
        for item in completed_publish_threads.values():
            protected_ids.update(state.thought_state_id for state in item.effective_sequence())
        effective_allowed = set(allowed_removed_ids or set())
        for thread_id in effective_allowed & completed_publish_threads.keys():
            effective_allowed.update(
                state.thought_state_id
                for state in completed_publish_threads[thread_id].effective_sequence()
            )
        report.blocked_removed_ids = blocked_removed_ids(
            protected_ids,
            set(report.disappeared_ids),
            effective_allowed,
            allow_all_removed,
        )
        if report.blocked_removed_ids:
            report.succeeded = False
            report.blocked_reason = "已发布 Thought Thread/State 消失但未取得本次删除许可"
            if output_path:
                write_migration_report(report, output_path)
            raise ValueError(report.blocked_reason)
    return artifact, report


def load_stage3_thought_review_artifact(path: str | Path) -> Stage3ThoughtReviewArtifact:
    """读取严格的全量 Thought Review，并拒绝旧逐集格式。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return Stage3ThoughtReviewArtifact.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("输入不是新版全量 Thought Review；旧逐集 Thought Review 不可迁移") from exc


def save_stage3_thought_review_artifact(
    artifact: Stage3ThoughtReviewArtifact,
    report: ReviewMigrationReport,
    output_path: str | Path,
) -> None:
    """安全保存 Thought Review 和本次迁移报告。"""

    safely_write_json_model(artifact, output_path)
    write_migration_report(report, output_path)
