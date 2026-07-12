"""Stage 3：把场景级关系观察聚合为长期角色关系状态。"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
from typing import Callable

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
from .schemas import (
    CharacterRelationStatePayload,
    CharacterRelationStateReviewRecord,
    NormalizationIssue,
    RelationAggregationResponse,
    RelationObservationCandidate,
    RelationObservationReviewRecord,
    Stage2AnnotationArtifact,
    Stage2InputArtifact,
    Stage3RelationAggregationArtifact,
    Stage3RelationAggregationMetadata,
    UnmergedRelationObservationReviewRecord,
)
from .stage3_rag_import import _build_scene_anchor_map, resolve_character_id


RELATION_VISIBLE_TO_DEFAULT = 999999
DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "relation_aggregation_pass.jinja"


@dataclass(frozen=True, slots=True)
class _ObservationSource:
    """保存规范化关系观察及其原始角色名称。"""

    record: RelationObservationReviewRecord
    subject_character_name: str
    object_character_name: str


def build_stage3_relation_aggregation_artifact(
    input_artifact: Stage2InputArtifact,
    annotation_artifact: Stage2AnnotationArtifact,
    llm_config: LiteLLMConfig,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    status_callback: Callable[[str], None] | None = None,
) -> Stage3RelationAggregationArtifact:
    """聚合单份剧集输入，并复用跨剧集全量聚合实现。"""

    return build_stage3_relation_aggregation_artifact_from_many(
        input_artifacts=[input_artifact],
        annotation_artifacts=[annotation_artifact],
        llm_config=llm_config,
        template_path=template_path,
        status_callback=status_callback,
    )


def build_stage3_relation_aggregation_artifact_from_many(
    input_artifacts: list[Stage2InputArtifact],
    annotation_artifacts: list[Stage2AnnotationArtifact],
    llm_config: LiteLLMConfig,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    status_callback: Callable[[str], None] | None = None,
) -> Stage3RelationAggregationArtifact:
    """全量聚合同一系列、季度与剧情分支中的多集关系观察。"""

    if not input_artifacts:
        raise ValueError("input_artifacts 不能为空。")
    if len(input_artifacts) != len(annotation_artifacts):
        raise ValueError("input_artifacts 与 annotation_artifacts 数量必须一致。")
    _validate_common_scope(input_artifacts)

    issues: list[NormalizationIssue] = []
    sources = _collect_observation_sources(input_artifacts, annotation_artifacts, issues)
    reference_input = input_artifacts[0]
    grouped = _group_observation_sources(sources)

    state_records: list[CharacterRelationStateReviewRecord] = []
    unmerged_records: list[UnmergedRelationObservationReviewRecord] = []
    client = LiteLLMJsonClient(llm_config)
    ordered_groups = sorted(grouped.items(), key=lambda item: item[0])
    for group_index, ((subject_name, object_name), group_sources) in enumerate(ordered_groups, start=1):
        ordered_sources = sorted(group_sources, key=lambda source: source.record.time_order)
        _emit_status(
            status_callback,
            "[{}/{}] 正在聚合 {} -> {}（{} 条观察）".format(
                group_index,
                len(ordered_groups),
                subject_name,
                object_name,
                len(ordered_sources),
            ),
        )
        prompt = render_relation_aggregation_prompt(
            subject_character_name=subject_name,
            object_character_name=object_name,
            observations=[source.record for source in ordered_sources],
            template_path=template_path,
        )
        try:
            payload = client.complete_json(prompt)
            response = RelationAggregationResponse.model_validate(payload.parsed_payload)
        except (ValueError, ValidationError) as exc:
            issues.append(
                NormalizationIssue(
                    scene_id=ordered_sources[0].record.scene_id,
                    collection_name="character_relation_states",
                    candidate_local_id=f"{subject_name}->{object_name}",
                    message=f"关系聚合 LLM 输出无效: {type(exc).__name__}: {exc}",
                )
            )
            continue

        records, unmerged = _normalize_group_response(
            response=response,
            expected_subject_name=subject_name,
            expected_object_name=object_name,
            sources=ordered_sources,
            input_artifact=reference_input,
            issues=issues,
        )
        state_records.extend(records)
        unmerged_records.extend(unmerged)

    return _build_relation_aggregation_artifact(
        input_artifacts=input_artifacts,
        annotation_artifacts=annotation_artifacts,
        sources=sources,
        state_records=state_records,
        unmerged_records=unmerged_records,
        issues=issues,
        aggregation_model=llm_config.model,
    )


def _validate_common_scope(input_artifacts: list[Stage2InputArtifact]) -> None:
    """确保多集聚合输入属于同一系列、时间线与剧情分支。"""

    if not input_artifacts:
        raise ValueError("关系全量聚合至少需要一份 Stage 2 Input。")
    reference = input_artifacts[0].metadata
    for artifact in input_artifacts[1:]:
        metadata = artifact.metadata
        if (
            metadata.series_id != reference.series_id
            or metadata.timeline_id != reference.timeline_id
            or metadata.canon_branch != reference.canon_branch
        ):
            raise ValueError("关系全量聚合只接受同一系列、时间线与剧情分支的输入。")


def render_relation_aggregation_prompt(
    subject_character_name: str,
    object_character_name: str,
    observations: list[RelationObservationReviewRecord],
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
) -> str:
    """渲染单个有向角色对的关系聚合 Prompt。"""

    resolved_template_path = Path(template_path)
    environment = Environment(
        loader=FileSystemLoader(str(resolved_template_path.parent)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    template = environment.get_template(resolved_template_path.name)
    return template.render(
        subject_character_name=subject_character_name,
        object_character_name=object_character_name,
        observations=[observation.model_dump(mode="json") for observation in observations],
    )


def save_stage3_relation_aggregation_artifact(
    artifact: Stage3RelationAggregationArtifact,
    output_path: str | Path,
) -> None:
    """保存角色关系聚合审查产物。"""

    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_stage3_relation_aggregation_artifact(
    input_path: str | Path,
) -> Stage3RelationAggregationArtifact:
    """读取角色关系聚合审查产物。"""

    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return Stage3RelationAggregationArtifact.model_validate(payload)


def prepare_stage3_relation_prompt_package(
    input_artifacts: list[Stage2InputArtifact],
    annotation_artifacts: list[Stage2AnnotationArtifact],
    input_paths: list[str | Path],
    annotation_paths: list[str | Path],
    output_dir: str | Path,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
) -> PromptPackageManifest:
    """按有向角色对生成跨场景关系聚合静态任务包。"""

    if len(input_paths) != len(input_artifacts) or len(annotation_paths) != len(annotation_artifacts):
        raise ValueError("关系聚合 artifact 与来源路径数量必须一致。")
    _validate_common_scope(input_artifacts)
    issues: list[NormalizationIssue] = []
    sources = _collect_observation_sources(input_artifacts, annotation_artifacts, issues)
    grouped = _group_observation_sources(sources)
    resolved_template_path = Path(template_path).resolve()
    prepared_prompts: list[PreparedPrompt] = []
    for (subject_name, object_name), group_sources in sorted(grouped.items()):
        ordered_sources = sorted(group_sources, key=lambda source: source.record.time_order)
        subject_id = ordered_sources[0].record.subject_character_id.value
        object_id = ordered_sources[0].record.object_character_id.value
        prepared_prompts.append(
            PreparedPrompt(
                task_id=f"relation_pair:{subject_id}:{object_id}",
                prompt=render_relation_aggregation_prompt(
                    subject_character_name=subject_name,
                    object_character_name=object_name,
                    observations=[source.record for source in ordered_sources],
                    template_path=resolved_template_path,
                ),
                context={
                    "subject_character_name": subject_name,
                    "object_character_name": object_name,
                    "observation_count": str(len(ordered_sources)),
                },
            )
        )
    return create_prompt_package(
        output_dir=output_dir,
        stage_kind="stage3_relation",
        prompts=prepared_prompts,
        source_paths=[*input_paths, *annotation_paths],
        template_paths=[resolved_template_path],
        parameters={
            "input_paths": json.dumps([str(Path(path).resolve()) for path in input_paths]),
            "annotation_paths": json.dumps(
                [str(Path(path).resolve()) for path in annotation_paths]
            ),
            "template_path": str(resolved_template_path),
            "preparation_issue_count": str(len(issues)),
        },
    )


def assemble_stage3_relation_prompt_package(
    manifest_path: str | Path,
    model_label: str = "codex-workspace",
    allow_partial: bool = False,
    allow_stale: bool = False,
) -> Stage3RelationAggregationArtifact:
    """校验角色对 response 并生成带时间窗口和风险的关系审查产物。"""

    manifest = load_prompt_package_manifest(manifest_path)
    if manifest.stage_kind != "stage3_relation":
        raise PromptPackageError("manifest 不是 Stage 3 Relation 任务包。")
    input_paths = _load_manifest_path_list(manifest.parameters, "input_paths")
    annotation_paths = _load_manifest_path_list(manifest.parameters, "annotation_paths")
    if len(input_paths) != len(annotation_paths):
        raise PromptPackageError("关系聚合 manifest 的输入与标注路径数量不一致。")
    from .stage2_document_extraction import load_stage2_annotation_artifact
    from .stage2_input_builder import load_stage2_input_artifact

    input_artifacts = [load_stage2_input_artifact(path) for path in input_paths]
    annotation_artifacts = [load_stage2_annotation_artifact(path) for path in annotation_paths]
    _validate_common_scope(input_artifacts)
    issues: list[NormalizationIssue] = []
    sources = _collect_observation_sources(input_artifacts, annotation_artifacts, issues)
    grouped = _group_observation_sources(sources)
    response_bundle = read_prompt_package_responses(
        manifest_path,
        allow_partial=allow_partial,
        allow_stale=allow_stale,
    )
    reference_input = input_artifacts[0]
    state_records: list[CharacterRelationStateReviewRecord] = []
    unmerged_records: list[UnmergedRelationObservationReviewRecord] = []
    for task in manifest.tasks:
        subject_name = task.context.get("subject_character_name", "")
        object_name = task.context.get("object_character_name", "")
        group_sources = grouped.get((subject_name, object_name), [])
        raw_response = response_bundle.responses.get(task.task_id)
        if not group_sources:
            issues.append(
                NormalizationIssue(
                    scene_id="",
                    collection_name="character_relation_states",
                    candidate_local_id=task.task_id,
                    message="manifest 角色对在当前 Observation 中不存在。",
                )
            )
            continue
        if raw_response is None:
            issues.append(
                NormalizationIssue(
                    scene_id=group_sources[0].record.scene_id,
                    collection_name="character_relation_states",
                    candidate_local_id=task.task_id,
                    message="缺少关系聚合 response。",
                )
            )
            continue
        try:
            response = RelationAggregationResponse.model_validate(
                parse_json_response(raw_response)
            )
            records, unmerged = _normalize_group_response(
                response=response,
                expected_subject_name=subject_name,
                expected_object_name=object_name,
                sources=sorted(group_sources, key=lambda source: source.record.time_order),
                input_artifact=reference_input,
                issues=issues,
            )
            state_records.extend(records)
            unmerged_records.extend(unmerged)
        except Exception as exc:
            issues.append(
                NormalizationIssue(
                    scene_id=group_sources[0].record.scene_id,
                    collection_name="character_relation_states",
                    candidate_local_id=task.task_id,
                    message=f"关系聚合 response 无效: {type(exc).__name__}: {exc}",
                )
            )
    return _build_relation_aggregation_artifact(
        input_artifacts=input_artifacts,
        annotation_artifacts=annotation_artifacts,
        sources=sources,
        state_records=state_records,
        unmerged_records=unmerged_records,
        issues=issues,
        aggregation_model=model_label,
    )


def _collect_observation_sources(
    input_artifacts: list[Stage2InputArtifact],
    annotation_artifacts: list[Stage2AnnotationArtifact],
    issues: list[NormalizationIssue],
) -> list[_ObservationSource]:
    """跨多集规范化并排序所有关系观察。"""

    if len(input_artifacts) != len(annotation_artifacts):
        raise ValueError("Stage 2 Input 与 Stage 2A 标注数量必须一致。")
    sources: list[_ObservationSource] = []
    for input_artifact, annotation_artifact in zip(input_artifacts, annotation_artifacts):
        sources.extend(_normalize_observations(input_artifact, annotation_artifact, issues))
    return sorted(sources, key=lambda source: (source.record.time_order, source.record.observation_id))


def _group_observation_sources(
    sources: list[_ObservationSource],
) -> dict[tuple[str, str], list[_ObservationSource]]:
    """按有向角色名称对分组关系观察。"""

    grouped: dict[tuple[str, str], list[_ObservationSource]] = defaultdict(list)
    for source in sources:
        grouped[(source.subject_character_name, source.object_character_name)].append(source)
    return grouped


def _build_relation_aggregation_artifact(
    input_artifacts: list[Stage2InputArtifact],
    annotation_artifacts: list[Stage2AnnotationArtifact],
    sources: list[_ObservationSource],
    state_records: list[CharacterRelationStateReviewRecord],
    unmerged_records: list[UnmergedRelationObservationReviewRecord],
    issues: list[NormalizationIssue],
    aggregation_model: str,
) -> Stage3RelationAggregationArtifact:
    """根据已完成的关系聚合结果组装最终审查产物。"""

    reference_input = input_artifacts[0]
    return Stage3RelationAggregationArtifact(
        metadata=Stage3RelationAggregationMetadata(
            anime_title=reference_input.metadata.anime_title,
            series_id=reference_input.metadata.series_id,
            timeline_id=reference_input.metadata.timeline_id,
            story_year=reference_input.metadata.story_year,
            canon_branch=reference_input.metadata.canon_branch,
            episodes=sorted({artifact.metadata.episode for artifact in input_artifacts}),
            subtitle_paths=list(
                dict.fromkeys(artifact.metadata.subtitle_path for artifact in input_artifacts)
            ),
        ),
        source_stage2_models=list(dict.fromkeys(artifact.model for artifact in annotation_artifacts)),
        aggregation_model=aggregation_model,
        observations=[source.record for source in sources],
        character_relation_states=state_records,
        unmerged_observations=unmerged_records,
        issues=issues,
    )


def _load_manifest_path_list(parameters: dict[str, str], key: str) -> list[str]:
    """从 manifest 参数中读取字符串路径列表。"""

    raw_value = parameters.get(key)
    if raw_value is None:
        raise PromptPackageError(f"manifest 缺少参数: {key}")
    payload = json.loads(raw_value)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise PromptPackageError(f"manifest 参数 {key} 不是字符串列表。")
    return list(payload)


def _normalize_observations(
    input_artifact: Stage2InputArtifact,
    annotation_artifact: Stage2AnnotationArtifact,
    issues: list[NormalizationIssue],
) -> list[_ObservationSource]:
    """规范化 Stage 2 场景观察并补充剧情顺序与角色 ID。"""

    scene_map = {scene.scene_id: scene for scene in input_artifact.scenes}
    scene_time_map = _build_scene_anchor_map(input_artifact, annotation_artifact, issues)
    sources: list[_ObservationSource] = []
    seen_observation_ids: set[str] = set()
    for result in annotation_artifact.results:
        if result.annotation is None:
            continue
        scene = scene_map.get(result.scene_id)
        if scene is None:
            issues.append(
                NormalizationIssue(
                    scene_id=result.scene_id,
                    collection_name="relation_observations",
                    candidate_local_id="scene",
                    message="Stage 2 标注引用了不存在的场景。",
                )
            )
            continue
        utterance_ids = {utterance.u_id for utterance in scene.utterances}
        for observation in result.annotation.relation_observations:
            source = _normalize_observation(
                observation=observation,
                scene_id=scene.scene_id,
                time_order=scene_time_map[scene.scene_id],
                utterance_ids=utterance_ids,
                input_artifact=input_artifact,
                issues=issues,
            )
            if source is None:
                continue
            if source.record.observation_id in seen_observation_ids:
                issues.append(
                    NormalizationIssue(
                        scene_id=scene.scene_id,
                        collection_name="relation_observations",
                        candidate_local_id=observation.observation_local_id,
                        message=f"发现重复 observation_id: {source.record.observation_id}",
                    )
                )
                continue
            seen_observation_ids.add(source.record.observation_id)
            sources.append(source)
    return sorted(sources, key=lambda source: (source.record.time_order, source.record.observation_id))


def _normalize_observation(
    observation: RelationObservationCandidate,
    scene_id: str,
    time_order: int,
    utterance_ids: set[str],
    input_artifact: Stage2InputArtifact,
    issues: list[NormalizationIssue],
) -> _ObservationSource | None:
    """把单条场景关系观察转换为审查记录。"""

    invalid_evidence_ids = [item for item in observation.evidence_u_ids if item not in utterance_ids]
    valid_evidence_ids = [item for item in observation.evidence_u_ids if item in utterance_ids]
    if invalid_evidence_ids:
        issues.append(
            NormalizationIssue(
                scene_id=scene_id,
                collection_name="relation_observations",
                candidate_local_id=observation.observation_local_id,
                message=f"发现不存在的 evidence_u_ids: {', '.join(invalid_evidence_ids)}",
            )
        )
    try:
        subject_character_id = resolve_character_id(observation.subject_character_name)
        object_character_id = resolve_character_id(observation.object_character_name)
    except ValueError as exc:
        issues.append(
            NormalizationIssue(
                scene_id=scene_id,
                collection_name="relation_observations",
                candidate_local_id=observation.observation_local_id,
                message=str(exc),
            )
        )
        return None

    observation_id = _stable_id(
        "relation_observation",
        input_artifact.metadata.series_id,
        input_artifact.metadata.timeline_id,
        input_artifact.metadata.canon_branch,
        scene_id,
        observation.observation_local_id,
        subject_character_id.value,
        object_character_id.value,
    )
    return _ObservationSource(
        record=RelationObservationReviewRecord(
            observation_id=observation_id,
            scene_id=scene_id,
            time_order=time_order,
            subject_character_id=subject_character_id,
            object_character_id=object_character_id,
            observation_text=observation.observation_text.strip(),
            speech_hint=observation.speech_hint.strip(),
            object_character_nickname=observation.object_character_nickname.strip(),
            evidence_u_ids=valid_evidence_ids,
            evidence_strength=observation.evidence_strength,
            confidence=observation.confidence,
            ambiguity_notes=observation.ambiguity_notes.strip(),
        ),
        subject_character_name=observation.subject_character_name,
        object_character_name=observation.object_character_name,
    )


def _normalize_group_response(
    response: RelationAggregationResponse,
    expected_subject_name: str,
    expected_object_name: str,
    sources: list[_ObservationSource],
    input_artifact: Stage2InputArtifact,
    issues: list[NormalizationIssue],
) -> tuple[list[CharacterRelationStateReviewRecord], list[UnmergedRelationObservationReviewRecord]]:
    """校验一个角色对的聚合响应并生成状态时间线。"""

    first_scene_id = sources[0].record.scene_id
    pair_label = f"{expected_subject_name}->{expected_object_name}"
    if (
        response.subject_character_name != expected_subject_name
        or response.object_character_name != expected_object_name
    ):
        issues.append(
            NormalizationIssue(
                scene_id=first_scene_id,
                collection_name="character_relation_states",
                candidate_local_id=pair_label,
                message="聚合响应的主客体与输入角色对不一致，已按输入角色对规范化。",
            )
        )

    source_map = {source.record.observation_id: source for source in sources}
    subject_character_id = sources[0].record.subject_character_id
    object_character_id = sources[0].record.object_character_id
    records: list[CharacterRelationStateReviewRecord] = []
    assigned_ids: list[str] = []
    for proposal_index, proposal in enumerate(response.states, start=1):
        supporting_ids = list(dict.fromkeys(proposal.supporting_observation_ids))
        missing_ids = [observation_id for observation_id in supporting_ids if observation_id not in source_map]
        valid_ids = [observation_id for observation_id in supporting_ids if observation_id in source_map]
        validation_errors: list[str] = []
        if missing_ids:
            validation_errors.append(f"引用了不存在的 Observation: {', '.join(missing_ids)}")
        if not valid_ids:
            validation_errors.append("State 没有有效的 supporting_observation_ids。")

        relation_type_key = re.sub(r"[\s-]+", "_", proposal.relation_type_key.strip().casefold())
        if not relation_type_key:
            validation_errors.append("relation_type_key 不能为空。")
            relation_type_key = f"invalid_type_{proposal_index}"
        elif re.fullmatch(r"[a-z0-9_]+", relation_type_key) is None:
            validation_errors.append("relation_type_key 必须是英文 snake_case。")
        if not proposal.summary.strip():
            validation_errors.append("State summary 不能为空。")
        visible_from = min(
            (source_map[observation_id].record.time_order for observation_id in valid_ids),
            default=0,
        )
        state_id = _stable_id(
            "character_relation_state",
            input_artifact.metadata.series_id,
            input_artifact.metadata.timeline_id,
            input_artifact.metadata.canon_branch,
            subject_character_id.value,
            object_character_id.value,
            relation_type_key,
            str(visible_from),
        )
        document = None
        if valid_ids:
            document = CharacterRelationStatePayload(
                subject_character_id=subject_character_id,
                object_character_id=object_character_id,
                timeline_id=input_artifact.metadata.timeline_id,
                series_id=input_artifact.metadata.series_id,
                visible_from=visible_from,
                visible_to=RELATION_VISIBLE_TO_DEFAULT,
                canon_branch=input_artifact.metadata.canon_branch,
                relation_type_key=relation_type_key,
                summary=proposal.summary.strip(),
                speech_hint=proposal.speech_hint.strip(),
                object_character_nickname=proposal.object_character_nickname.strip(),
            )
        records.append(
            CharacterRelationStateReviewRecord(
                state_id=state_id,
                supporting_observation_ids=valid_ids,
                confidence=proposal.confidence,
                ambiguity_notes=proposal.ambiguity_notes.strip(),
                validation_errors=validation_errors,
                document=document,
            )
        )
        assigned_ids.extend(valid_ids)

    _backfill_state_windows(records)
    _validate_state_support_windows(records, source_map)
    _apply_state_risks(records, source_map, Counter(assigned_ids))
    unmerged_records = _normalize_unmerged_observations(
        response=response,
        source_map=source_map,
        assigned_ids=set(assigned_ids),
        issues=issues,
        pair_label=pair_label,
    )
    return records, unmerged_records


def _backfill_state_windows(records: list[CharacterRelationStateReviewRecord]) -> None:
    """按关系语义键回填相邻长期状态的结束时间。"""

    grouped: dict[str, list[CharacterRelationStateReviewRecord]] = defaultdict(list)
    for record in records:
        if record.document is not None:
            grouped[record.document.relation_type_key].append(record)

    for group in grouped.values():
        ordered = sorted(group, key=lambda record: record.document.visible_from if record.document else 0)
        for current_record, next_record in zip(ordered, ordered[1:]):
            if current_record.document is None or next_record.document is None:
                continue
            if next_record.document.visible_from <= current_record.document.visible_from:
                message = "同一 relation_type_key 的相邻 State 起始时间没有递增。"
                current_record.validation_errors.append(message)
                next_record.validation_errors.append(message)
                continue
            current_record.document.visible_to = next_record.document.visible_from - 1


def _validate_state_support_windows(
    records: list[CharacterRelationStateReviewRecord],
    source_map: dict[str, _ObservationSource],
) -> None:
    """确保每条支持观察都落在聚合后 State 的有效区间内。"""

    for record in records:
        if record.document is None:
            continue
        out_of_window_ids = [
            observation_id
            for observation_id in record.supporting_observation_ids
            if observation_id in source_map
            and not (
                record.document.visible_from
                <= source_map[observation_id].record.time_order
                <= record.document.visible_to
            )
        ]
        if out_of_window_ids:
            record.validation_errors.append(
                "支持 Observation 落在 State 有效区间之外: " + ", ".join(out_of_window_ids)
            )


def _apply_state_risks(
    records: list[CharacterRelationStateReviewRecord],
    source_map: dict[str, _ObservationSource],
    assignment_counts: Counter[str],
) -> None:
    """为关系状态计算不阻断人工审核的高风险标记。"""

    type_keys = {
        record.document.relation_type_key
        for record in records
        if record.document is not None
    }
    over_split = len(type_keys) > 3
    grouped: dict[str, list[CharacterRelationStateReviewRecord]] = defaultdict(list)
    for record in records:
        risk_reasons: list[str] = []
        if record.confidence < 0.7:
            risk_reasons.append("聚合置信度低于 0.7。")
        if record.ambiguity_notes:
            risk_reasons.append("聚合结果包含歧义说明。")
        if record.validation_errors:
            risk_reasons.append("存在阻断校验错误。")
        if over_split:
            risk_reasons.append("同一有向角色对生成了超过 3 个 relation_type_key。")
        supporting_sources = [
            source_map[observation_id]
            for observation_id in record.supporting_observation_ids
            if observation_id in source_map
        ]
        if len(supporting_sources) == 1:
            only_source = supporting_sources[0].record
            if only_source.evidence_strength == "inferred" or only_source.confidence < 0.7:
                risk_reasons.append("State 仅由一条弱证据 Observation 支持。")
        if supporting_sources:
            boundary_source = min(supporting_sources, key=lambda source: source.record.time_order).record
            if boundary_source.evidence_strength == "inferred":
                risk_reasons.append("State 变化边界来自推断证据。")
        if any(assignment_counts[observation_id] > 2 for observation_id in record.supporting_observation_ids):
            risk_reasons.append("存在同时支持超过 2 个关系状态的 Observation。")
        record.risk_reasons = list(dict.fromkeys(risk_reasons))
        record.risk_level = "high" if record.risk_reasons else "low"
        if record.document is not None:
            grouped[record.document.relation_type_key].append(record)

    for group in grouped.values():
        ordered = sorted(group, key=lambda record: record.document.visible_from if record.document else 0)
        for previous_record, current_record in zip(ordered, ordered[1:]):
            if previous_record.document is None or current_record.document is None:
                continue
            similarity = SequenceMatcher(
                None,
                previous_record.document.summary,
                current_record.document.summary,
            ).ratio()
            if similarity >= 0.85:
                reason = "相邻同类型 State 摘要高度相似，可能是不必要的切分。"
                previous_record.risk_reasons.append(reason)
                current_record.risk_reasons.append(reason)
                previous_record.risk_level = "high"
                current_record.risk_level = "high"
            if (
                previous_record.document.speech_hint != current_record.document.speech_hint
                or previous_record.document.object_character_nickname
                != current_record.document.object_character_nickname
            ):
                boundary_ids = current_record.supporting_observation_ids
                boundary_sources = [source_map[item].record for item in boundary_ids if item in source_map]
                if boundary_sources and min(
                    boundary_sources,
                    key=lambda observation: observation.time_order,
                ).evidence_strength == "inferred":
                    current_record.risk_reasons.append("称呼或说话方式变化仅由间接证据支持。")
                    current_record.risk_reasons = list(dict.fromkeys(current_record.risk_reasons))
                    current_record.risk_level = "high"


def _normalize_unmerged_observations(
    response: RelationAggregationResponse,
    source_map: dict[str, _ObservationSource],
    assigned_ids: set[str],
    issues: list[NormalizationIssue],
    pair_label: str,
) -> list[UnmergedRelationObservationReviewRecord]:
    """规范化未提升为长期关系状态的场景观察。"""

    records: list[UnmergedRelationObservationReviewRecord] = []
    explicitly_unmerged: set[str] = set()
    for item in response.unmerged_observations:
        if item.observation_id not in source_map:
            issues.append(
                NormalizationIssue(
                    scene_id="",
                    collection_name="relation_observations",
                    candidate_local_id=pair_label,
                    message=f"未合并列表引用了不存在的 Observation: {item.observation_id}",
                )
            )
            continue
        explicitly_unmerged.add(item.observation_id)
        reasons: list[str] = []
        if not item.reason.strip():
            reasons.append("未合并 Observation 没有提供原因。")
        if item.observation_id in assigned_ids:
            reasons.append("Observation 同时被 State 使用并被标记为未合并。")
        records.append(
            UnmergedRelationObservationReviewRecord(
                observation_id=item.observation_id,
                reason=item.reason.strip(),
                risk_level="high" if reasons else "low",
                risk_reasons=reasons,
            )
        )

    for observation_id in source_map:
        if observation_id in assigned_ids or observation_id in explicitly_unmerged:
            continue
        records.append(
            UnmergedRelationObservationReviewRecord(
                observation_id=observation_id,
                reason="",
                risk_level="high",
                risk_reasons=["Observation 未进入任何 State，且模型没有提供未合并原因。"],
            )
        )
    return records


def _stable_id(prefix: str, *parts: str) -> str:
    """根据稳定输入生成长度适中的可重复标识。"""

    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _emit_status(callback: Callable[[str], None] | None, message: str) -> None:
    """在调用方提供状态回调时发送进度消息。"""

    if callback is not None:
        callback(message)
