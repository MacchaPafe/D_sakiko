"""从权威 JSON 对账派生世界书索引。"""

from __future__ import annotations

from uuid import UUID

from .adapters import AdapterRegistry
from .effective_entries import merge_effective_entries
from .index import WorldbookIndex
from .models import (
    EffectiveWorldbookEntry,
    IndexPointMetadata,
    PackageReadiness,
    SyncPlan,
    SyncReport,
    ValidationIssue,
    WorldbookReadiness,
)
from .package_loader import WorldbookPackageLoader
from .user_state import WorldbookUserStateRepository


class WorldbookSyncCoordinator:
    """协调包加载、有效条目、实际索引扫描与增量对账。"""

    def __init__(
        self,
        package_loader: WorldbookPackageLoader,
        user_state_repository: WorldbookUserStateRepository,
        registry: AdapterRegistry,
        index: WorldbookIndex,
        index_fingerprint: str,
    ) -> None:
        """注入全部边界，使同步逻辑可脱离 Qdrant 测试。"""

        self._package_loader = package_loader
        self._user_state_repository = user_state_repository
        self._registry = registry
        self._index = index
        self._index_fingerprint = index_fingerprint

    def reconcile_all(self, force_rebuild: bool = False) -> SyncReport:
        """从全部权威 JSON 对账实际 point，并返回内存报告。"""

        package_results = self._package_loader.discover()
        issues: list[ValidationIssue] = []
        effective: dict[UUID, EffectiveWorldbookEntry] = {}
        unavailable_packages: list[str] = []
        for package_id, result in package_results.items():
            issues.extend(result.issues)
            if result.manifest is None or result.readiness == PackageReadiness.UNAVAILABLE:
                unavailable_packages.append(package_id)
                continue
            try:
                state = self._user_state_repository.load(package_id)
            except (OSError, ValueError, TypeError) as exc:
                issues.append(ValidationIssue(code="invalid_user_state", message=str(exc), package_id=package_id))
                unavailable_packages.append(package_id)
                continue
            merged, merge_issues = merge_effective_entries(package_id, result.entries, state)
            issues.extend(merge_issues)
            for item in merged:
                try:
                    self._registry.normalize(item.entry)
                    effective[item.entry.entry_id] = item
                except (TypeError, ValueError, KeyError) as exc:
                    issues.append(ValidationIssue(code="invalid_entry", message=str(exc), package_id=package_id, entry_id=item.entry.entry_id))
        indexed = self._index.scan_metadata()
        plan = self._build_plan(effective, indexed, force_rebuild)
        deleted_count = 0
        for package_id in unavailable_packages:
            deleted_count += self._index.delete_package(package_id)
        if plan.rebuild:
            self._index.rebuild()
            indexed = {}
        deleted_count += self._index.delete(plan.delete_entry_ids)
        projections = [self._registry.projection(effective[entry_id]) for entry_id in plan.upsert_entry_ids]
        indexed_count = self._index.upsert(projections, self._index_fingerprint)
        readiness = WorldbookReadiness.READY
        if issues:
            readiness = WorldbookReadiness.DEGRADED if effective else WorldbookReadiness.UNAVAILABLE
        return SyncReport(
            success=readiness != WorldbookReadiness.UNAVAILABLE,
            readiness=readiness,
            indexed_count=indexed_count,
            deleted_count=deleted_count,
            skipped_count=len(issues),
            issues=issues,
        )

    def _build_plan(
        self,
        effective: dict[UUID, EffectiveWorldbookEntry],
        indexed: dict[UUID, IndexPointMetadata],
        force_rebuild: bool,
    ) -> SyncPlan:
        """比较实际 point revision 与期望条目，生成最小操作集合。"""

        fingerprint_mismatch = any(metadata.index_fingerprint != self._index_fingerprint for metadata in indexed.values())
        rebuild = force_rebuild or fingerprint_mismatch
        if rebuild:
            return SyncPlan(rebuild=True, upsert_entry_ids=sorted(effective, key=str), reason="强制重建或索引指纹变化")
        upserts = [
            entry_id
            for entry_id, entry in effective.items()
            if entry_id not in indexed or indexed[entry_id].entry_revision != entry.revision
        ]
        deletes = list(indexed.keys() - effective.keys())
        return SyncPlan(upsert_entry_ids=sorted(upserts, key=str), delete_entry_ids=sorted(deletes, key=str), reason="按实际 point revision 增量对账")
