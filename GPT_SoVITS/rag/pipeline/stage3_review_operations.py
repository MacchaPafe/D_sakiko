"""定义 CLI 与图形审核工作台共享的类型化审核操作。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from pydantic import BaseModel

from .review_models import (
    Disposition,
    DispositionReasonCode,
    ReviewFields,
    complete_review,
    reset_review_fields,
)
from .schemas import LoreEntryPayload, StoryEventPayload
from .stage3_document_models import (
    LoreEntryReviewRecord,
    Stage3DocumentReviewArtifact,
    StoryEventReviewRecord,
)
from .stage3_lore_models import LoreDedupAction, Stage3LoreDecisionsArtifact
from .stage3_relation_models import (
    RelationTypeContentDraft,
    RelationTypeReviewRecord,
    Stage3RelationReviewArtifact,
)
from .stage3_thought_models import (
    Stage3ThoughtReviewArtifact,
    ThoughtThreadContentDraft,
    ThoughtThreadReviewRecord,
)


ReviewArtifact: TypeAlias = (
    Stage3DocumentReviewArtifact
    | Stage3RelationReviewArtifact
    | Stage3ThoughtReviewArtifact
    | Stage3LoreDecisionsArtifact
)
OrdinaryReviewArtifact: TypeAlias = (
    Stage3DocumentReviewArtifact
    | Stage3RelationReviewArtifact
    | Stage3ThoughtReviewArtifact
)
IdentityReviewItem: TypeAlias = (
    StoryEventReviewRecord
    | LoreEntryReviewRecord
    | RelationTypeReviewRecord
    | ThoughtThreadReviewRecord
)


@dataclass(frozen=True, slots=True)
class CompleteItemReviewCommand:
    """表示完成一个普通审核单位的最终处置。"""

    item_id: str
    disposition: Disposition
    reason_code: DispositionReasonCode | None = None
    reason_note: str | None = None
    review_notes: str | None = None


@dataclass(frozen=True, slots=True)
class BatchCompleteReviewCommand:
    """表示把显式选择的多个审核单位批量审核通过。"""

    item_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarkItemFollowupCommand:
    """表示把一个普通审核单位标记为需要继续处理。"""

    item_id: str
    review_notes: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateItemNotesCommand:
    """表示只更新审核备注。"""

    item_id: str
    review_notes: str | None


@dataclass(frozen=True, slots=True)
class ReplaceStoryDocumentCommand:
    """表示替换 Story Event 的完整人工文档。"""

    item_id: str
    content: StoryEventPayload


@dataclass(frozen=True, slots=True)
class ReplaceLoreDocumentCommand:
    """表示替换 Lore Entry 的完整人工文档。"""

    item_id: str
    content: LoreEntryPayload


@dataclass(frozen=True, slots=True)
class ReplaceRelationContentCommand:
    """表示替换 Relation Type 的完整人工内容。"""

    item_id: str
    content: RelationTypeContentDraft


@dataclass(frozen=True, slots=True)
class ReplaceThoughtContentCommand:
    """表示替换 Thought Thread 的完整人工内容。"""

    item_id: str
    content: ThoughtThreadContentDraft


@dataclass(frozen=True, slots=True)
class RestoreGeneratedContentCommand:
    """表示删除人工快照并恢复机器基准。"""

    item_id: str


@dataclass(frozen=True, slots=True)
class AssignRelationObservationCommand:
    """表示把一个 Observation 移入目标 Relation State。"""

    observation_id: str
    target_type_id: str
    target_state_index: int


@dataclass(frozen=True, slots=True)
class AssignThoughtUpdateCommand:
    """表示把一个 Thought Update 移入目标 Thought State。"""

    update_id: str
    target_thread_id: str
    target_state_index: int


@dataclass(frozen=True, slots=True)
class SplitRelationTypeCommand:
    """表示在指定 State 位置拆分 Relation Type。"""

    item_id: str
    split_index: int
    new_type_id: str
    new_semantic_label: str


@dataclass(frozen=True, slots=True)
class MergeRelationTypesCommand:
    """表示把两个同向 Relation Type 合并到主 Type。"""

    primary_type_id: str
    merged_type_id: str
    semantic_label: str


@dataclass(frozen=True, slots=True)
class SplitThoughtThreadCommand:
    """表示在指定 State 位置拆分 Thought Thread。"""

    item_id: str
    split_index: int
    new_thread_id: str
    new_canonical_subject: str
    new_thought_aspect: str


@dataclass(frozen=True, slots=True)
class MergeThoughtThreadsCommand:
    """表示把同一角色的两个 Thought Thread 合并到主 Thread。"""

    primary_thread_id: str
    merged_thread_id: str
    canonical_subject: str
    thought_aspect: str


@dataclass(frozen=True, slots=True)
class ResolveIdentityCommand:
    """表示确认一个候选是否继承上一版开发侧身份。"""

    item_id: str
    choice: Literal["inherit", "new"]
    previous_id: str | None = None


@dataclass(frozen=True, slots=True)
class CompleteLoreDecisionCommand:
    """表示完成一个 Lore 重复组的人工决定。"""

    group_id: str
    action: LoreDedupAction
    primary_candidate_id: str | None = None
    reviewed_document: LoreEntryPayload | None = None
    review_notes: str | None = None


@dataclass(frozen=True, slots=True)
class ClearLoreDecisionCommand:
    """表示清除一个人工 Lore 决定并恢复待处理状态。"""

    group_id: str


ReviewCommand: TypeAlias = (
    CompleteItemReviewCommand
    | BatchCompleteReviewCommand
    | MarkItemFollowupCommand
    | UpdateItemNotesCommand
    | ReplaceStoryDocumentCommand
    | ReplaceLoreDocumentCommand
    | ReplaceRelationContentCommand
    | ReplaceThoughtContentCommand
    | RestoreGeneratedContentCommand
    | AssignRelationObservationCommand
    | AssignThoughtUpdateCommand
    | SplitRelationTypeCommand
    | MergeRelationTypesCommand
    | SplitThoughtThreadCommand
    | MergeThoughtThreadsCommand
    | ResolveIdentityCommand
    | CompleteLoreDecisionCommand
    | ClearLoreDecisionCommand
)


def apply_review_command(
    artifact: ReviewArtifact,
    command: ReviewCommand,
) -> ReviewArtifact:
    """在深拷贝上执行一个审核命令并返回完整校验后的 artifact。"""

    candidate = artifact.model_copy(deep=True)
    if isinstance(command, CompleteLoreDecisionCommand):
        _complete_lore_decision(candidate, command)
    elif isinstance(command, ClearLoreDecisionCommand):
        _clear_lore_decision(candidate, command)
    else:
        ordinary = _require_ordinary_artifact(candidate)
        if isinstance(command, CompleteItemReviewCommand):
            item = find_review_item(ordinary, command.item_id)
            _validate_completion(ordinary, item, command.disposition)
            if command.review_notes is not None:
                item.review_notes = command.review_notes
            complete_review(
                item,
                command.disposition,
                command.reason_code,
                command.reason_note,
            )
        elif isinstance(command, BatchCompleteReviewCommand):
            _batch_complete_review(ordinary, command)
        elif isinstance(command, MarkItemFollowupCommand):
            item = find_review_item(ordinary, command.item_id)
            reset_review_fields(item)
            item.review_status = "needs_followup"
            if command.review_notes is not None:
                item.review_notes = command.review_notes
        elif isinstance(command, UpdateItemNotesCommand):
            find_review_item(ordinary, command.item_id).review_notes = command.review_notes
        elif isinstance(command, ReplaceStoryDocumentCommand):
            _replace_story_document(ordinary, command)
        elif isinstance(command, ReplaceLoreDocumentCommand):
            _replace_lore_document(ordinary, command)
        elif isinstance(command, ReplaceRelationContentCommand):
            _replace_relation_content(ordinary, command)
        elif isinstance(command, ReplaceThoughtContentCommand):
            _replace_thought_content(ordinary, command)
        elif isinstance(command, RestoreGeneratedContentCommand):
            _restore_generated_content(ordinary, command.item_id)
        elif isinstance(command, AssignRelationObservationCommand):
            _assign_relation_observation(ordinary, command)
        elif isinstance(command, AssignThoughtUpdateCommand):
            _assign_thought_update(ordinary, command)
        elif isinstance(command, SplitRelationTypeCommand):
            _split_relation_type(ordinary, command)
        elif isinstance(command, MergeRelationTypesCommand):
            _merge_relation_types(ordinary, command)
        elif isinstance(command, SplitThoughtThreadCommand):
            _split_thought_thread(ordinary, command)
        elif isinstance(command, MergeThoughtThreadsCommand):
            _merge_thought_threads(ordinary, command)
        elif isinstance(command, ResolveIdentityCommand):
            _resolve_identity(ordinary, command)
    return _validate_artifact(candidate)


def find_review_item(
    artifact: OrdinaryReviewArtifact,
    item_id: str,
) -> ReviewFields:
    """按开发侧稳定 ID 查找普通审核单位。"""

    if isinstance(artifact, Stage3DocumentReviewArtifact):
        items: list[ReviewFields] = [*artifact.story_events, *artifact.lore_entries]
    elif isinstance(artifact, Stage3RelationReviewArtifact):
        items = [*artifact.relation_types, *artifact.unmerged_observations]
    else:
        items = [*artifact.threads, *artifact.unassigned_updates]
    for item in items:
        identifiers = {
            str(getattr(item, field_name))
            for field_name in (
                "candidate_id",
                "relation_type_id",
                "observation_id",
                "thought_thread_id",
                "update_id",
            )
            if hasattr(item, field_name)
        }
        if item_id in identifiers:
            return item
    raise KeyError(f"未找到审核项: {item_id}")


def _require_ordinary_artifact(artifact: ReviewArtifact) -> OrdinaryReviewArtifact:
    """要求命令目标是普通审核 artifact。"""

    if isinstance(artifact, Stage3LoreDecisionsArtifact):
        raise ValueError("该审核命令不适用于 Lore decisions")
    return artifact


def _batch_complete_review(
    artifact: OrdinaryReviewArtifact,
    command: BatchCompleteReviewCommand,
) -> None:
    """批量完成显式选择且没有阻断问题的普通审核单位。"""

    if not command.item_ids:
        raise ValueError("批量审核必须显式选择至少一个审核项")
    if len(command.item_ids) != len(set(command.item_ids)):
        raise ValueError("批量审核项不得重复")
    selected = [find_review_item(artifact, item_id) for item_id in command.item_ids]
    for item in selected:
        if item.review_status == "needs_followup":
            raise ValueError("needs_followup 审核项不能批量通过")
        if item.risk_level == "high" or item.risk_reasons:
            raise ValueError("存在风险提示的审核项不能批量通过")
        if isinstance(item, (RelationTypeReviewRecord, ThoughtThreadReviewRecord)):
            if item.identity_suggestions:
                raise ValueError("身份待确认的序列不能批量通过")
        _validate_completion(artifact, item, "publish")
    for item in selected:
        complete_review(item, "publish")


def _validate_completion(
    artifact: OrdinaryReviewArtifact,
    item: ReviewFields,
    disposition: Disposition,
) -> None:
    """在完成发布处置前校验当前审核单位的局部发布不变量。"""

    if disposition != "publish":
        return
    if isinstance(item, (StoryEventReviewRecord, LoreEntryReviewRecord)):
        if item.identity_suggestions:
            raise ValueError("身份继承尚未确认，不能审核发布")
        return
    if isinstance(item, RelationTypeReviewRecord):
        if not isinstance(artifact, Stage3RelationReviewArtifact):
            raise TypeError("Relation Type 所属 artifact 类型错误")
        if item.identity_suggestions:
            raise ValueError("Relation Type 身份继承尚未确认")
        states = item.effective_content().states
        if not states:
            raise ValueError("发布的 Relation Type 不得为空")
        _validate_windows(
            item.relation_type_id,
            [(state.visible_from, state.visible_to) for state in states],
        )
        valid_observations = {
            observation.observation_id: observation
            for observation in artifact.observations
        }
        assigned = [
            observation_id
            for state in states
            for observation_id in state.supporting_observation_ids
        ]
        if len(assigned) != len(set(assigned)):
            raise ValueError("同一 Relation Type 内的 Observation 不得重复覆盖")
        coverage_counts = {
            observation_id: sum(
                observation_id in state.supporting_observation_ids
                for record in artifact.relation_types
                for state in record.effective_content().states
            )
            + sum(
                decision.observation_id == observation_id
                for decision in artifact.unmerged_observations
            )
            for observation_id in assigned
        }
        if any(count != 1 for count in coverage_counts.values()):
            raise ValueError("Relation Observation 必须恰好由一个 Type 或未归属决定覆盖")
        for observation_id in assigned:
            observation = valid_observations.get(observation_id)
            if observation is None:
                raise ValueError(f"Relation State 引用了未知 Observation: {observation_id}")
            if (
                observation.subject_character_id != item.subject_character_id
                or observation.object_character_id != item.object_character_id
            ):
                raise ValueError("Relation State 包含其他有向角色对的 Observation")
        return
    if isinstance(item, ThoughtThreadReviewRecord):
        if not isinstance(artifact, Stage3ThoughtReviewArtifact):
            raise TypeError("Thought Thread 所属 artifact 类型错误")
        if item.identity_suggestions:
            raise ValueError("Thought Thread 身份继承尚未确认")
        states = item.effective_content().states
        if not states:
            raise ValueError("发布的 Thought Thread 不得为空")
        _validate_windows(
            item.thought_thread_id,
            [(state.visible_from, state.visible_to) for state in states],
        )
        valid_updates = {update.update_id: update for update in artifact.updates}
        assigned = [
            update_id
            for state in states
            for update_id in state.supporting_update_ids
        ]
        if len(assigned) != len(set(assigned)):
            raise ValueError("同一 Thought Thread 内的 Update 不得重复覆盖")
        coverage_counts = {
            update_id: sum(
                update_id in state.supporting_update_ids
                for record in artifact.threads
                for state in record.effective_content().states
            )
            + sum(
                decision.update_id == update_id
                for decision in artifact.unassigned_updates
            )
            for update_id in assigned
        }
        if any(count != 1 for count in coverage_counts.values()):
            raise ValueError("Thought Update 必须恰好由一个 Thread 或未归属决定覆盖")
        valid_fact_ids = {
            str(fact.get("fact_id", ""))
            for fact in artifact.event_facts
            if fact.get("fact_id")
        }
        for state in states:
            for update_id in state.supporting_update_ids:
                update = valid_updates.get(update_id)
                if update is None:
                    raise ValueError(f"Thought State 引用了未知 Update: {update_id}")
                if update.character_id != item.character_id:
                    raise ValueError("Thought State 包含其他角色的 Update")
            unknown_facts = set(state.event_fact_ids) - valid_fact_ids
            if unknown_facts:
                raise ValueError(
                    "Thought State 引用了未知 Event Fact: " + ", ".join(sorted(unknown_facts))
                )
        return
    if isinstance(artifact, Stage3RelationReviewArtifact):
        raise ValueError("未归属 Relation Observation 不能直接发布")
    if isinstance(artifact, Stage3ThoughtReviewArtifact):
        raise ValueError("未归属 Thought Update 不能直接发布")


def _validate_windows(label: str, windows: list[tuple[int, int]]) -> None:
    """校验一个状态序列的时间窗口按时间互不重叠。"""

    ordered = sorted(windows)
    for index, (visible_from, visible_to) in enumerate(ordered):
        if visible_from > visible_to:
            raise ValueError(f"{label} 存在反向时间窗口")
        if index > 0 and ordered[index - 1][1] >= visible_from:
            raise ValueError(f"{label} 的状态时间窗口重叠")


def _replace_story_document(
    artifact: OrdinaryReviewArtifact,
    command: ReplaceStoryDocumentCommand,
) -> None:
    """写入 Story Event 人工文档。"""

    if not isinstance(artifact, Stage3DocumentReviewArtifact):
        raise ValueError("Story Event 文档只能写入 document review")
    record = next(
        (item for item in artifact.story_events if item.candidate_id == command.item_id),
        None,
    )
    if record is None:
        raise KeyError(f"未找到 Story Event: {command.item_id}")
    record.reviewed_document = command.content.model_copy(deep=True)
    reset_review_fields(record)


def _replace_lore_document(
    artifact: OrdinaryReviewArtifact,
    command: ReplaceLoreDocumentCommand,
) -> None:
    """写入 Lore Entry 人工文档。"""

    if not isinstance(artifact, Stage3DocumentReviewArtifact):
        raise ValueError("Lore Entry 文档只能写入 document review")
    record = next(
        (item for item in artifact.lore_entries if item.candidate_id == command.item_id),
        None,
    )
    if record is None:
        raise KeyError(f"未找到 Lore Entry: {command.item_id}")
    record.reviewed_document = command.content.model_copy(deep=True)
    reset_review_fields(record)


def _replace_relation_content(
    artifact: OrdinaryReviewArtifact,
    command: ReplaceRelationContentCommand,
) -> None:
    """写入 Relation Type 人工完整内容。"""

    if not isinstance(artifact, Stage3RelationReviewArtifact):
        raise ValueError("Relation 内容只能写入 relation review")
    record = next(
        (item for item in artifact.relation_types if item.relation_type_id == command.item_id),
        None,
    )
    if record is None:
        raise KeyError(f"未找到 Relation Type: {command.item_id}")
    record.reviewed_content = command.content.model_copy(deep=True)
    reset_review_fields(record)


def _replace_thought_content(
    artifact: OrdinaryReviewArtifact,
    command: ReplaceThoughtContentCommand,
) -> None:
    """写入 Thought Thread 人工完整内容。"""

    if not isinstance(artifact, Stage3ThoughtReviewArtifact):
        raise ValueError("Thought 内容只能写入 thought review")
    record = next(
        (item for item in artifact.threads if item.thought_thread_id == command.item_id),
        None,
    )
    if record is None:
        raise KeyError(f"未找到 Thought Thread: {command.item_id}")
    record.reviewed_content = command.content.model_copy(deep=True)
    reset_review_fields(record)


def _restore_generated_content(
    artifact: OrdinaryReviewArtifact,
    item_id: str,
) -> None:
    """删除一个候选的人工快照并撤销审批。"""

    if isinstance(artifact, Stage3DocumentReviewArtifact):
        for record in artifact.story_events:
            if record.candidate_id == item_id:
                record.reviewed_document = None
                reset_review_fields(record)
                return
        for record in artifact.lore_entries:
            if record.candidate_id == item_id:
                record.reviewed_document = None
                reset_review_fields(record)
                return
    elif isinstance(artifact, Stage3RelationReviewArtifact):
        for record in artifact.relation_types:
            if record.relation_type_id == item_id:
                record.reviewed_content = None
                reset_review_fields(record)
                return
    else:
        for record in artifact.threads:
            if record.thought_thread_id == item_id:
                record.reviewed_content = None
                reset_review_fields(record)
                return
    raise KeyError(f"未找到可恢复机器版本的审核项: {item_id}")


def _assign_relation_observation(
    artifact: OrdinaryReviewArtifact,
    command: AssignRelationObservationCommand,
) -> None:
    """在同一有向角色对内把 Observation 原子移动到目标 State。"""

    if not isinstance(artifact, Stage3RelationReviewArtifact):
        raise ValueError("Observation 只能在 relation review 中分配")
    observation = next(
        (item for item in artifact.observations if item.observation_id == command.observation_id),
        None,
    )
    target = next(
        (item for item in artifact.relation_types if item.relation_type_id == command.target_type_id),
        None,
    )
    if observation is None or target is None:
        raise KeyError("未找到 Observation 或目标 Relation Type")
    if (
        observation.subject_character_id != target.subject_character_id
        or observation.object_character_id != target.object_character_id
    ):
        raise ValueError("Observation 不得跨有向角色对移动")
    for record in artifact.relation_types:
        content = record.effective_content().model_copy(deep=True)
        changed = False
        for state in content.states:
            if command.observation_id in state.supporting_observation_ids:
                state.supporting_observation_ids = [
                    value
                    for value in state.supporting_observation_ids
                    if value != command.observation_id
                ]
                changed = True
        if changed:
            record.reviewed_content = content
            reset_review_fields(record)
    target_content = target.effective_content().model_copy(deep=True)
    if not 0 <= command.target_state_index < len(target_content.states):
        raise IndexError("目标 Relation State 索引越界")
    target_state = target_content.states[command.target_state_index]
    target_state.supporting_observation_ids = sorted(
        set(target_state.supporting_observation_ids) | {command.observation_id}
    )
    target.reviewed_content = target_content
    reset_review_fields(target)
    artifact.unmerged_observations = [
        item
        for item in artifact.unmerged_observations
        if item.observation_id != command.observation_id
    ]


def _assign_thought_update(
    artifact: OrdinaryReviewArtifact,
    command: AssignThoughtUpdateCommand,
) -> None:
    """在同一角色内把 Update 原子移动到目标 Thought State。"""

    if not isinstance(artifact, Stage3ThoughtReviewArtifact):
        raise ValueError("Update 只能在 thought review 中分配")
    update = next((item for item in artifact.updates if item.update_id == command.update_id), None)
    target = next(
        (item for item in artifact.threads if item.thought_thread_id == command.target_thread_id),
        None,
    )
    if update is None or target is None:
        raise KeyError("未找到 Update 或目标 Thought Thread")
    if update.character_id != target.character_id:
        raise ValueError("Thought Update 不得跨角色移动")
    for record in artifact.threads:
        content = record.effective_content().model_copy(deep=True)
        changed = False
        for state in content.states:
            if command.update_id in state.supporting_update_ids:
                state.supporting_update_ids = [
                    value for value in state.supporting_update_ids if value != command.update_id
                ]
                changed = True
        if changed:
            record.reviewed_content = content
            reset_review_fields(record)
    target_content = target.effective_content().model_copy(deep=True)
    if not 0 <= command.target_state_index < len(target_content.states):
        raise IndexError("目标 Thought State 索引越界")
    target_state = target_content.states[command.target_state_index]
    target_state.supporting_update_ids = sorted(
        set(target_state.supporting_update_ids) | {command.update_id}
    )
    target.reviewed_content = target_content
    reset_review_fields(target)
    artifact.unassigned_updates = [
        item for item in artifact.unassigned_updates if item.update_id != command.update_id
    ]


def _split_relation_type(
    artifact: OrdinaryReviewArtifact,
    command: SplitRelationTypeCommand,
) -> None:
    """拆分 Relation Type，并让原 Type 明确继承旧身份。"""

    if not isinstance(artifact, Stage3RelationReviewArtifact):
        raise ValueError("Relation Type 只能在 relation review 中拆分")
    if any(item.relation_type_id == command.new_type_id for item in artifact.relation_types):
        raise ValueError("新 Relation Type ID 已存在")
    source = next(
        (item for item in artifact.relation_types if item.relation_type_id == command.item_id),
        None,
    )
    if source is None:
        raise KeyError(f"未找到 Relation Type: {command.item_id}")
    content = source.effective_content().model_copy(deep=True)
    if not 0 < command.split_index < len(content.states):
        raise ValueError("Relation Type 拆分位置必须位于两个 State 之间")
    source_content = content.model_copy(
        update={"states": content.states[: command.split_index]},
        deep=True,
    )
    new_content = RelationTypeContentDraft(
        semantic_label=command.new_semantic_label,
        states=content.states[command.split_index :],
    )
    new_record = source.model_copy(deep=True)
    new_record.relation_type_id = command.new_type_id
    new_record.reviewed_content = new_content
    new_record.previous_review_reference = source.relation_type_id
    new_record.identity_suggestions = []
    reset_review_fields(new_record)
    source.reviewed_content = source_content
    reset_review_fields(source)
    artifact.relation_types.append(new_record)
    artifact.relation_types.sort(key=lambda item: item.relation_type_id)


def _merge_relation_types(
    artifact: OrdinaryReviewArtifact,
    command: MergeRelationTypesCommand,
) -> None:
    """合并两个同向 Relation Type，并拒绝被合并的旧候选。"""

    if not isinstance(artifact, Stage3RelationReviewArtifact):
        raise ValueError("Relation Type 只能在 relation review 中合并")
    primary = next(
        (item for item in artifact.relation_types if item.relation_type_id == command.primary_type_id),
        None,
    )
    merged = next(
        (item for item in artifact.relation_types if item.relation_type_id == command.merged_type_id),
        None,
    )
    if primary is None or merged is None or primary is merged:
        raise KeyError("未找到两个不同的 Relation Type")
    if (
        primary.subject_character_id != merged.subject_character_id
        or primary.object_character_id != merged.object_character_id
    ):
        raise ValueError("Relation Type 不得跨有向角色对合并")
    states = [
        *primary.effective_content().model_copy(deep=True).states,
        *merged.effective_content().model_copy(deep=True).states,
    ]
    states.sort(key=lambda item: (item.visible_from, item.visible_to, item.relation_state_id))
    primary.reviewed_content = RelationTypeContentDraft(
        semantic_label=command.semantic_label,
        states=states,
    )
    reset_review_fields(primary)
    merged.reviewed_content = RelationTypeContentDraft(
        semantic_label=merged.effective_content().semantic_label,
        states=[],
    )
    reset_review_fields(merged)
    complete_review(merged, "reject", "duplicate", "已人工合并到其他 Relation Type。")


def _split_thought_thread(
    artifact: OrdinaryReviewArtifact,
    command: SplitThoughtThreadCommand,
) -> None:
    """拆分 Thought Thread，并让原 Thread 明确继承旧身份。"""

    if not isinstance(artifact, Stage3ThoughtReviewArtifact):
        raise ValueError("Thought Thread 只能在 thought review 中拆分")
    if any(item.thought_thread_id == command.new_thread_id for item in artifact.threads):
        raise ValueError("新 Thought Thread ID 已存在")
    source = next(
        (item for item in artifact.threads if item.thought_thread_id == command.item_id),
        None,
    )
    if source is None:
        raise KeyError(f"未找到 Thought Thread: {command.item_id}")
    content = source.effective_content().model_copy(deep=True)
    if not 0 < command.split_index < len(content.states):
        raise ValueError("Thought Thread 拆分位置必须位于两个 State 之间")
    source_content = content.model_copy(
        update={"states": content.states[: command.split_index]},
        deep=True,
    )
    new_content = ThoughtThreadContentDraft(
        canonical_subject=command.new_canonical_subject,
        thought_aspect=command.new_thought_aspect,
        states=content.states[command.split_index :],
    )
    new_record = source.model_copy(deep=True)
    new_record.thought_thread_id = command.new_thread_id
    new_record.reviewed_content = new_content
    new_record.previous_review_reference = source.thought_thread_id
    new_record.identity_suggestions = []
    reset_review_fields(new_record)
    source.reviewed_content = source_content
    reset_review_fields(source)
    artifact.threads.append(new_record)
    artifact.threads.sort(key=lambda item: item.thought_thread_id)


def _merge_thought_threads(
    artifact: OrdinaryReviewArtifact,
    command: MergeThoughtThreadsCommand,
) -> None:
    """合并同一角色的两个 Thought Thread，并拒绝被合并旧候选。"""

    if not isinstance(artifact, Stage3ThoughtReviewArtifact):
        raise ValueError("Thought Thread 只能在 thought review 中合并")
    primary = next(
        (item for item in artifact.threads if item.thought_thread_id == command.primary_thread_id),
        None,
    )
    merged = next(
        (item for item in artifact.threads if item.thought_thread_id == command.merged_thread_id),
        None,
    )
    if primary is None or merged is None or primary is merged:
        raise KeyError("未找到两个不同的 Thought Thread")
    if primary.character_id != merged.character_id:
        raise ValueError("Thought Thread 不得跨角色合并")
    states = [
        *primary.effective_content().model_copy(deep=True).states,
        *merged.effective_content().model_copy(deep=True).states,
    ]
    states.sort(key=lambda item: (item.visible_from, item.visible_to, item.thought_state_id))
    primary.reviewed_content = ThoughtThreadContentDraft(
        canonical_subject=command.canonical_subject,
        thought_aspect=command.thought_aspect,
        states=states,
    )
    reset_review_fields(primary)
    merged.reviewed_content = ThoughtThreadContentDraft(
        canonical_subject=merged.effective_content().canonical_subject,
        thought_aspect=merged.effective_content().thought_aspect,
        states=[],
    )
    reset_review_fields(merged)
    complete_review(merged, "reject", "duplicate", "已人工合并到其他 Thought Thread。")


def _resolve_identity(
    artifact: OrdinaryReviewArtifact,
    command: ResolveIdentityCommand,
) -> None:
    """确认顶层候选的开发侧身份继承建议。"""

    item = _find_identity_item(artifact, command.item_id)
    if command.choice == "inherit":
        suggested_ids = {value.previous_id for value in item.identity_suggestions}
        if command.previous_id is None or command.previous_id not in suggested_ids:
            raise ValueError("inherit 必须指定现有建议中的 previous_id")
        if isinstance(item, (StoryEventReviewRecord, LoreEntryReviewRecord)):
            item.candidate_id = command.previous_id
        elif isinstance(item, RelationTypeReviewRecord):
            item.relation_type_id = command.previous_id
        else:
            item.thought_thread_id = command.previous_id
    item.identity_suggestions = []
    reset_review_fields(item)


def _find_identity_item(
    artifact: OrdinaryReviewArtifact,
    item_id: str,
) -> IdentityReviewItem:
    """查找具有身份建议的顶层候选。"""

    if isinstance(artifact, Stage3DocumentReviewArtifact):
        candidates: list[IdentityReviewItem] = [*artifact.story_events, *artifact.lore_entries]
    elif isinstance(artifact, Stage3RelationReviewArtifact):
        candidates = list(artifact.relation_types)
    else:
        candidates = list(artifact.threads)
    for item in candidates:
        current_id = (
            getattr(item, "candidate_id", None)
            or getattr(item, "relation_type_id", None)
            or getattr(item, "thought_thread_id", None)
        )
        if current_id == item_id:
            return item
    raise KeyError(f"未找到身份候选: {item_id}")


def _complete_lore_decision(
    artifact: ReviewArtifact,
    command: CompleteLoreDecisionCommand,
) -> None:
    """完成一个人工 Lore 去重决定。"""

    if not isinstance(artifact, Stage3LoreDecisionsArtifact):
        raise ValueError("Lore 决定命令只能写入 lore decisions")
    decision = next(
        (item for item in artifact.decisions if item.group_id == command.group_id),
        None,
    )
    if decision is None:
        raise KeyError(f"未找到 Lore 去重组: {command.group_id}")
    if decision.status == "automatic":
        raise ValueError("机械一致的自动 Lore 决定不能人工覆盖")
    updated = decision.model_copy(
        update={
            "status": "completed",
            "action": command.action,
            "primary_candidate_id": command.primary_candidate_id,
            "reviewed_document": command.reviewed_document,
            "review_notes": command.review_notes,
        }
    )
    artifact.decisions[artifact.decisions.index(decision)] = decision.__class__.model_validate(
        updated.model_dump(mode="python")
    )


def _clear_lore_decision(
    artifact: ReviewArtifact,
    command: ClearLoreDecisionCommand,
) -> None:
    """把一个人工 Lore 决定恢复为 pending。"""

    if not isinstance(artifact, Stage3LoreDecisionsArtifact):
        raise ValueError("Lore 决定命令只能写入 lore decisions")
    decision = next(
        (item for item in artifact.decisions if item.group_id == command.group_id),
        None,
    )
    if decision is None:
        raise KeyError(f"未找到 Lore 去重组: {command.group_id}")
    if decision.status == "automatic":
        raise ValueError("机械一致的自动 Lore 决定不能恢复 pending")
    updated = decision.model_copy(
        update={
            "status": "pending",
            "action": None,
            "primary_candidate_id": None,
            "reviewed_document": None,
        }
    )
    artifact.decisions[artifact.decisions.index(decision)] = decision.__class__.model_validate(
        updated.model_dump(mode="python")
    )


def _validate_artifact(artifact: ReviewArtifact) -> ReviewArtifact:
    """按 artifact 的具体模型重新执行完整校验。"""

    validated: BaseModel = artifact.__class__.model_validate(artifact.model_dump(mode="python"))
    if isinstance(validated, Stage3DocumentReviewArtifact):
        return validated
    if isinstance(validated, Stage3RelationReviewArtifact):
        return validated
    if isinstance(validated, Stage3ThoughtReviewArtifact):
        return validated
    if isinstance(validated, Stage3LoreDecisionsArtifact):
        return validated
    raise TypeError("未知的审核 artifact 类型")
