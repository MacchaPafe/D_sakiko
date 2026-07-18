"""安全发现并加载官方世界书包。"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from .hashing import file_sha256
from .models import (
    PackageLoadResult,
    PackageReadiness,
    ValidationIssue,
    WorldbookEntry,
    WorldbookManifest,
)
from .versioning import version_satisfies


class WorldbookPackageLoader:
    """从显式根目录加载通过完整性校验的官方世界书包。"""

    def __init__(self, official_root: Path) -> None:
        """保存官方包根目录，不依赖当前工作目录。"""

        self._official_root = official_root.resolve()

    def discover(self) -> dict[str, PackageLoadResult]:
        """发现全部 manifest，并执行全局 ID 和依赖图校验。"""

        if not self._official_root.exists():
            return {}
        results: dict[str, PackageLoadResult] = {}
        seen_entry_ids: dict[UUID, str] = {}
        for manifest_path in sorted(self._official_root.glob("*/manifest.json")):
            result = self.load_package(manifest_path.parent)
            key = result.manifest.package_id if result.manifest is not None else manifest_path.parent.name
            if key.casefold() in {item.casefold() for item in results}:
                result.issues.append(ValidationIssue(code="duplicate_package_id", message="包 ID 存在大小写冲突", path=str(manifest_path)))
                result.readiness = PackageReadiness.UNAVAILABLE
            results[key] = result
            for entry in result.entries:
                owner = seen_entry_ids.get(entry.entry_id)
                if owner is not None:
                    result.issues.append(ValidationIssue(code="duplicate_entry_id", message=f"entry ID 已由包 {owner} 使用", package_id=key, entry_id=entry.entry_id))
                    result.readiness = PackageReadiness.UNAVAILABLE
                else:
                    seen_entry_ids[entry.entry_id] = key
        self._validate_dependencies(results)
        return results

    def load_package(self, package_dir: Path) -> PackageLoadResult:
        """加载一个包；结构错误会让整包不可用。"""

        issues: list[ValidationIssue] = []
        manifest_path = package_dir / "manifest.json"
        try:
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = WorldbookManifest.model_validate(raw_manifest)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            return PackageLoadResult(issues=[ValidationIssue(code="invalid_manifest", message=str(exc), path=str(manifest_path))])
        if manifest.format_version != 1:
            issues.append(ValidationIssue(code="unsupported_manifest", message=f"不支持 manifest v{manifest.format_version}", package_id=manifest.package_id))
        declared_paths: set[str] = set()
        entries: list[WorldbookEntry] = []
        seen_ids: set[UUID] = set()
        for record in manifest.content_files:
            try:
                content_path = self._resolve_content_path(package_dir, record.path)
            except ValueError as exc:
                issues.append(ValidationIssue(code="unsafe_path", message=str(exc), package_id=manifest.package_id, path=record.path))
                continue
            folded = record.path.casefold()
            if folded in declared_paths:
                issues.append(ValidationIssue(code="duplicate_content_path", message="内容路径重复或存在大小写冲突", package_id=manifest.package_id, path=record.path))
                continue
            declared_paths.add(folded)
            if not content_path.is_file() or file_sha256(content_path) != record.sha256:
                issues.append(ValidationIssue(code="content_hash_mismatch", message="内容文件缺失或摘要不匹配", package_id=manifest.package_id, path=record.path))
                continue
            try:
                raw_content = json.loads(content_path.read_text(encoding="utf-8"))
                raw_entries = raw_content["entries"]
                if not isinstance(raw_entries, list):
                    raise TypeError("entries 必须是列表")
                for raw_entry in raw_entries:
                    try:
                        entry = WorldbookEntry.model_validate(raw_entry)
                    except ValidationError as exc:
                        issues.append(ValidationIssue(code="invalid_entry", message=str(exc), package_id=manifest.package_id, path=record.path))
                        continue
                    if entry.entry_type != record.entry_type:
                        issues.append(ValidationIssue(code="invalid_entry", message="entry_type 与 manifest 声明不一致", package_id=manifest.package_id, entry_id=entry.entry_id, path=record.path))
                        continue
                    if entry.entry_id in seen_ids:
                        raise ValueError(f"包内 entry ID 重复: {entry.entry_id}")
                    seen_ids.add(entry.entry_id)
                    entries.append(entry)
            except (OSError, json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
                issues.append(ValidationIssue(code="invalid_content", message=str(exc), package_id=manifest.package_id, path=record.path))
        package_files = {path.relative_to(package_dir).as_posix().casefold() for path in (package_dir / "content").glob("*.json")} if (package_dir / "content").exists() else set()
        undeclared = package_files - declared_paths
        for path in sorted(undeclared):
            issues.append(ValidationIssue(code="undeclared_content", message="发现 manifest 未声明的内容文件", package_id=manifest.package_id, path=path))
        if not issues:
            readiness = PackageReadiness.READY
        elif all(issue.code == "invalid_entry" for issue in issues):
            readiness = PackageReadiness.DEGRADED
        else:
            readiness = PackageReadiness.UNAVAILABLE
        return PackageLoadResult(manifest=manifest, entries=entries, issues=issues, readiness=readiness)

    def _resolve_content_path(self, package_dir: Path, relative_path: str) -> Path:
        """拒绝绝对路径和逃逸包目录的内容路径。"""

        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise ValueError("内容路径不能是绝对路径")
        resolved = (package_dir / candidate).resolve()
        if package_dir.resolve() not in resolved.parents:
            raise ValueError("内容路径逃逸包目录")
        return resolved

    def _validate_dependencies(self, results: dict[str, PackageLoadResult]) -> None:
        """校验依赖存在、版本、循环和传递可用性。"""

        graph: dict[str, list[str]] = {}
        for package_id, result in results.items():
            if result.manifest is None:
                continue
            graph[package_id] = [dependency.package_id for dependency in result.manifest.dependencies]
            for dependency in result.manifest.dependencies:
                target = results.get(dependency.package_id)
                if target is None:
                    result.issues.append(ValidationIssue(code="missing_dependency", message=f"缺少依赖 {dependency.package_id}", package_id=package_id))
                    result.readiness = PackageReadiness.UNAVAILABLE
                    continue
                if target.manifest is None:
                    result.issues.append(ValidationIssue(code="unavailable_dependency", message=f"依赖 {dependency.package_id} 的 manifest 不可用", package_id=package_id))
                    result.readiness = PackageReadiness.UNAVAILABLE
                    continue
                if not version_satisfies(target.manifest.package_version, dependency.version_spec):
                    result.issues.append(
                        ValidationIssue(
                            code="dependency_version_mismatch",
                            message=f"依赖 {dependency.package_id} 的版本 {target.manifest.package_version} 不满足 {dependency.version_spec}",
                            package_id=package_id,
                        )
                    )
                    result.readiness = PackageReadiness.UNAVAILABLE
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(package_id: str) -> bool:
            """深度优先检测当前依赖分支是否形成循环。"""

            if package_id in visiting:
                return True
            if package_id in visited:
                return False
            visiting.add(package_id)
            cyclic = any(visit(dependency) for dependency in graph.get(package_id, []) if dependency in graph)
            visiting.remove(package_id)
            visited.add(package_id)
            if cyclic and package_id in results:
                results[package_id].issues.append(ValidationIssue(code="dependency_cycle", message="包依赖形成循环", package_id=package_id))
                results[package_id].readiness = PackageReadiness.UNAVAILABLE
            return cyclic

        for package_id in graph:
            visit(package_id)

        changed = True
        while changed:
            changed = False
            for package_id, dependencies in graph.items():
                result = results[package_id]
                if result.readiness == PackageReadiness.UNAVAILABLE:
                    continue
                dependency_results = [results[item] for item in dependencies if item in results]
                unavailable = next((item for item in dependency_results if item.readiness == PackageReadiness.UNAVAILABLE), None)
                if unavailable is not None:
                    dependency_id = unavailable.manifest.package_id if unavailable.manifest is not None else "unknown"
                    result.issues.append(
                        ValidationIssue(
                            code="unavailable_dependency",
                            message=f"依赖闭包中的包 {dependency_id} 不可用",
                            package_id=package_id,
                        )
                    )
                    result.readiness = PackageReadiness.UNAVAILABLE
                    changed = True
                    continue
                if result.readiness == PackageReadiness.READY and any(
                    item.readiness == PackageReadiness.DEGRADED for item in dependency_results
                ):
                    result.issues.append(
                        ValidationIssue(
                            code="degraded_dependency",
                            message="依赖闭包中存在已降级的包",
                            package_id=package_id,
                        )
                    )
                    result.readiness = PackageReadiness.DEGRADED
                    changed = True
