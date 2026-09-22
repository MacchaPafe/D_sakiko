"""发现季度根包并把用户选择解析成稳定检索上下文。"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rag.models import CanonBranch, CharacterId
from rag.worldbook.effective_entries import merge_effective_entries
from rag.worldbook.models import (
    EffectiveWorldbookEntry,
    PackageLoadResult,
    PackageReadiness,
    WorldbookEntry,
)
from rag.worldbook.package_loader import WorldbookPackageLoader
from rag.worldbook.time_coordinates import EPISODE_SLOT_COUNT, StoryTimeCoordinate, encode_story_time
from rag.worldbook.user_state import WorldbookUserStateRepository

from .models import WorldbookResolvedContext, WorldbookRootOption


class WorldbookCatalogError(ValueError):
    """表示根包或剧情上下文无法安全解析。"""


class WorldbookRootCatalog:
    """提供根包目录、依赖闭包和跨包状态优先级。"""

    def __init__(self, official_root: Path, user_state_root: Path) -> None:
        """保存权威包和用户状态目录。"""

        self._loader = WorldbookPackageLoader(official_root)
        self._user_state = WorldbookUserStateRepository(user_state_root)

    def list_roots(self) -> list[WorldbookRootOption]:
        """列出全部季度包，并保留不可用包的结构化原因。"""

        results = self._loader.discover()
        options: list[WorldbookRootOption] = []
        for package_id, result in sorted(results.items()):
            manifest = result.manifest
            if manifest is None or manifest.package_type != "season":
                continue
            reasons = [f"{issue.code}: {issue.message}" for issue in result.issues]
            closure, _ = self._dependency_closure(package_id, results)
            if result.readiness != PackageReadiness.UNAVAILABLE:
                reasons.extend(self._state_conflicts(closure, results))
            options.append(
                WorldbookRootOption(
                    package_id=package_id,
                    display_name=manifest.display_name,
                    package_version=manifest.package_version,
                    enabled=result.readiness != PackageReadiness.UNAVAILABLE and not reasons,
                    unavailable_reasons=reasons,
                    available_characters=self._available_characters(closure, results),
                )
            )
        return options

    def resolve(
        self,
        root_package_id: str,
        episode: int,
        character_id: CharacterId | str,
    ) -> WorldbookResolvedContext:
        """把根包、集数和角色视角解析成一次可冻结上下文。"""

        if not 1 <= episode <= 13:
            raise WorldbookCatalogError("剧情进度必须位于第 1～13 集")
        results = self._loader.discover()
        result = results.get(root_package_id)
        if result is None or result.manifest is None:
            raise WorldbookCatalogError("所选世界书包不存在")
        manifest = result.manifest
        if manifest.package_type != "season" or manifest.conversation_context is None:
            raise WorldbookCatalogError("所选世界书包不能作为季度根包")
        closure, depths = self._dependency_closure(root_package_id, results)
        reasons = [f"{issue.code}: {issue.message}" for issue in result.issues]
        reasons.extend(self._state_conflicts(closure, results))
        if result.readiness == PackageReadiness.UNAVAILABLE or reasons:
            raise WorldbookCatalogError("；".join(reasons) or "所选世界书包不可用")
        normalized_character = CharacterId(character_id)
        current_time = encode_story_time(
            manifest.conversation_context.series_id,
            StoryTimeCoordinate(episode=episode, episode_offset=EPISODE_SLOT_COUNT - 1),
        )
        versions = {
            package_id: results[package_id].manifest.package_version
            for package_id in closure
            if results[package_id].manifest is not None
        }
        return WorldbookResolvedContext(
            root_package_id=root_package_id,
            root_package_version=manifest.package_version,
            package_ids=closure,
            package_versions=versions,
            package_depths=depths,
            character_id=normalized_character,
            series_id=manifest.conversation_context.series_id,
            timeline_id=manifest.timeline_id,
            canon_branch=manifest.conversation_context.canon_branch,
            current_time=current_time,
            story_year=manifest.conversation_context.story_year,
            episode=episode,
        )

    def effective_entries(
        self,
        context: WorldbookResolvedContext,
    ) -> list[EffectiveWorldbookEntry]:
        """读取快照闭包内当前权威 JSON 与用户状态合并后的条目。"""

        results = self._loader.discover()
        effective: list[EffectiveWorldbookEntry] = []
        for package_id in context.package_ids:
            result = results.get(package_id)
            if result is None or result.manifest is None or result.readiness == PackageReadiness.UNAVAILABLE:
                raise WorldbookCatalogError(f"世界书包 {package_id} 已不可用")
            try:
                state = self._user_state.load(package_id)
                merged, issues = merge_effective_entries(package_id, result.entries, state)
            except (OSError, TypeError, ValueError) as exc:
                raise WorldbookCatalogError(f"读取世界书包 {package_id} 的用户状态失败") from exc
            if issues:
                raise WorldbookCatalogError(f"世界书包 {package_id} 的用户状态存在冲突")
            effective.extend(merged)
        return effective

    def available_characters(
        self,
        context: WorldbookResolvedContext,
    ) -> list[CharacterId]:
        """返回快照依赖闭包中实际出现过的规范角色。"""

        results = self._loader.discover()
        return self._available_characters(context.package_ids, results)

    def _dependency_closure(
        self,
        root_package_id: str,
        results: dict[str, PackageLoadResult],
    ) -> tuple[list[str], dict[str, int]]:
        """按根包优先顺序计算依赖闭包和最短依赖深度。"""

        depths: dict[str, int] = {}
        pending: list[tuple[str, int]] = [(root_package_id, 0)]
        while pending:
            package_id, depth = pending.pop(0)
            previous = depths.get(package_id)
            if previous is not None and previous <= depth:
                continue
            depths[package_id] = depth
            result = results.get(package_id)
            if result is None or result.manifest is None:
                continue
            pending.extend((item.package_id, depth + 1) for item in result.manifest.dependencies)
        ordered = sorted(depths, key=lambda item: (depths[item], item))
        return ordered, depths

    def _available_characters(
        self,
        package_ids: list[str],
        results: dict[str, PackageLoadResult],
    ) -> list[CharacterId]:
        """收集闭包内实际出现过的规范角色。"""

        characters: set[CharacterId] = set()
        for package_id in package_ids:
            result = results.get(package_id)
            if result is None:
                continue
            for entry in result.entries:
                for field_name in (
                    "character_id",
                    "subject_character_id",
                    "object_character_id",
                ):
                    value = entry.content.get(field_name)
                    if isinstance(value, str):
                        try:
                            characters.add(CharacterId(value))
                        except ValueError:
                            continue
        return sorted(characters, key=lambda item: item.value)

    def _state_conflicts(
        self,
        package_ids: list[str],
        results: dict[str, PackageLoadResult],
    ) -> list[str]:
        """检查同一依赖层无法按方向裁决的跨包状态重叠。"""

        _, depths = self._dependency_closure(package_ids[0], results)
        grouped: dict[tuple[object, ...], list[tuple[str, int, int]]] = defaultdict(list)
        for package_id in package_ids:
            result = results.get(package_id)
            if result is None:
                continue
            entries = self._effective_package_entries(package_id, result)
            for entry in entries:
                state_key = self._state_key(entry)
                if state_key is None:
                    continue
                visible_from = entry.content.get("visible_from")
                visible_to = entry.content.get("visible_to")
                if not isinstance(visible_from, int) or not isinstance(visible_to, int):
                    continue
                grouped[(depths.get(package_id, 0), *state_key)].append(
                    (package_id, visible_from, visible_to)
                )
        reasons: list[str] = []
        for states in grouped.values():
            for index, left in enumerate(states):
                for right in states[index + 1 :]:
                    if left[0] == right[0]:
                        continue
                    if max(left[1], right[1]) <= min(left[2], right[2]):
                        reasons.append(
                            "ambiguous_state_key: "
                            f"同层包 {left[0]} 与 {right[0]} 存在重叠的主观状态 key"
                        )
        return sorted(set(reasons))

    def _effective_package_entries(
        self,
        package_id: str,
        result: PackageLoadResult,
    ) -> list[WorldbookEntry]:
        """读取一个包用于目录审计的有效条目，失败时保守使用官方条目。"""

        try:
            merged, issues = merge_effective_entries(
                package_id,
                result.entries,
                self._user_state.load(package_id),
            )
        except (OSError, TypeError, ValueError):
            return result.entries
        if issues:
            return result.entries
        return [item.entry for item in merged]

    def _state_key(self, entry: WorldbookEntry) -> tuple[object, ...] | None:
        """提取 Relation 或 Thought 的显式跨包状态身份。"""

        content = entry.content
        timeline_id = content.get("timeline_id")
        canon_branch = content.get("canon_branch")
        if not isinstance(timeline_id, str) or not isinstance(canon_branch, str):
            return None
        try:
            branch = CanonBranch(canon_branch)
        except ValueError:
            return None
        if entry.entry_type == "character_relation":
            return (
                entry.entry_type,
                timeline_id,
                branch.value,
                content.get("subject_character_id"),
                content.get("object_character_id"),
                content.get("relation_type_key"),
            )
        if entry.entry_type == "character_thought":
            return (
                entry.entry_type,
                timeline_id,
                branch.value,
                content.get("character_id"),
                content.get("thought_thread_key"),
            )
        return None
