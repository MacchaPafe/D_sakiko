"""使用正式 loader 与 Type Module 审计 staging 包集合。"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from uuid import UUID

from .adapters import create_default_registry
from .models import PackageReadiness, ValidationIssue, WorldbookEntry
from .package_loader import WorldbookPackageLoader
from .versioning import parse_semver


def audit_staging_packages(
    official_root: Path,
    staging_packages: dict[str, Path],
) -> list[ValidationIssue]:
    """把 staging 与未更新正式包组成预期集合并执行全局审计。"""

    issues: list[ValidationIssue] = []
    with tempfile.TemporaryDirectory(prefix="worldbook-audit-") as directory:
        expected_root = Path(directory)
        if official_root.exists():
            for package_dir in sorted(official_root.iterdir()):
                if not package_dir.is_dir() or package_dir.name in staging_packages:
                    continue
                os.symlink(package_dir.resolve(), expected_root / package_dir.name, target_is_directory=True)
        for package_id, package_dir in staging_packages.items():
            os.symlink(package_dir.resolve(), expected_root / package_id, target_is_directory=True)
        results = WorldbookPackageLoader(expected_root).discover()
        registry = create_default_registry()
        all_entries: dict[UUID, WorldbookEntry] = {}
        for package_id, result in results.items():
            issues.extend(result.issues)
            if result.manifest is None or result.readiness == PackageReadiness.UNAVAILABLE:
                continue
            for entry in result.entries:
                try:
                    registry.normalize(entry)
                except (TypeError, ValueError, KeyError) as exc:
                    issues.append(
                        ValidationIssue(
                            code="invalid_entry_schema",
                            message=str(exc),
                            package_id=package_id,
                            entry_id=entry.entry_id,
                        )
                    )
                all_entries[entry.entry_id] = entry
        _validate_thought_references(all_entries, issues)
        for package_id in staging_packages:
            if package_id not in results:
                issues.append(
                    ValidationIssue(
                        code="missing_staging_package",
                        message="预期的 staging 包未被正式 loader 发现",
                        package_id=package_id,
                    )
                )
    return issues


def validate_package_version_not_lower(
    official_root: Path,
    package_id: str,
    new_version: str,
) -> None:
    """拒绝把现有正式包降级到更低 SemVer。"""

    manifest_path = official_root / package_id / "manifest.json"
    if not manifest_path.exists():
        return
    result = WorldbookPackageLoader(official_root).load_package(manifest_path.parent)
    if result.manifest is None:
        raise ValueError("现有正式包 manifest 无效，无法比较 package_version")
    if parse_semver(new_version) < parse_semver(result.manifest.package_version):
        raise ValueError(
            f"package_version 不得从 {result.manifest.package_version} 降为 {new_version}"
        )


def _validate_thought_references(
    entries: dict[UUID, WorldbookEntry],
    issues: list[ValidationIssue],
) -> None:
    """校验 Thought 引用实际存在且指向兼容 Story Event。"""

    for entry in entries.values():
        if entry.entry_type != "character_thought":
            continue
        raw_references = entry.content.get("story_event_entry_ids", [])
        if not isinstance(raw_references, list):
            continue
        for raw_reference in raw_references:
            try:
                reference = UUID(str(raw_reference))
            except ValueError:
                issues.append(
                    ValidationIssue(
                        code="invalid_story_event_reference",
                        message=f"Thought 包含无效 Story Event UUID: {raw_reference}",
                        entry_id=entry.entry_id,
                    )
                )
                continue
            target = entries.get(reference)
            if target is None or target.entry_type != "story_event":
                issues.append(
                    ValidationIssue(
                        code="missing_story_event_reference",
                        message=f"Thought 引用的 Story Event 不存在: {reference}",
                        entry_id=entry.entry_id,
                    )
                )
                continue
            for field in ("series_id", "timeline_id", "canon_branch"):
                if target.content.get(field) != entry.content.get(field):
                    issues.append(
                        ValidationIssue(
                            code="incompatible_story_event_reference",
                            message=f"Thought 与 Story Event 的 {field} 不一致",
                            entry_id=entry.entry_id,
                        )
                    )
