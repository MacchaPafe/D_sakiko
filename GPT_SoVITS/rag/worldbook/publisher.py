"""世界书 build spec 的纯验证与正式发布编排。"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Literal

from .build_audit import audit_staging_packages, validate_package_version_not_lower
from pydantic import ValidationError

from .build_models import WorldbookBatchBuildReport, WorldbookBatchBuildSpec, WorldbookBuildReport
from .builder import build_staging_package, load_build_spec
from .identity_map import IdentityResolver, load_identity_map, save_identity_map
from .models import ValidationIssue
from .worker import run_worker


IndexRebuildCallback = Callable[[Path], tuple[bool, str | None]]


def validate_worldbook_build(build_spec_path: str | Path) -> WorldbookBuildReport:
    """只读验证 build spec、审核门槛、正式 Schema 和全局包集合。"""

    resolved = load_build_spec(build_spec_path)
    report = _new_report(resolved.spec.package_id, "validate", resolved.build_report)
    try:
        validate_package_version_not_lower(
            resolved.official_root,
            resolved.spec.package_id,
            resolved.spec.package_version,
        )
        identity_map = load_identity_map(resolved.id_map, resolved.spec.package_id)
        resolver = IdentityResolver(identity_map, allocate=False, reactivate_all=True)
        with tempfile.TemporaryDirectory(prefix="worldbook-validate-") as directory:
            package_dir = Path(directory) / resolved.spec.package_id
            built = build_staging_package(resolved, resolver, package_dir)
            resolver.finalize_removed(set(), allow_all_removed=True)
            report.input_sha256 = built.input_sha256
            report.identity_changes = resolver.changes
            report.issues = audit_staging_packages(
                resolved.official_root,
                {resolved.spec.package_id: package_dir},
            )
            report.succeeded = not report.issues
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report.issues.append(
            ValidationIssue(
                code="build_validation_failed",
                message=f"{type(exc).__name__}: {exc}",
                package_id=resolved.spec.package_id,
            )
        )
    _write_report(resolved.build_report, report)
    return report


def publish_worldbook_package(
    build_spec_path: str | Path,
    allowed_removed_ids: set[str] | None = None,
    allow_all_removed: bool = False,
    reactivate_ids: set[str] | None = None,
    reactivate_all: bool = False,
    rebuild_callback: IndexRebuildCallback | None = None,
) -> WorldbookBuildReport:
    """审计 staging 后替换正式包，并对全部世界书索引执行一次重建。"""

    resolved = load_build_spec(build_spec_path)
    report = _new_report(resolved.spec.package_id, "publish", resolved.build_report)
    staging_parent = resolved.build_root / "staging"
    staging_package = staging_parent / resolved.spec.package_id
    try:
        validate_package_version_not_lower(
            resolved.official_root,
            resolved.spec.package_id,
            resolved.spec.package_version,
        )
        identity_map = load_identity_map(resolved.id_map, resolved.spec.package_id)
        resolver = IdentityResolver(
            identity_map,
            allocate=True,
            reactivate_ids=reactivate_ids,
            reactivate_all=reactivate_all,
        )
        if staging_package.exists():
            shutil.rmtree(staging_package)
        built = build_staging_package(resolved, resolver, staging_package)
        resolver.finalize_removed(allowed_removed_ids or set(), allow_all_removed)
        report.input_sha256 = built.input_sha256
        report.identity_changes = resolver.changes
        report.staging_path = str(staging_package)
        save_identity_map(resolved.id_map, identity_map)
        report.issues = audit_staging_packages(
            resolved.official_root,
            {resolved.spec.package_id: staging_package},
        )
        if report.issues:
            raise ValueError("staging 或预期官方包集合审计失败")
        target = resolved.official_root / resolved.spec.package_id
        _replace_package_directory(staging_package, target, resolved.official_root)
        report.package_published = True
        callback = rebuild_callback or _default_rebuild_callback
        rebuilt, readiness = callback(_derive_app_root(resolved.official_root))
        report.index_rebuilt = rebuilt
        report.index_readiness = readiness
        if not rebuilt:
            raise RuntimeError("正式包已替换，但世界书索引全量重建失败")
        report.succeeded = True
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        report.issues.append(
            ValidationIssue(
                code="publish_failed",
                message=f"{type(exc).__name__}: {exc}",
                package_id=resolved.spec.package_id,
            )
        )
    _write_report(resolved.build_report, report)
    return report


def publish_worldbook_packages(
    batch_spec_path: str | Path,
    allowed_removed_ids: set[str] | None = None,
    allow_all_removed: bool = False,
    reactivate_ids: set[str] | None = None,
    reactivate_all: bool = False,
    rebuild_callback: IndexRebuildCallback | None = None,
) -> WorldbookBatchBuildReport:
    """先共同审计全部 staging，再依次替换并且只重建一次索引。"""

    batch_path = Path(batch_spec_path).resolve()
    try:
        batch_spec = WorldbookBatchBuildSpec.model_validate_json(batch_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ValueError(f"无效的 worldbook batch build spec: {batch_path}") from exc
    report_path_value = Path(batch_spec.build_report)
    report_path = (
        (batch_path.parent / report_path_value).resolve()
        if not report_path_value.is_absolute()
        else report_path_value.resolve()
    )
    batch_report = WorldbookBatchBuildReport(report_path=str(report_path))
    resolved_specs = [
        load_build_spec(
            (batch_path.parent / item).resolve() if not Path(item).is_absolute() else Path(item).resolve()
        )
        for item in batch_spec.build_specs
    ]
    package_ids = [item.spec.package_id for item in resolved_specs]
    if len(package_ids) != len(set(package_ids)):
        batch_report.issues.append(ValidationIssue(code="duplicate_package_id", message="批量配置不得重复 package_id"))
        _write_batch_report(report_path, batch_report)
        return batch_report
    official_roots = {item.official_root for item in resolved_specs}
    if len(official_roots) != 1:
        batch_report.issues.append(ValidationIssue(code="mixed_official_roots", message="一次批量发布必须使用同一 official_root"))
        _write_batch_report(report_path, batch_report)
        return batch_report
    official_root = next(iter(official_roots))
    staging_packages: dict[str, Path] = {}
    identity_maps = []
    try:
        for resolved in resolved_specs:
            package_report = _new_report(resolved.spec.package_id, "publish", resolved.build_report)
            batch_report.package_reports.append(package_report)
            validate_package_version_not_lower(
                official_root,
                resolved.spec.package_id,
                resolved.spec.package_version,
            )
            identity_map = load_identity_map(resolved.id_map, resolved.spec.package_id)
            resolver = IdentityResolver(
                identity_map,
                allocate=True,
                reactivate_ids=reactivate_ids,
                reactivate_all=reactivate_all,
            )
            staging_package = resolved.build_root / "batch-staging" / resolved.spec.package_id
            if staging_package.exists():
                shutil.rmtree(staging_package)
            built = build_staging_package(resolved, resolver, staging_package)
            resolver.finalize_removed(allowed_removed_ids or set(), allow_all_removed)
            package_report.input_sha256 = built.input_sha256
            package_report.identity_changes = resolver.changes
            package_report.staging_path = str(staging_package)
            staging_packages[resolved.spec.package_id] = staging_package
            identity_maps.append((resolved.id_map, identity_map))
        for path, identity_map in identity_maps:
            save_identity_map(path, identity_map)
        audit_issues = audit_staging_packages(official_root, staging_packages)
        if audit_issues:
            batch_report.issues.extend(audit_issues)
            raise ValueError("批量 staging 与预期官方包集合审计失败")
        for resolved in resolved_specs:
            _replace_package_directory(
                staging_packages[resolved.spec.package_id],
                official_root / resolved.spec.package_id,
                official_root,
            )
            package_report = next(
                item for item in batch_report.package_reports if item.package_id == resolved.spec.package_id
            )
            package_report.package_published = True
        callback = rebuild_callback or _default_rebuild_callback
        rebuilt, readiness = callback(_derive_app_root(official_root))
        batch_report.index_rebuilt = rebuilt
        batch_report.index_readiness = readiness
        for package_report in batch_report.package_reports:
            package_report.index_rebuilt = rebuilt
            package_report.index_readiness = readiness
            package_report.succeeded = rebuilt
        if not rebuilt:
            raise RuntimeError("正式包已替换，但批量发布后的索引全量重建失败")
        batch_report.succeeded = True
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        batch_report.issues.append(
            ValidationIssue(code="batch_publish_failed", message=f"{type(exc).__name__}: {exc}")
        )
        for package_report in batch_report.package_reports:
            if not package_report.succeeded:
                package_report.issues.extend(batch_report.issues)
    for resolved, package_report in zip(resolved_specs, batch_report.package_reports):
        _write_report(resolved.build_report, package_report)
    _write_batch_report(report_path, batch_report)
    return batch_report


def _replace_package_directory(staging: Path, target: Path, official_root: Path) -> None:
    """删除精确目标后把已审计 staging 目录整体移入正式根目录。"""

    official_root = official_root.resolve()
    target = target.resolve()
    if target.parent != official_root:
        raise ValueError("正式包目标必须是 official_root 的直接子目录")
    official_root.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)


def _derive_app_root(official_root: Path) -> Path:
    """从标准 GPT_SoVITS/rag/worldbooks/official 路径推导应用根目录。"""

    resolved = official_root.resolve()
    expected_suffix = ("GPT_SoVITS", "rag", "worldbooks", "official")
    if tuple(resolved.parts[-4:]) != expected_suffix:
        raise ValueError("official_root 不是标准应用路径，无法确定索引重建的 app root")
    return resolved.parents[3]


def _default_rebuild_callback(app_root: Path) -> tuple[bool, str | None]:
    """调用现有 worker 对四张世界书 collection 执行全量重建。"""

    exit_code = run_worker(app_root, "rebuild")
    return exit_code == 0, "ready" if exit_code == 0 else "unavailable"


def _new_report(package_id: str, command: str, report_path: Path) -> WorldbookBuildReport:
    """创建带尽力 Git commit 的单次构建报告。"""

    normalized_command: Literal["validate", "publish"] = "publish" if command == "publish" else "validate"
    return WorldbookBuildReport(
        package_id=package_id,
        command=normalized_command,
        git_commit=_git_commit(),
        report_path=str(report_path),
    )


def _git_commit() -> str | None:
    """尽力读取当前 Git commit，不检查或记录 dirty 状态。"""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value or None


def _write_report(path: Path, report: WorldbookBuildReport) -> None:
    """覆盖写入不属于正式包的 build report。"""

    _write_json_report(path, report.model_dump(mode="json"))


def _write_batch_report(path: Path, report: WorldbookBatchBuildReport) -> None:
    """覆盖写入批量发布报告。"""

    _write_json_report(path, report.model_dump(mode="json"))


def _write_json_report(path: Path, payload: object) -> None:
    """使用同目录临时文件安全覆盖单次构建报告。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
