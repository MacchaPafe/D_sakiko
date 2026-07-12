"""Stage 3：链接、聚合并评估 Character Thought 候选。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from rag.models import CharacterThoughtDocument
from rag.services import PointRecord, QdrantRagService, UpsertReport

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
from .schemas import (
    CharacterThoughtPayload,
    CharacterThoughtReviewRecord,
    LinkedCharacterThoughtUpdate,
    NormalizationIssue,
    NormalizedEventFact,
    ResolvedThoughtUpdateType,
    Stage2BAnnotationArtifact,
    Stage2InputArtifact,
    Stage2Utterance,
    Stage3NormalizedImportArtifact,
    Stage3ThoughtImportArtifact,
    ThoughtReferenceLinkDecision,
)
from .stage3_rag_import import resolve_character_id


THOUGHT_VISIBLE_TO_DEFAULT = 999999
DEFAULT_LINK_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "thought_reference_linking.jinja"


@dataclass(frozen=True, slots=True)
class _EventLinkCandidate:
    """保存提供给 Stage 3 LLM 的 Story Event 候选摘要。"""

    id: str
    title: str
    summary: str
    time: int


@dataclass(frozen=True, slots=True)
class _FactLinkCandidate:
    """保存提供给 Stage 3 LLM 的 Event Fact 候选摘要。"""

    id: str
    text: str
    event_id: str | None


@dataclass(slots=True)
class _ThoughtPipelinePreparation:
    """保存观点链接前的确定性规范化结果。"""

    issues: list[NormalizationIssue]
    scene_time_map: dict[str, int]
    utterance_map: dict[str, Stage2Utterance]
    evidence_scene_map: dict[str, str]
    facts: list[NormalizedEventFact]
    linked_updates: list[LinkedCharacterThoughtUpdate]


def build_stage3_thought_import_artifact(
    input_artifact: Stage2InputArtifact,
    stage2b_artifact: Stage2BAnnotationArtifact,
    stage3_rag_artifact: Stage3NormalizedImportArtifact,
    linker_llm_config: LiteLLMConfig | None = None,
    linker_template_path: str | Path = DEFAULT_LINK_TEMPLATE_PATH,
    max_link_candidates: int = 8,
) -> Stage3ThoughtImportArtifact:
    """把 Stage 2B 局部候选转换为可复核的跨场景 Character Thought。"""

    if max_link_candidates < 1:
        raise ValueError("max_link_candidates 必须大于等于 1")
    preparation = _prepare_thought_pipeline(
        input_artifact,
        stage2b_artifact,
        stage3_rag_artifact,
    )
    if linker_llm_config is not None:
        _resolve_unlinked_updates_with_llm(
            preparation.linked_updates,
            preparation.facts,
            stage3_rag_artifact,
            preparation.scene_time_map,
            linker_llm_config,
            linker_template_path,
            max_link_candidates,
            preparation.issues,
        )
    thought_records = _build_thought_timeline(
        preparation.linked_updates,
        preparation.utterance_map,
        preparation.evidence_scene_map,
        preparation.facts,
        stage3_rag_artifact,
    )
    return Stage3ThoughtImportArtifact(
        metadata=stage3_rag_artifact.metadata,
        source_stage2b_model=stage2b_artifact.model,
        linker_model=None if linker_llm_config is None else linker_llm_config.model,
        event_facts=preparation.facts,
        linked_updates=preparation.linked_updates,
        character_thoughts=thought_records,
        issues=preparation.issues,
    )


def _prepare_thought_pipeline(
    input_artifact: Stage2InputArtifact,
    stage2b_artifact: Stage2BAnnotationArtifact,
    stage3_rag_artifact: Stage3NormalizedImportArtifact,
) -> _ThoughtPipelinePreparation:
    """执行观点语义链接之前的全部确定性规范化。"""

    issues: list[NormalizationIssue] = []
    event_map = {
        (record.source_scene_id, record.source_local_id): record
        for record in stage3_rag_artifact.story_events
    }
    scene_time_map = _build_scene_time_map(input_artifact, stage3_rag_artifact)
    utterance_map = {
        utterance.u_id: utterance
        for scene in input_artifact.scenes
        for utterance in scene.utterances
    }
    evidence_scene_map = {
        utterance.u_id: scene.scene_id
        for scene in input_artifact.scenes
        for utterance in scene.utterances
    }
    facts = _normalize_event_facts(stage2b_artifact, event_map, issues)
    fact_map = {(fact.source_scene_id, fact.source_local_id): fact for fact in facts}
    linked_updates = _link_updates(
        stage2b_artifact,
        event_map,
        fact_map,
        scene_time_map,
        issues,
    )
    return _ThoughtPipelinePreparation(
        issues=issues,
        scene_time_map=scene_time_map,
        utterance_map=utterance_map,
        evidence_scene_map=evidence_scene_map,
        facts=facts,
        linked_updates=linked_updates,
    )


def prepare_stage3_thought_link_prompt_package(
    input_artifact: Stage2InputArtifact,
    stage2b_artifact: Stage2BAnnotationArtifact,
    stage3_rag_artifact: Stage3NormalizedImportArtifact,
    input_path: str | Path,
    stage2b_path: str | Path,
    stage3_rag_path: str | Path,
    output_dir: str | Path,
    template_path: str | Path = DEFAULT_LINK_TEMPLATE_PATH,
    max_link_candidates: int = 8,
) -> PromptPackageManifest:
    """只为确定性链接未解决的观点生成 Stage 3 静态任务包。"""

    if max_link_candidates < 1:
        raise ValueError("max_link_candidates 必须大于等于 1")
    preparation = _prepare_thought_pipeline(
        input_artifact,
        stage2b_artifact,
        stage3_rag_artifact,
    )
    resolved_template_path = Path(template_path).resolve()
    prompts: list[PreparedPrompt] = []
    for update in preparation.linked_updates:
        if update.link_status != "unresolved":
            continue
        event_candidates, fact_candidates = _select_link_candidates(
            update,
            preparation.facts,
            stage3_rag_artifact,
            preparation.scene_time_map,
            max_link_candidates,
        )
        prompts.append(
            PreparedPrompt(
                task_id=f"thought_link:{update.source_scene_id}:{update.source_local_id}",
                prompt=_render_link_prompt(
                    update,
                    event_candidates,
                    fact_candidates,
                    resolved_template_path,
                ),
                context={
                    "source_scene_id": update.source_scene_id,
                    "source_local_id": update.source_local_id,
                    "event_candidate_count": str(len(event_candidates)),
                    "fact_candidate_count": str(len(fact_candidates)),
                },
            )
        )
    return create_prompt_package(
        output_dir=output_dir,
        stage_kind="stage3_thought_link",
        prompts=prompts,
        source_paths=[input_path, stage2b_path, stage3_rag_path],
        template_paths=[resolved_template_path],
        parameters={
            "input_path": str(Path(input_path).resolve()),
            "stage2b_path": str(Path(stage2b_path).resolve()),
            "stage3_rag_path": str(Path(stage3_rag_path).resolve()),
            "template_path": str(resolved_template_path),
            "max_link_candidates": str(max_link_candidates),
            "preparation_issue_count": str(len(preparation.issues)),
        },
    )


def assemble_stage3_thought_link_prompt_package(
    manifest_path: str | Path,
    model_label: str = "codex-workspace",
    allow_partial: bool = False,
    allow_stale: bool = False,
) -> Stage3ThoughtImportArtifact:
    """应用离线语义链接 response 并构建 Character Thought 审查产物。"""

    manifest = load_prompt_package_manifest(manifest_path)
    if manifest.stage_kind != "stage3_thought_link":
        raise PromptPackageError("manifest 不是 Stage 3 Thought Link 任务包。")
    input_path = manifest.parameters.get("input_path")
    stage2b_path = manifest.parameters.get("stage2b_path")
    stage3_rag_path = manifest.parameters.get("stage3_rag_path")
    if not input_path or not stage2b_path or not stage3_rag_path:
        raise PromptPackageError("Stage 3 Thought manifest 缺少来源路径。")
    from .stage2_input_builder import load_stage2_input_artifact
    from .stage2b_thought_extraction import load_stage2b_annotation_artifact
    from .stage3_rag_import import load_stage3_normalized_import_artifact

    input_artifact = load_stage2_input_artifact(input_path)
    stage2b_artifact = load_stage2b_annotation_artifact(stage2b_path)
    stage3_rag_artifact = load_stage3_normalized_import_artifact(stage3_rag_path)
    preparation = _prepare_thought_pipeline(
        input_artifact,
        stage2b_artifact,
        stage3_rag_artifact,
    )
    max_link_candidates = int(manifest.parameters.get("max_link_candidates", "8"))
    update_map = {
        (update.source_scene_id, update.source_local_id): update
        for update in preparation.linked_updates
    }
    response_bundle = read_prompt_package_responses(
        manifest_path,
        allow_partial=allow_partial,
        allow_stale=allow_stale,
    )
    for task in manifest.tasks:
        scene_id = task.context.get("source_scene_id", "")
        local_id = task.context.get("source_local_id", "")
        update = update_map.get((scene_id, local_id))
        raw_response = response_bundle.responses.get(task.task_id)
        if update is None:
            preparation.issues.append(
                NormalizationIssue(
                    scene_id=scene_id,
                    collection_name="character_thoughts",
                    candidate_local_id=local_id or task.task_id,
                    message="manifest 引用的观点更新不存在。",
                )
            )
            continue
        if raw_response is None:
            preparation.issues.append(
                NormalizationIssue(
                    scene_id=scene_id,
                    collection_name="character_thoughts",
                    candidate_local_id=local_id,
                    message="缺少 Stage 3 Thought Link response。",
                )
            )
            continue
        event_candidates, fact_candidates = _select_link_candidates(
            update,
            preparation.facts,
            stage3_rag_artifact,
            preparation.scene_time_map,
            max_link_candidates,
        )
        try:
            decision = ThoughtReferenceLinkDecision.model_validate(
                parse_json_response(raw_response)
            )
            _apply_link_decision(update, decision, event_candidates, fact_candidates)
        except Exception as exc:
            preparation.issues.append(
                NormalizationIssue(
                    scene_id=scene_id,
                    collection_name="character_thoughts",
                    candidate_local_id=local_id,
                    message=f"Stage 3 Thought Link response 无效: {type(exc).__name__}: {exc}",
                )
            )
    thought_records = _build_thought_timeline(
        preparation.linked_updates,
        preparation.utterance_map,
        preparation.evidence_scene_map,
        preparation.facts,
        stage3_rag_artifact,
    )
    return Stage3ThoughtImportArtifact(
        metadata=stage3_rag_artifact.metadata,
        source_stage2b_model=stage2b_artifact.model,
        linker_model=model_label,
        event_facts=preparation.facts,
        linked_updates=preparation.linked_updates,
        character_thoughts=thought_records,
        issues=preparation.issues,
    )


def _build_scene_time_map(
    input_artifact: Stage2InputArtifact,
    stage3_rag_artifact: Stage3NormalizedImportArtifact,
) -> dict[str, int]:
    """为每个场景建立单调递增的证据时间锚点。"""

    explicit = {
        record.source_scene_id: record.document.visible_from
        for record in stage3_rag_artifact.story_events
    }
    first_time = min(explicit.values(), default=input_artifact.metadata.episode * 1000)
    result: dict[str, int] = {}
    cursor = first_time
    for scene in input_artifact.scenes:
        anchor = explicit.get(scene.scene_id, cursor)
        anchor = max(anchor, cursor)
        result[scene.scene_id] = anchor
        cursor = anchor + 1
    return result


def _normalize_event_facts(
    stage2b_artifact: Stage2BAnnotationArtifact,
    event_map: dict[tuple[str, str], object],
    issues: list[NormalizationIssue],
) -> list[NormalizedEventFact]:
    """规范化按需抽取的 Event Fact，并尽量保留其 Story Event 引用。"""

    normalized: list[NormalizedEventFact] = []
    for result in stage2b_artifact.results:
        if result.annotation is None:
            continue
        for fact in result.annotation.event_facts:
            event_record = (
                event_map.get((fact.scene_id, fact.event_local_id))
                if fact.event_local_id is not None
                else None
            )
            about_event_id = getattr(event_record, "point_id", None)
            if fact.event_local_id is not None and about_event_id is None:
                issues.append(
                    NormalizationIssue(
                        scene_id=fact.scene_id,
                        collection_name="event_facts",
                        candidate_local_id=fact.fact_local_id,
                        message=f"无法解析 event_local_id: {fact.event_local_id}",
                    )
                )
            normalized.append(
                NormalizedEventFact(
                    fact_id=_stable_id("event_fact", fact.scene_id, fact.fact_local_id),
                    source_scene_id=fact.scene_id,
                    source_local_id=fact.fact_local_id,
                    about_event_id=about_event_id,
                    fact_text=fact.fact_text,
                    tags=fact.tags,
                    evidence_u_ids=fact.evidence_u_ids,
                    evidence_s_ids=fact.evidence_s_ids,
                    confidence=fact.confidence,
                )
            )
    return normalized


def _link_updates(
    stage2b_artifact: Stage2BAnnotationArtifact,
    event_map: dict[tuple[str, str], object],
    fact_map: dict[tuple[str, str], NormalizedEventFact],
    scene_time_map: dict[str, int],
    issues: list[NormalizationIssue],
) -> list[LinkedCharacterThoughtUpdate]:
    """使用场景本地 ID 完成确定性链接，并保守保留无法链接的更新。"""

    linked: list[LinkedCharacterThoughtUpdate] = []
    for result in stage2b_artifact.results:
        if result.annotation is None:
            continue
        for update in result.annotation.character_thought_updates:
            try:
                character_id = resolve_character_id(update.character_name)
            except ValueError as exc:
                issues.append(
                    NormalizationIssue(
                        scene_id=update.scene_id,
                        collection_name="character_thoughts",
                        candidate_local_id=update.update_local_id,
                        message=str(exc),
                    )
                )
                continue

            event_record = (
                event_map.get((update.scene_id, update.about_event_local_id))
                if update.about_event_local_id is not None
                else None
            )
            fact_record = (
                fact_map.get((update.scene_id, update.about_fact_local_id))
                if update.about_fact_local_id is not None
                else None
            )
            about_event_id = getattr(event_record, "point_id", None)
            about_fact_id = None if fact_record is None else fact_record.fact_id
            standalone_key: str | None = None
            link_status = "unresolved"
            link_confidence = 0.0
            if update.subject_kind == "event" and about_event_id is not None:
                link_status = "linked"
                link_confidence = 1.0
            elif update.subject_kind == "event_fact" and about_fact_id is not None:
                link_status = "linked"
                link_confidence = 1.0
            elif update.subject_kind == "standalone_topic":
                link_status = "standalone"
                link_confidence = 1.0
                standalone_key = _normalize_topic_key(update.subject_text)

            reference_key = about_fact_id or about_event_id or standalone_key or _normalize_topic_key(update.subject_text)
            thought_aspect = _normalize_topic_key(update.subject_text)
            thought_thread_key = f"{character_id.value}:{update.subject_kind}:{reference_key}:{thought_aspect}"
            linked.append(
                LinkedCharacterThoughtUpdate(
                    source_scene_id=update.scene_id,
                    source_local_id=update.update_local_id,
                    character_id=character_id,
                    thought_text=update.thought_text,
                    subject_kind=update.subject_kind,
                    subject_text=update.subject_text,
                    epistemic_status=update.epistemic_status,
                    provisional_update_type=update.provisional_update_type,
                    resolved_update_type="acquired",
                    evidence_strength=update.evidence_strength,
                    evidence_u_ids=update.evidence_u_ids,
                    inference_note=update.inference_note,
                    ambiguity_notes=update.ambiguity_notes,
                    effective_from_hint=update.effective_from_hint,
                    extraction_confidence=update.extraction_confidence,
                    link_status=link_status,
                    link_confidence=link_confidence,
                    about_event_id=about_event_id,
                    about_fact_id=about_fact_id,
                    standalone_topic_key=standalone_key,
                    thought_aspect=thought_aspect,
                    thought_thread_key=thought_thread_key,
                    evidence_time=scene_time_map.get(update.scene_id, 0),
                )
            )
    return linked


def _resolve_unlinked_updates_with_llm(
    updates: list[LinkedCharacterThoughtUpdate],
    facts: list[NormalizedEventFact],
    stage3_rag_artifact: Stage3NormalizedImportArtifact,
    scene_time_map: dict[str, int],
    llm_config: LiteLLMConfig,
    template_path: str | Path,
    max_candidates: int,
    issues: list[NormalizationIssue],
) -> None:
    """让 LLM 仅在时间兼容的候选集合内处理确定性链接未解决项。"""

    for update in updates:
        if update.link_status != "unresolved":
            continue
        selected_events, selected_facts = _select_link_candidates(
            update,
            facts,
            stage3_rag_artifact,
            scene_time_map,
            max_candidates,
        )
        prompt = _render_link_prompt(update, selected_events, selected_facts, template_path)
        try:
            response = LiteLLMJsonClient(llm_config).complete_json(prompt)
            decision = ThoughtReferenceLinkDecision.model_validate(response.parsed_payload)
            _apply_link_decision(update, decision, selected_events, selected_facts)
        except Exception as exc:  # pragma: no cover - 真实 API 调用路径
            issues.append(
                NormalizationIssue(
                    scene_id=update.source_scene_id,
                    collection_name="character_thoughts",
                    candidate_local_id=update.source_local_id,
                    message=f"Stage 3 LLM 链接失败：{type(exc).__name__}: {exc}",
                )
            )


def _select_link_candidates(
    update: LinkedCharacterThoughtUpdate,
    facts: list[NormalizedEventFact],
    stage3_rag_artifact: Stage3NormalizedImportArtifact,
    scene_time_map: dict[str, int],
    max_candidates: int,
) -> tuple[list[_EventLinkCandidate], list[_FactLinkCandidate]]:
    """为一条未解决观点选择时间兼容且语义最接近的候选。"""

    event_candidates = [
        _EventLinkCandidate(
            id=record.point_id,
            title=record.document.title,
            summary=record.document.summary,
            time=record.document.visible_from,
        )
        for record in stage3_rag_artifact.story_events
        if record.document.visible_from <= update.evidence_time
    ]
    fact_candidates = [
        _FactLinkCandidate(
            id=fact.fact_id,
            text=fact.fact_text,
            event_id=fact.about_event_id,
        )
        for fact in facts
        if scene_time_map.get(fact.source_scene_id, 0) <= update.evidence_time
    ]
    event_candidates.sort(
        key=lambda candidate: _semantic_overlap_score(
            update.subject_text,
            f"{candidate.title} {candidate.summary}",
        ),
        reverse=True,
    )
    fact_candidates.sort(
        key=lambda candidate: _semantic_overlap_score(update.subject_text, candidate.text),
        reverse=True,
    )
    return event_candidates[:max_candidates], fact_candidates[:max_candidates]


def _render_link_prompt(
    update: LinkedCharacterThoughtUpdate,
    event_candidates: list[_EventLinkCandidate],
    fact_candidates: list[_FactLinkCandidate],
    template_path: str | Path,
) -> str:
    """渲染受限候选集合的 Stage 3 观点链接 prompt。"""

    resolved_path = Path(template_path)
    environment = Environment(
        loader=FileSystemLoader(str(resolved_path.parent)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    return environment.get_template(resolved_path.name).render(
        update=update.model_dump(mode="json"),
        event_candidates=event_candidates,
        fact_candidates=fact_candidates,
    )


def _apply_link_decision(
    update: LinkedCharacterThoughtUpdate,
    decision: ThoughtReferenceLinkDecision,
    event_candidates: list[_EventLinkCandidate],
    fact_candidates: list[_FactLinkCandidate],
) -> None:
    """校验 LLM 目标属于候选集合后更新观点引用和 Thought Thread。"""

    if decision.source_local_id != update.source_local_id:
        raise ValueError("Stage 3 LLM 返回了不匹配的 source_local_id")
    event_ids = {candidate.id for candidate in event_candidates}
    fact_ids = {candidate.id for candidate in fact_candidates}
    update.thought_aspect = _normalize_topic_key(decision.thought_aspect)
    update.link_confidence = decision.link_confidence
    if decision.link_status == "linked" and decision.target_kind == "event":
        if decision.target_id not in event_ids:
            raise ValueError("Stage 3 LLM 选择了候选集合外的 Story Event")
        update.subject_kind = "event"
        update.about_event_id = decision.target_id
        update.about_fact_id = None
        update.standalone_topic_key = None
        update.link_status = "linked"
    elif decision.link_status == "linked" and decision.target_kind == "event_fact":
        if decision.target_id not in fact_ids:
            raise ValueError("Stage 3 LLM 选择了候选集合外的 Event Fact")
        update.subject_kind = "event_fact"
        update.about_event_id = None
        update.about_fact_id = decision.target_id
        update.standalone_topic_key = None
        update.link_status = "linked"
    elif decision.link_status == "standalone" and decision.target_kind == "standalone_topic":
        update.subject_kind = "standalone_topic"
        update.about_event_id = None
        update.about_fact_id = None
        update.standalone_topic_key = _normalize_topic_key(update.subject_text)
        update.link_status = "standalone"
    elif decision.link_status == "unresolved" and decision.target_kind == "unresolved":
        update.link_status = "unresolved"
    else:
        raise ValueError("Stage 3 LLM 的 link_status 与 target_kind 组合不合法")
    reference_key = (
        update.about_fact_id
        or update.about_event_id
        or update.standalone_topic_key
        or _normalize_topic_key(update.subject_text)
    )
    update.thought_thread_key = (
        f"{update.character_id.value}:{update.subject_kind}:{reference_key}:{update.thought_aspect}"
    )


def _semantic_overlap_score(first: str, second: str) -> float:
    """计算轻量字符集合重叠分数，用于缩小 LLM 候选集合。"""

    first_chars = set(re.sub(r"\s+", "", first.casefold()))
    second_chars = set(re.sub(r"\s+", "", second.casefold()))
    if not first_chars or not second_chars:
        return 0.0
    return len(first_chars & second_chars) / len(first_chars | second_chars)


def _build_thought_timeline(
    updates: list[LinkedCharacterThoughtUpdate],
    utterance_map: dict[str, Stage2Utterance],
    evidence_scene_map: dict[str, str],
    facts: list[NormalizedEventFact],
    stage3_rag_artifact: Stage3NormalizedImportArtifact,
) -> list[CharacterThoughtReviewRecord]:
    """按 Thought Thread 聚合重申证据并投影观点有效区间。"""

    fact_tags = {fact.fact_id: fact.tags for fact in facts}
    event_tags = {record.point_id: record.document.tags for record in stage3_rag_artifact.story_events}
    thread_groups: dict[str, list[LinkedCharacterThoughtUpdate]] = defaultdict(list)
    for update in updates:
        thread_groups[update.thought_thread_key].append(update)

    records: list[CharacterThoughtReviewRecord] = []
    for thread_updates in thread_groups.values():
        ordered = sorted(thread_updates, key=lambda item: (item.evidence_time, item.source_scene_id, item.source_local_id))
        active_record: CharacterThoughtReviewRecord | None = None
        active_update: LinkedCharacterThoughtUpdate | None = None
        for update in ordered:
            if update.link_status == "unresolved":
                update.resolved_update_type = "acquired"
                risk_score, risk_reasons = _analyze_update_risk(
                    update,
                    utterance_map,
                    evidence_scene_map,
                )
                records.append(
                    CharacterThoughtReviewRecord(
                        point_id=_stable_id("character_thought_unresolved", update.source_scene_id, update.source_local_id),
                        source_update_ids=[update.source_local_id],
                        link_status="unresolved",
                        review_status="needs_followup",
                        risk_level="high",
                        risk_score=max(70, risk_score),
                        risk_reasons=["观点语义对象尚未可靠链接", *risk_reasons],
                        validation_errors=["unresolved 观点不得进入 Qdrant"],
                        provisional_update_types=[update.provisional_update_type],
                        resolved_update_type="acquired",
                    )
                )
                continue

            is_reaffirmed = (
                active_update is not None
                and active_update.thought_text == update.thought_text
                and active_update.epistemic_status == update.epistemic_status
            )
            if is_reaffirmed and active_record is not None and active_record.document is not None:
                update.resolved_update_type = "reaffirmed"
                active_record.source_update_ids.append(update.source_local_id)
                active_record.provisional_update_types.append(update.provisional_update_type)
                active_record.document.source_scene_ids = _ordered_union(
                    active_record.document.source_scene_ids,
                    [update.source_scene_id],
                )
                active_record.document.evidence_u_ids = _ordered_union(
                    active_record.document.evidence_u_ids,
                    update.evidence_u_ids,
                )
                active_record.document.extraction_confidence = min(
                    active_record.document.extraction_confidence,
                    update.extraction_confidence,
                )
                continue

            resolved_type: ResolvedThoughtUpdateType = (
                "retracted"
                if update.provisional_update_type == "retracted"
                else "revised" if active_record is not None else "acquired"
            )
            update.resolved_update_type = resolved_type
            if active_record is not None and active_record.document is not None:
                active_record.document.valid_to = max(
                    active_record.document.valid_from,
                    update.evidence_time - 1,
                )
            tags = fact_tags.get(update.about_fact_id or "", event_tags.get(update.about_event_id or "", []))
            document = CharacterThoughtPayload(
                character_id=update.character_id,
                series_id=stage3_rag_artifact.metadata.series_id,
                season_id=stage3_rag_artifact.metadata.season_id,
                canon_branch=stage3_rag_artifact.metadata.canon_branch,
                thought_thread_key=update.thought_thread_key,
                subject_kind=update.subject_kind,
                about_event_id=update.about_event_id,
                about_fact_id=update.about_fact_id,
                standalone_topic_key=update.standalone_topic_key,
                thought_text=update.thought_text,
                epistemic_status=update.epistemic_status,
                valid_from=update.evidence_time,
                valid_to=THOUGHT_VISIBLE_TO_DEFAULT,
                tags=tags,
                retrieval_text=f"{update.character_id.common_name}：{update.thought_text} 主题：{update.subject_text}",
                source_scene_ids=[update.source_scene_id],
                evidence_u_ids=update.evidence_u_ids,
                evidence_strength=update.evidence_strength,
                extraction_confidence=update.extraction_confidence,
                link_confidence=update.link_confidence,
            )
            risk_score, risk_reasons = _analyze_update_risk(
                update,
                utterance_map,
                evidence_scene_map,
            )
            if update.provisional_update_type not in {resolved_type, "unspecified", "disclosed_existing"}:
                risk_score += 15
                risk_reasons.append(
                    f"Stage 2B 更新类型 {update.provisional_update_type} 与 Stage 3 的 {resolved_type} 不一致"
                )
            risk_score = min(100, risk_score)
            active_record = CharacterThoughtReviewRecord(
                point_id=_stable_id("character_thought", update.thought_thread_key, str(update.evidence_time)),
                source_update_ids=[update.source_local_id],
                link_status=update.link_status,
                risk_level=_risk_level(risk_score),
                risk_score=risk_score,
                risk_reasons=risk_reasons,
                validation_errors=[],
                provisional_update_types=[update.provisional_update_type],
                resolved_update_type=resolved_type,
                document=document,
            )
            records.append(active_record)
            active_update = update
    return sorted(records, key=lambda item: (-item.risk_score, item.point_id))


def _analyze_update_risk(
    update: LinkedCharacterThoughtUpdate,
    utterance_map: dict[str, Stage2Utterance],
    evidence_scene_map: dict[str, str],
) -> tuple[int, list[str]]:
    """根据证据、链接与推断特征计算可解释的风险分数。"""

    score = 0
    reasons: list[str] = []
    if not update.evidence_u_ids:
        score += 70
        reasons.append("缺少台词证据")
    missing_ids = [u_id for u_id in update.evidence_u_ids if u_id not in utterance_map]
    if missing_ids:
        score += 60
        reasons.append(f"存在无效 evidence_u_id：{', '.join(missing_ids)}")
    cross_scene_ids = [
        u_id
        for u_id in update.evidence_u_ids
        if evidence_scene_map.get(u_id) not in {None, update.source_scene_id}
    ]
    if cross_scene_ids:
        score += 50
        reasons.append(f"Stage 2B 引用了其他场景的台词：{', '.join(cross_scene_ids)}")
    evidence = [utterance_map[u_id] for u_id in update.evidence_u_ids if u_id in utterance_map]
    low_confidence = [item.u_id for item in evidence if item.speaker_confidence < 0.65]
    if low_confidence:
        score += 25
        reasons.append(f"说话人置信度低：{', '.join(low_confidence)}")
    mismatched_monologue = [
        item.u_id
        for item in evidence
        if item.is_inner_monologue and item.speaker_name != update.character_id.common_name
    ]
    if mismatched_monologue:
        score += 70
        reasons.append(f"引用了其他角色的内心独白：{', '.join(mismatched_monologue)}")
    if update.evidence_strength == "inferred":
        score += 25
        reasons.append("观点依赖模型推断")
        sensitive_markers = ("真实原因", "内心", "故意", "其实", "秘密", "动机", "为了")
        if any(marker in update.thought_text for marker in sensitive_markers):
            score += 30
            reasons.append("推断型观点可能涉及隐藏动机或私人事实")
    if update.link_confidence < 0.7:
        score += 30
        reasons.append("观点链接置信度较低")
    if update.effective_from_hint in {"earlier_than_current_scene", "unknown"}:
        score += 15
        reasons.append("观点起始时间缺少明确锚点")
    if update.ambiguity_notes:
        score += min(20, 5 * len(update.ambiguity_notes))
        reasons.append("提取结果包含歧义说明")
    omniscient_markers = ("事实上", "真相是", "实际上一定", "所有人都知道")
    if any(marker in update.thought_text for marker in omniscient_markers):
        score += 20
        reasons.append("观点文本可能使用了全知口吻")
    if len(update.thought_text) > 180:
        score += 15
        reasons.append("观点文本过宽，可能混合多个认知方面")
    return min(100, score), reasons


def _risk_level(score: int) -> str:
    """把风险分数映射为查看器使用的三级标签。"""

    if score >= 60:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def _normalize_topic_key(text: str) -> str:
    """把自由文本主题规范化为稳定但仍可读的键。"""

    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text.casefold()).strip("_")
    return normalized[:80] or hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _stable_id(prefix: str, *parts: str) -> str:
    """根据来源字段生成可重复计算的短标识。"""

    raw = "|".join(parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _ordered_union(first: list[str], second: list[str]) -> list[str]:
    """按出现顺序合并两个字符串列表并去重。"""

    return list(dict.fromkeys([*first, *second]))


def save_stage3_thought_import_artifact(
    artifact: Stage3ThoughtImportArtifact,
    output_path: str | Path,
) -> None:
    """保存 Stage 3 Character Thought 审查产物。"""

    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_stage3_thought_import_artifact(input_path: str | Path) -> Stage3ThoughtImportArtifact:
    """读取并校验 Stage 3 Character Thought 审查产物。"""

    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return Stage3ThoughtImportArtifact.model_validate(payload)


def build_character_thought_point_records(
    artifact: Stage3ThoughtImportArtifact,
    require_review_approval: bool = True,
) -> list[PointRecord[CharacterThoughtDocument]]:
    """把已链接且通过复核的观点转换为 Qdrant point records。"""

    records: list[PointRecord[CharacterThoughtDocument]] = []
    for record in artifact.character_thoughts:
        if record.document is None or record.link_status == "unresolved" or record.validation_errors:
            continue
        if require_review_approval and record.review_status not in {"approved", "edited"}:
            continue
        document = CharacterThoughtDocument(**record.document.model_dump())
        records.append(PointRecord(point_id=record.point_id, document=document))
    return records


def upsert_stage3_thought_import_artifact(
    artifact: Stage3ThoughtImportArtifact,
    service: QdrantRagService,
    require_review_approval: bool = True,
) -> UpsertReport:
    """把合法且按配置通过复核的 Character Thought 写入 Qdrant。"""

    records = build_character_thought_point_records(
        artifact,
        require_review_approval=require_review_approval,
    )
    return service.upsert_character_thoughts(records)
