"""世界书运行时编辑所需的查询、验证与 Override 服务。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .adapters import AdapterRegistry
from .effective_entries import entry_revision, merge_effective_entries
from .models import (
    EffectiveWorldbookEntry,
    PackageLoadResult,
    ValidationIssue,
    WorldbookEntry,
    WorldbookOverride,
    WorldbookTombstone,
)
from .user_state import WorldbookUserStateRepository


class RetrievalSummaryReviewRequired(ValueError):
    """表示语义字段变化后尚未确认检索摘要。"""


class WorldbookSequenceConflict(ValueError):
    """表示时间化状态与同一语义序列中的其他状态重叠。"""


class WorldbookReferenceError(ValueError):
    """表示 Character Thought 引用了不相容的剧情事件。"""


@dataclass(frozen=True, slots=True)
class PackageEntryRecord:
    """表示根世界书依赖闭包中的一个可导航条目位置。"""

    owner_package_id: str
    official_entry: WorldbookEntry | None
    effective_entry: EffectiveWorldbookEntry | None
    hidden: bool = False
    hidden_base_conflict: bool = False
    issue: ValidationIssue | None = None

    @property
    def entry(self) -> WorldbookEntry:
        """返回优先用于显示的有效内容，必要时回退到官方内容。"""

        if self.effective_entry is not None:
            return self.effective_entry.entry
        if self.official_entry is not None:
            return self.official_entry
        raise ValueError("条目记录缺少可显示内容")


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
                )
            )
        for item in effective:
            if item.entry.entry_id not in official_ids:
                records.append(
                    PackageEntryRecord(
                        owner_package_id=package_id,
                        official_entry=None,
                        effective_entry=item,
                    )
                )
    return records, all_issues


class WorldbookEditService:
    """在写入用户状态前统一校验完整 Override 与跨条目约束。"""

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
