"""世界书运行时编辑所需的查询、验证与 Override 服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from rag.models import SeriesId

from .adapters import AdapterRegistry
from .effective_entries import entry_revision, merge_effective_entries
from .hashing import canonical_json_sha256
from .models import (
    EffectiveWorldbookEntry,
    PackageLoadResult,
    ValidationIssue,
    WorldbookEntry,
    WorldbookOverride,
    WorldbookTombstone,
    WorldbookUserState,
)
from .time_coordinates import OPEN_ENDED_TIME, StoryTimeCoordinate, encode_story_time
from .user_state import WorldbookUserStateRepository


class RetrievalSummaryReviewRequired(ValueError):
    """表示语义字段变化后尚未确认检索摘要。"""


class WorldbookSequenceConflict(ValueError):
    """表示时间化状态与同一语义序列中的其他状态重叠。"""


class WorldbookReferenceError(ValueError):
    """表示 Character Thought 引用了不相容的剧情事件。"""


class WorldbookExtensionReferencedError(WorldbookReferenceError):
    """表示待删除的用户扩展仍被其他条目引用。"""

    def __init__(self, referencing_entries: list[WorldbookEntry]) -> None:
        """保存稳定排序后的引用条目并生成用户可读错误。"""

        self.referencing_entries = sorted(referencing_entries, key=lambda entry: str(entry.entry_id))
        super().__init__(f"该剧情事件仍被 {len(self.referencing_entries)} 个角色想法引用")


@dataclass(frozen=True, slots=True)
class PackageEntryRecord:
    """表示根世界书依赖闭包中的一个可导航条目位置。"""

    owner_package_id: str
    official_entry: WorldbookEntry | None
    effective_entry: EffectiveWorldbookEntry | None
    hidden: bool = False
    hidden_base_conflict: bool = False
    issue: ValidationIssue | None = None
    raw_entry: WorldbookEntry | None = None
    has_override: bool = False
    orphaned_override: bool = False

    @property
    def entry(self) -> WorldbookEntry:
        """返回优先用于显示的有效内容，必要时回退到官方内容。"""

        if self.effective_entry is not None:
            return self.effective_entry.entry
        if self.official_entry is not None:
            return self.official_entry
        if self.raw_entry is not None:
            return self.raw_entry
        raise ValueError("条目记录缺少可显示内容")


@dataclass(frozen=True, slots=True)
class PersistentEntryRecord:
    """表示跨全部已安装包保留的官方或用户条目。"""

    owner_package_id: str
    entry: WorldbookEntry
    source: Literal["official", "override", "extension"]
    hidden: bool = False


DeletionImpactAction = Literal[
    "detach_reference",
    "create_official_override",
    "delete_orphan_override",
    "delete_incompatible_extension",
    "restore_official",
    "cross_package_blocker",
    "validation_blocker",
]


@dataclass(frozen=True, slots=True)
class StoryEventDeletionImpact:
    """描述删除剧情事件时一条角色想法将采取的动作。"""

    owner_package_id: str
    entry: WorldbookEntry
    source: Literal["official", "override", "extension"]
    action: DeletionImpactAction
    hidden: bool = False
    detail: str = ""


@dataclass(frozen=True, slots=True)
class StoryEventDeletionPlan:
    """保存一次剧情事件级联删除的纯规划结果。"""

    package_id: str
    target_entry_id: UUID
    source_state_revision: str
    catalog_revision: str
    target_state: WorldbookUserState
    impacts: tuple[StoryEventDeletionImpact, ...]

    @property
    def blockers(self) -> tuple[StoryEventDeletionImpact, ...]:
        """返回阻止本次删除的跨包或校验问题。"""

        return tuple(
            item
            for item in self.impacts
            if item.action in {"cross_package_blocker", "validation_blocker"}
        )

    @property
    def can_apply(self) -> bool:
        """返回计划是否可以作为一次原子写入提交。"""

        return not self.blockers


def dependency_closure(packages: dict[str, PackageLoadResult], root_package_id: str) -> list[str]:
    """按根包优先的稳定顺序返回完整依赖闭包。"""

    if root_package_id not in packages:
        raise KeyError(f"未知世界书包: {root_package_id}")
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(package_id: str) -> None:
        """递归加入一个包及其尚未访问的依赖。"""

        if package_id in visited:
            return
        visited.add(package_id)
        ordered.append(package_id)
        result = packages.get(package_id)
        if result is None or result.manifest is None:
            return
        for dependency in result.manifest.dependencies:
            if dependency.package_id in packages:
                visit(dependency.package_id)

    visit(root_package_id)
    return ordered


def build_package_entry_records(
    packages: dict[str, PackageLoadResult],
    states: WorldbookUserStateRepository,
    root_package_id: str,
    registry: AdapterRegistry | None = None,
) -> tuple[list[PackageEntryRecord], list[ValidationIssue]]:
    """合并根世界书依赖闭包并保留隐藏或不兼容条目的导航位置。"""

    records: list[PackageEntryRecord] = []
    all_issues: list[ValidationIssue] = []
    for package_id in dependency_closure(packages, root_package_id):
        result = packages[package_id]
        all_issues.extend(result.issues)
        if result.manifest is None:
            continue
        try:
            state = states.load(package_id)
        except (OSError, ValueError, TypeError) as exc:
            all_issues.append(
                ValidationIssue(code="invalid_user_state", message=str(exc), package_id=package_id)
            )
            continue
        effective, issues = merge_effective_entries(package_id, result.entries, state)
        if registry is not None:
            for item in effective:
                try:
                    registry.normalize(item.entry)
                except (KeyError, TypeError, ValueError) as exc:
                    issues.append(
                        ValidationIssue(
                            code="incompatible_user_entry",
                            message=str(exc),
                            package_id=package_id,
                            entry_id=item.entry.entry_id,
                        )
                    )
        all_issues.extend(issues)
        invalid_ids = {item.entry_id for item in issues if item.entry_id is not None}
        effective_map = {
            item.entry.entry_id: item
            for item in effective
            if item.entry.entry_id not in invalid_ids
        }
        hidden_map = {item.entry_id: item for item in state.tombstones}
        hidden_ids = set(hidden_map)
        issue_map = {item.entry_id: item for item in issues if item.entry_id is not None}
        official_ids = {entry.entry_id for entry in result.entries}
        override_ids = {item.entry_id for item in state.overrides}
        for official in result.entries:
            records.append(
                PackageEntryRecord(
                    owner_package_id=package_id,
                    official_entry=official,
                    effective_entry=effective_map.get(official.entry_id),
                    hidden=official.entry_id in hidden_ids,
                    hidden_base_conflict=(
                        official.entry_id in hidden_map
                        and hidden_map[official.entry_id].base_revision != entry_revision(official)
                    ),
                    issue=issue_map.get(official.entry_id),
                    raw_entry=official,
                    has_override=official.entry_id in override_ids,
                )
            )
        for item in effective:
            if item.entry.entry_id not in official_ids:
                records.append(
                    PackageEntryRecord(
                        owner_package_id=package_id,
                        official_entry=None,
                        effective_entry=effective_map.get(item.entry.entry_id),
                        issue=issue_map.get(item.entry.entry_id),
                        raw_entry=item.entry,
                    )
                )
        for override in state.overrides:
            if override.entry_id in official_ids:
                continue
            raw_entry = WorldbookEntry(
                entry_id=override.entry_id,
                entry_type=override.entry_type,
                schema_version=override.schema_version,
                content=override.content,
            )
            issue: ValidationIssue | None = None
            if registry is not None:
                try:
                    registry.normalize(raw_entry)
                except (KeyError, TypeError, ValueError) as exc:
                    issue = ValidationIssue(
                        code="incompatible_orphan_override",
                        message=str(exc),
                        package_id=package_id,
                        entry_id=override.entry_id,
                    )
            records.append(
                PackageEntryRecord(
                    owner_package_id=package_id,
                    official_entry=None,
                    effective_entry=None,
                    issue=issue,
                    raw_entry=raw_entry,
                    has_override=True,
                    orphaned_override=True,
                )
            )
    return records, all_issues


def build_persistent_entry_catalog(
    packages: dict[str, PackageLoadResult],
    states: WorldbookUserStateRepository,
) -> list[PersistentEntryRecord]:
    """构建包含隐藏内容的全局持久条目目录供写入校验使用。"""

    records: list[PersistentEntryRecord] = []
    for package_id, result in sorted(packages.items()):
        if result.manifest is None:
            continue
        state = states.load(package_id)
        hidden_ids = {item.entry_id for item in state.tombstones}
        for official in result.entries:
            records.append(
                PersistentEntryRecord(
                    owner_package_id=package_id,
                    entry=official,
                    source="official",
                    hidden=official.entry_id in hidden_ids,
                )
            )
        records.extend(
            PersistentEntryRecord(
                owner_package_id=package_id,
                entry=WorldbookEntry(
                    entry_id=override.entry_id,
                    entry_type=override.entry_type,
                    schema_version=override.schema_version,
                    content=override.content,
                ),
                source="override",
                hidden=override.entry_id in hidden_ids,
            )
            for override in state.overrides
        )
        records.extend(
            PersistentEntryRecord(
                owner_package_id=package_id,
                entry=entry,
                source="extension",
            )
            for entry in state.extensions
        )
    return records


class WorldbookEditService:
    """在写入用户状态前统一校验 Override、扩展与跨条目约束。"""

    def __init__(self, states: WorldbookUserStateRepository, registry: AdapterRegistry) -> None:
        """保存用户状态仓库与 Type Module 注册表。"""

        self._states = states
        self._registry = registry

    def semantic_content_changed(self, previous: WorldbookEntry, candidate: WorldbookEntry) -> bool:
        """判断 Type Module 声明的检索语义字段是否发生变化。"""

        if previous.entry_type != candidate.entry_type:
            return True
        fields = self._registry.module(candidate.entry_type).semantic_fields
        return any(previous.content.get(field) != candidate.content.get(field) for field in fields)

    def basic_retrieval_text(self, entry: WorldbookEntry) -> str:
        """返回对应 Type Module 的透明基础检索文本。"""

        normalized = self._registry.normalize(entry)
        return self._registry.module(normalized.entry_type).basic_retrieval_text(normalized)

    def create_extension_draft(
        self,
        entry_type: Literal["story_event", "character_relation", "lore_entry", "character_thought"],
        timeline_id: str,
        default_series_id: str | None,
        default_canon_branch: str | None,
    ) -> WorldbookEntry:
        """创建尚未持久化且允许字段暂时不完整的用户扩展草稿。"""

        series_id = default_series_id or ""
        canon_branch = default_canon_branch or ""
        start = self._initial_story_time(series_id)
        common = {
            "timeline_id": timeline_id,
            "canon_branch": canon_branch,
            "retrieval_text": "",
        }
        if entry_type == "story_event":
            content: dict[str, object] = {
                **common,
                "occurred_story_year": None,
                "series_id": series_id,
                "episode": 1,
                "time_order": start,
                "visible_from": start,
                "visible_to": OPEN_ENDED_TIME,
                "title": "",
                "summary": "",
                "participants": [],
                "importance": 3,
                "tags": [],
            }
        elif entry_type == "character_thought":
            content = {
                **common,
                "character_id": "",
                "series_id": series_id,
                "thought_thread_key": str(uuid4()),
                "canonical_subject": "",
                "thought_aspect": "",
                "thought_text": "",
                "epistemic_status": "uncertain",
                "visible_from": start,
                "visible_to": OPEN_ENDED_TIME,
                "story_event_entry_ids": [],
                "tags": [],
            }
        elif entry_type == "character_relation":
            content = {
                **common,
                "subject_character_id": "",
                "object_character_id": "",
                "series_id": series_id,
                "relation_type_key": str(uuid4()),
                "state_summary": "",
                "speech_hint": "",
                "object_character_nickname": "",
                "visible_from": start,
                "visible_to": OPEN_ENDED_TIME,
                "tags": [],
            }
        else:
            content = {
                **common,
                "scope_type": "package",
                "series_ids": None,
                "applicable_story_years": None,
                "visible_from": None,
                "visible_to": None,
                "title": "",
                "content": "",
                "tags": [],
            }
        return WorldbookEntry(entry_id=uuid4(), entry_type=entry_type, content=content)

    def save_extension(
        self,
        package_id: str,
        candidate: WorldbookEntry,
        catalog: list[PersistentEntryRecord],
    ) -> WorldbookEntry:
        """验证并创建或更新一条归属于指定包的用户扩展。"""

        state = self._states.load(package_id)
        existing = next(
            (entry for entry in state.extensions if entry.entry_id == candidate.entry_id),
            None,
        )
        collisions = [record for record in catalog if record.entry.entry_id == candidate.entry_id]
        if existing is None and collisions:
            raise ValueError("新条目 UUID 已被其他世界书条目使用")
        if existing is not None and existing.entry_type != candidate.entry_type:
            raise ValueError("自定义条目不得改变 entry_type")
        if existing is not None and any(
            record.owner_package_id != package_id or record.source != "extension"
            for record in collisions
        ):
            raise ValueError("自定义条目 UUID 与其他世界书条目冲突")
        normalized = self._normalize_extension(candidate)
        validation_entries = [record.entry for record in catalog]
        self._validate_thought_references(normalized, validation_entries)
        self._validate_state_sequence(normalized, validation_entries)
        self._states.put_extension(package_id, normalized)
        return normalized

    def delete_extension(
        self,
        package_id: str,
        entry_id: UUID,
        catalog: list[PersistentEntryRecord],
    ) -> None:
        """验证所有权和反向引用后永久删除一条用户扩展。"""

        state = self._states.load(package_id)
        target = next((entry for entry in state.extensions if entry.entry_id == entry_id), None)
        if target is None:
            raise ValueError("自定义条目不存在或不属于当前世界书")
        if target.entry_type == "story_event":
            referencing = [
                record.entry
                for record in catalog
                if record.entry.entry_id != entry_id
                and record.entry.entry_type == "character_thought"
                and entry_id in self._story_reference_ids(record.entry)
            ]
            if referencing:
                raise WorldbookExtensionReferencedError(referencing)
        self._states.delete_extension(package_id, entry_id)

    def plan_story_event_deletion(
        self,
        package_id: str,
        entry_id: UUID,
        catalog: list[PersistentEntryRecord],
    ) -> StoryEventDeletionPlan:
        """规划删除剧情事件扩展及同包角色想法的全部连带修改。"""

        source_state = self._states.load(package_id)
        target = next(
            (entry for entry in source_state.extensions if entry.entry_id == entry_id),
            None,
        )
        if target is None or target.entry_type != "story_event":
            raise ValueError("待删除条目不是当前世界书中的剧情事件扩展")

        official_records = {
            (record.owner_package_id, record.entry.entry_id): record
            for record in catalog
            if record.source == "official"
        }
        override_records = {
            (record.owner_package_id, record.entry.entry_id): record
            for record in catalog
            if record.source == "override"
        }
        extension_records = [
            record for record in catalog if record.source == "extension"
        ]
        impacts: list[StoryEventDeletionImpact] = []
        target_state = source_state.model_copy(deep=True)
        target_state.extensions = [
            entry for entry in target_state.extensions if entry.entry_id != entry_id
        ]

        identities = sorted(
            set(official_records) | set(override_records),
            key=lambda value: (value[0], str(value[1])),
        )
        for identity in identities:
            official_record = official_records.get(identity)
            override_record = override_records.get(identity)
            active_record = override_record or official_record
            if (
                active_record is None
                or active_record.entry.entry_type != "character_thought"
                or entry_id not in self._story_reference_ids(active_record.entry)
            ):
                continue
            impact = self._plan_persistent_thought_impact(
                package_id,
                entry_id,
                active_record,
                official_record,
                override_record,
                target_state,
                catalog,
            )
            impacts.append(impact)

        for record in sorted(
            extension_records,
            key=lambda item: (item.owner_package_id, str(item.entry.entry_id)),
        ):
            if (
                record.entry.entry_type != "character_thought"
                or record.entry.entry_id == entry_id
                or entry_id not in self._story_reference_ids(record.entry)
            ):
                continue
            impacts.append(
                self._plan_extension_thought_impact(
                    package_id,
                    entry_id,
                    record,
                    target_state,
                )
            )

        if not any(
            item.action in {"cross_package_blocker", "validation_blocker"}
            for item in impacts
        ):
            affected_entry_ids = {
                item.entry.entry_id
                for item in impacts
                if item.action
                not in {"cross_package_blocker", "validation_blocker"}
            }
            self._validate_planned_state(
                package_id,
                entry_id,
                target_state,
                catalog,
                affected_entry_ids,
            )
        return StoryEventDeletionPlan(
            package_id=package_id,
            target_entry_id=entry_id,
            source_state_revision=self._state_revision(source_state),
            catalog_revision=self._catalog_revision(catalog),
            target_state=target_state,
            impacts=tuple(impacts),
        )

    def apply_story_event_deletion(
        self,
        plan: StoryEventDeletionPlan,
        catalog: list[PersistentEntryRecord],
    ) -> None:
        """在计划仍对应最新状态时以一次原子保存应用级联删除。"""

        if not plan.can_apply:
            raise WorldbookExtensionReferencedError(
                [item.entry for item in plan.blockers]
            )
        current = self._states.load(plan.package_id)
        if self._state_revision(current) != plan.source_state_revision:
            raise ValueError("世界书内容在确认期间已经变化，请重新检查删除影响")
        if self._catalog_revision(catalog) != plan.catalog_revision:
            raise ValueError("其他世界书内容在确认期间已经变化，请重新检查删除影响")
        self._states.save(plan.target_state)

    def delete_orphan_override(self, package_id: str, entry_id: UUID) -> None:
        """永久删除一条仍保存在所属包中的孤立 Override。"""

        state = self._states.load(package_id)
        if not any(item.entry_id == entry_id for item in state.overrides):
            raise ValueError("孤立修改不存在或已被处理")
        self._states.remove_override(package_id, entry_id)

    def validate_official_references(
        self,
        official_entry: WorldbookEntry,
        catalog: list[PersistentEntryRecord],
    ) -> None:
        """校验即将重新生效的官方角色想法没有悬空剧情事件引用。"""

        available = self._available_story_entries(catalog)
        self._validate_thought_references(official_entry, available)

    def restore_official_content(
        self,
        package_id: str,
        official_entry: WorldbookEntry,
        catalog: list[PersistentEntryRecord],
    ) -> None:
        """校验当前官方引用后删除 Override 并恢复官方内容。"""

        self.validate_official_references(official_entry, catalog)
        self._states.remove_override(package_id, official_entry.entry_id)

    def restore_hidden_entry(
        self,
        package_id: str,
        official_entry: WorldbookEntry,
        catalog: list[PersistentEntryRecord],
    ) -> None:
        """校验当前官方引用后恢复一条已隐藏的官方身份。"""

        self.validate_official_references(official_entry, catalog)
        self._states.restore(package_id, official_entry.entry_id)

    def save_override(
        self,
        package_id: str,
        official_entry: WorldbookEntry,
        previous_effective_entry: WorldbookEntry,
        content: dict[str, object],
        available_entries: list[WorldbookEntry],
        retrieval_reviewed: bool,
    ) -> WorldbookOverride:
        """验证并保存一条保留官方身份和类型的完整 Override。"""

        candidate = WorldbookEntry(
            entry_id=official_entry.entry_id,
            entry_type=official_entry.entry_type,
            schema_version=official_entry.schema_version,
            content=content,
        )
        validated = self._registry.normalize(candidate)
        module = self._registry.module(validated.entry_type)
        normalized = WorldbookEntry(
            entry_id=validated.entry_id,
            entry_type=validated.entry_type,
            schema_version=validated.schema_version,
            content=module.payload(validated),
        )
        if self.semantic_content_changed(previous_effective_entry, normalized) and not retrieval_reviewed:
            raise RetrievalSummaryReviewRequired("内容已变化，请先确认检索摘要")
        self._validate_thought_references(normalized, available_entries)
        self._validate_state_sequence(normalized, available_entries)
        override = WorldbookOverride(
            entry_id=official_entry.entry_id,
            entry_type=official_entry.entry_type,
            schema_version=normalized.schema_version,
            base_revision=entry_revision(official_entry),
            content=normalized.content,
        )
        self._states.put_override(package_id, override)
        return override

    def remove_override(self, package_id: str, entry_id: UUID) -> None:
        """删除 Override，使条目恢复为当前官方内容。"""

        self._states.remove_override(package_id, entry_id)

    def confirm_current_base(self, package_id: str, official_entry: WorldbookEntry) -> WorldbookOverride:
        """保留用户内容并把 Override 的基准更新为当前官方 revision。"""

        state = self._states.load(package_id)
        current = next((item for item in state.overrides if item.entry_id == official_entry.entry_id), None)
        if current is None:
            raise ValueError("当前条目没有可确认的修改")
        confirmed = current.model_copy(update={"base_revision": entry_revision(official_entry)})
        self._states.put_override(package_id, confirmed)
        return confirmed

    def hide_entry(self, package_id: str, official_entry: WorldbookEntry) -> None:
        """保存针对官方条目身份的可恢复隐藏记录。"""

        self._states.hide(
            package_id,
            WorldbookTombstone(
                entry_id=official_entry.entry_id,
                base_revision=entry_revision(official_entry),
            ),
        )

    def restore_entry(self, package_id: str, entry_id: UUID) -> None:
        """移除隐藏记录并保留可能存在的 Override。"""

        self._states.restore(package_id, entry_id)

    def raw_override(self, package_id: str, entry_id: UUID) -> WorldbookOverride | None:
        """返回供诊断导出的原始 Override。"""

        state = self._states.load(package_id)
        return next((item for item in state.overrides if item.entry_id == entry_id), None)

    def _plan_persistent_thought_impact(
        self,
        package_id: str,
        target_entry_id: UUID,
        active_record: PersistentEntryRecord,
        official_record: PersistentEntryRecord | None,
        override_record: PersistentEntryRecord | None,
        target_state: WorldbookUserState,
        catalog: list[PersistentEntryRecord],
    ) -> StoryEventDeletionImpact:
        """规划官方或 Override 角色想法对事件删除的响应。"""

        if active_record.owner_package_id != package_id:
            return StoryEventDeletionImpact(
                active_record.owner_package_id,
                active_record.entry,
                active_record.source,
                "cross_package_blocker",
                active_record.hidden,
                "该引用属于其他世界书",
            )
        if override_record is not None and official_record is None:
            target_state.overrides = [
                item
                for item in target_state.overrides
                if item.entry_id != active_record.entry.entry_id
            ]
            return StoryEventDeletionImpact(
                package_id,
                active_record.entry,
                "override",
                "delete_orphan_override",
                active_record.hidden,
            )
        try:
            normalized = self._registry.normalize(active_record.entry)
        except (KeyError, TypeError, ValueError):
            if override_record is None:
                return StoryEventDeletionImpact(
                    package_id,
                    active_record.entry,
                    "official",
                    "validation_blocker",
                    active_record.hidden,
                    "官方角色想法格式不兼容，无法安全保存修改",
                )
            if official_record is None:
                raise AssertionError("孤立 Override 应在格式校验前处理")
            available = self._available_story_entries(
                catalog,
                excluded_entry_ids={target_entry_id},
            )
            try:
                self._validate_thought_references(official_record.entry, available)
            except WorldbookReferenceError as exc:
                return StoryEventDeletionImpact(
                    package_id,
                    active_record.entry,
                    "override",
                    "validation_blocker",
                    active_record.hidden,
                    f"删除修改后恢复的官方角色想法仍有无效引用：{exc}",
                )
            target_state.overrides = [
                item
                for item in target_state.overrides
                if item.entry_id != active_record.entry.entry_id
            ]
            return StoryEventDeletionImpact(
                package_id,
                active_record.entry,
                "override",
                "restore_official",
                active_record.hidden,
            )

        updated_entry = self._without_story_reference(normalized, target_entry_id)
        if override_record is not None:
            existing = next(
                item
                for item in target_state.overrides
                if item.entry_id == active_record.entry.entry_id
            )
            replacement = existing.model_copy(update={"content": updated_entry.content})
            target_state.overrides = [
                item
                for item in target_state.overrides
                if item.entry_id != replacement.entry_id
            ] + [replacement]
            return StoryEventDeletionImpact(
                package_id,
                updated_entry,
                "override",
                "detach_reference",
                active_record.hidden,
            )
        if official_record is None:
            raise AssertionError("官方角色想法规划缺少官方记录")
        target_state.overrides.append(
            WorldbookOverride(
                entry_id=official_record.entry.entry_id,
                entry_type=official_record.entry.entry_type,
                schema_version=updated_entry.schema_version,
                base_revision=entry_revision(official_record.entry),
                content=updated_entry.content,
            )
        )
        return StoryEventDeletionImpact(
            package_id,
            updated_entry,
            "official",
            "create_official_override",
            active_record.hidden,
        )

    def _plan_extension_thought_impact(
        self,
        package_id: str,
        target_entry_id: UUID,
        record: PersistentEntryRecord,
        target_state: WorldbookUserState,
    ) -> StoryEventDeletionImpact:
        """规划一条用户扩展角色想法对事件删除的响应。"""

        if record.owner_package_id != package_id:
            return StoryEventDeletionImpact(
                record.owner_package_id,
                record.entry,
                "extension",
                "cross_package_blocker",
                record.hidden,
                "该引用属于其他世界书",
            )
        try:
            normalized = self._registry.normalize(record.entry)
        except (KeyError, TypeError, ValueError):
            target_state.extensions = [
                item
                for item in target_state.extensions
                if item.entry_id != record.entry.entry_id
            ]
            return StoryEventDeletionImpact(
                package_id,
                record.entry,
                "extension",
                "delete_incompatible_extension",
                record.hidden,
            )
        updated_entry = self._without_story_reference(normalized, target_entry_id)
        target_state.extensions = [
            item
            for item in target_state.extensions
            if item.entry_id != updated_entry.entry_id
        ] + [updated_entry]
        return StoryEventDeletionImpact(
            package_id,
            updated_entry,
            "extension",
            "detach_reference",
            record.hidden,
        )

    def _validate_planned_state(
        self,
        package_id: str,
        target_entry_id: UUID,
        target_state: WorldbookUserState,
        catalog: list[PersistentEntryRecord],
        affected_entry_ids: set[UUID],
    ) -> None:
        """验证本次受影响内容能够合并，且没有新增悬空引用。"""

        official_entries = [
            record.entry
            for record in catalog
            if record.owner_package_id == package_id and record.source == "official"
        ]
        effective, issues = merge_effective_entries(
            package_id,
            official_entries,
            target_state,
        )
        affected_issues = [
            issue for issue in issues if issue.entry_id in affected_entry_ids
        ]
        if affected_issues:
            messages = "；".join(issue.message for issue in affected_issues)
            raise ValueError(f"级联删除后的用户状态不合法：{messages}")
        normalized_entries: list[WorldbookEntry] = []
        for item in effective:
            if item.entry.entry_id in affected_entry_ids:
                normalized_entries.append(self._registry.normalize(item.entry))
        external_entries = [
            entry
            for entry in self._available_story_entries(
                catalog,
                excluded_entry_ids={target_entry_id},
            )
            if all(
                entry.entry_id != current.entry_id
                for current in normalized_entries
            )
        ]
        available = [*external_entries, *normalized_entries]
        for entry in normalized_entries:
            self._validate_thought_references(entry, available)

    def _without_story_reference(
        self,
        entry: WorldbookEntry,
        target_entry_id: UUID,
    ) -> WorldbookEntry:
        """移除一个剧情事件引用并规范化角色想法的完整内容。"""

        content = dict(entry.content)
        values = content.get("story_event_entry_ids")
        if not isinstance(values, list):
            raise WorldbookReferenceError("关联剧情事件必须是列表")
        content["story_event_entry_ids"] = [
            value for value in values if str(value) != str(target_entry_id)
        ]
        candidate = entry.model_copy(update={"content": content})
        normalized = self._registry.normalize(candidate)
        module = self._registry.module(normalized.entry_type)
        return normalized.model_copy(update={"content": module.payload(normalized)})

    @staticmethod
    def _state_revision(state: WorldbookUserState) -> str:
        """计算用于检测确认期间并发变化的用户状态摘要。"""

        return canonical_json_sha256(state.model_dump(mode="json"))

    @staticmethod
    def _catalog_revision(catalog: list[PersistentEntryRecord]) -> str:
        """计算全局持久目录摘要，防止确认期间跨包状态发生变化。"""

        payload = [
            {
                "owner_package_id": record.owner_package_id,
                "source": record.source,
                "hidden": record.hidden,
                "entry": record.entry.model_dump(mode="json"),
            }
            for record in sorted(
                catalog,
                key=lambda item: (
                    item.owner_package_id,
                    str(item.entry.entry_id),
                    item.source,
                ),
            )
        ]
        return canonical_json_sha256(payload)

    def _available_story_entries(
        self,
        catalog: list[PersistentEntryRecord],
        excluded_entry_ids: set[UUID] | None = None,
    ) -> list[WorldbookEntry]:
        """从分层目录构造可被角色想法引用的兼容剧情事件身份。"""

        excluded = excluded_entry_ids or set()
        official = {
            (record.owner_package_id, record.entry.entry_id): record.entry
            for record in catalog
            if record.source == "official"
        }
        overrides = {
            (record.owner_package_id, record.entry.entry_id): record.entry
            for record in catalog
            if record.source == "override"
        }
        candidates = [
            overrides.get(identity, entry)
            for identity, entry in official.items()
        ]
        candidates.extend(
            record.entry
            for record in catalog
            if record.source == "extension"
        )
        available: list[WorldbookEntry] = []
        for candidate in candidates:
            if (
                candidate.entry_id in excluded
                or candidate.entry_type != "story_event"
            ):
                continue
            try:
                available.append(self._registry.normalize(candidate))
            except (KeyError, TypeError, ValueError):
                continue
        return available

    def _normalize_extension(self, candidate: WorldbookEntry) -> WorldbookEntry:
        """补齐基础检索文本并返回 Type Module 的规范化完整条目。"""

        content = dict(candidate.content)
        retrieval_text = content.get("retrieval_text")
        if not isinstance(retrieval_text, str) or not retrieval_text.strip():
            content["retrieval_text"] = "待生成"
            provisional = candidate.model_copy(update={"content": content})
            validated = self._registry.normalize(provisional)
            content["retrieval_text"] = self._registry.module(
                validated.entry_type
            ).basic_retrieval_text(validated)
        prepared = candidate.model_copy(update={"content": content})
        validated = self._registry.normalize(prepared)
        module = self._registry.module(validated.entry_type)
        return WorldbookEntry(
            entry_id=validated.entry_id,
            entry_type=validated.entry_type,
            schema_version=validated.schema_version,
            content=module.payload(validated),
        )

    @staticmethod
    def _initial_story_time(series_id: str) -> int:
        """为合法系列返回第一集起点，否则返回草稿占位坐标。"""

        if series_id not in {series.value for series in SeriesId}:
            return 0
        return encode_story_time(series_id, StoryTimeCoordinate(1, 0))

    @staticmethod
    def _story_reference_ids(entry: WorldbookEntry) -> set[UUID]:
        """从可能不兼容的角色想法中尽力读取剧情事件引用。"""

        values = entry.content.get("story_event_entry_ids", [])
        if not isinstance(values, list):
            return set()
        references: set[UUID] = set()
        for value in values:
            try:
                references.add(UUID(str(value)))
            except ValueError:
                continue
        return references

    def _validate_thought_references(
        self,
        candidate: WorldbookEntry,
        available_entries: list[WorldbookEntry],
    ) -> None:
        """校验 Character Thought 引用的 Story Event 存在且范围相容。"""

        if candidate.entry_type != "character_thought":
            return
        reference_values = candidate.content.get("story_event_entry_ids", [])
        if not isinstance(reference_values, list):
            raise WorldbookReferenceError("关联剧情事件必须是列表")
        entry_map = {entry.entry_id: entry for entry in available_entries}
        for value in reference_values:
            try:
                reference_id = UUID(str(value))
            except ValueError as exc:
                raise WorldbookReferenceError(f"无效的剧情事件 ID: {value}") from exc
            target = entry_map.get(reference_id)
            if target is None or target.entry_type != "story_event":
                raise WorldbookReferenceError(f"找不到关联的剧情事件: {reference_id}")
            for field in ("series_id", "timeline_id", "canon_branch"):
                if candidate.content.get(field) != target.content.get(field):
                    raise WorldbookReferenceError(f"关联剧情事件的 {field} 与角色想法不一致")

    def _validate_state_sequence(
        self,
        candidate: WorldbookEntry,
        available_entries: list[WorldbookEntry],
    ) -> None:
        """阻止同一 Relation Type 或 Thought Thread 的闭区间互相重叠。"""

        key_field = {
            "character_relation": "relation_type_key",
            "character_thought": "thought_thread_key",
        }.get(candidate.entry_type)
        if key_field is None:
            return
        candidate_key = candidate.content.get(key_field)
        candidate_start = candidate.content.get("visible_from")
        candidate_end = candidate.content.get("visible_to")
        if not isinstance(candidate_start, int) or not isinstance(candidate_end, int):
            raise WorldbookSequenceConflict("状态时间区间必须是整数")
        for existing in available_entries:
            if existing.entry_id == candidate.entry_id or existing.entry_type != candidate.entry_type:
                continue
            if existing.content.get(key_field) != candidate_key:
                continue
            existing_start = existing.content.get("visible_from")
            existing_end = existing.content.get("visible_to")
            if not isinstance(existing_start, int) or not isinstance(existing_end, int):
                continue
            if max(candidate_start, existing_start) <= min(candidate_end, existing_end):
                raise WorldbookSequenceConflict(
                    f"时间区间与条目 {existing.entry_id} 重叠"
                )
