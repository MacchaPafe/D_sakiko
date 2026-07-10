#!/usr/bin/env python3
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import shutil
import stat
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "GPT_SoVITS"))
sys.path.insert(0, str(REPO_ROOT))

from repair.repair_manifest import (
    APP_ID,
    MANIFEST_SCHEMA,
    RepairFileEntry,
    RepairManifest,
    manifest_to_dict,
    object_relative_path,
    parse_manifest_bytes,
    sha256_file,
    version_key,
)
from tools.release.file_selection import (
    FileSelectionResult,
    FileSelectionRules,
    list_directory_files,
    list_git_tracked_files,
    path_matches,
    select_files,
)


PROFILE_FILE = REPO_ROOT / "tools" / "release" / "repair_asset_profiles.json"


class RepairBuildError(RuntimeError):
    """表示修复资产生成无法安全继续。"""


@dataclass(frozen=True)
class RepairBuildProfile:
    """保存合并后的修复资产 profile。"""

    name: str
    app_id: str
    channel: str
    platform_name: str
    arch: str
    min_repair_client_version: str
    rules: FileSelectionRules


@dataclass(frozen=True)
class RepairBuildResult:
    """保存一次修复资产生成的输出信息。"""

    output_dir: Path
    manifest_file: Path
    audit_file: Path
    zip_file: Path | None
    manifest: RepairManifest
    selection: FileSelectionResult


def parse_args() -> argparse.Namespace:
    """解析修复资产生成命令行参数。"""

    parser = argparse.ArgumentParser(description="从实际发布包生成 D_sakiko 修复 manifest 和 objects。")
    parser.add_argument("--profile", required=True, help="repair profile，例如 macos-arm64。")
    parser.add_argument("--package-root", required=True, help="实际发布软件包根目录。")
    parser.add_argument("--version", required=True, help="软件包版本。")
    parser.add_argument("--source-root", default=str(REPO_ROOT), help="提供 Git 追踪路径的源码仓库。")
    parser.add_argument("--profile-file", default=str(PROFILE_FILE), help="repair profile JSON。")
    parser.add_argument("--output", default="", help="交付目录；默认写入 dist/repair。")
    parser.add_argument("--no-zip", action="store_true", help="不生成自包含 zip。")
    parser.add_argument("--overwrite-output", action="store_true", help="允许覆盖已有输出目录和 zip。")
    return parser.parse_args()


def _load_json_object(path: Path) -> dict[str, object]:
    """读取顶层为对象的 JSON 文件。"""

    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepairBuildError(f"无法读取 JSON：{path}，错误：{exc}") from exc
    if not isinstance(value, dict):
        raise RepairBuildError(f"JSON 顶层必须是对象：{path}")
    return {str(key): item for key, item in value.items()}


def _require_object(data: dict[str, object], key: str) -> dict[str, object]:
    """读取必需的对象字段。"""

    value = data.get(key)
    if not isinstance(value, dict):
        raise RepairBuildError(f"配置字段 {key} 必须是对象")
    return {str(item_key): item for item_key, item in value.items()}


def _require_string(data: dict[str, object], key: str, default: str = "") -> str:
    """读取字符串配置字段。"""

    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise RepairBuildError(f"配置字段 {key} 必须是非空字符串")
    return value.strip()


def _string_list(data: dict[str, object], key: str) -> list[str]:
    """读取可选字符串数组字段。"""

    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RepairBuildError(f"配置字段 {key} 必须是字符串数组")
    return [item for item in value if isinstance(item, str)]


def load_profile(profile_file: Path, profile_name: str) -> RepairBuildProfile:
    """加载 defaults 与指定平台 profile。"""

    data = _load_json_object(profile_file)
    defaults = _require_object(data, "defaults")
    profiles = _require_object(data, "profiles")
    raw_profile = profiles.get(profile_name)
    if not isinstance(raw_profile, dict):
        raise RepairBuildError(f"不存在 repair profile：{profile_name}")
    profile = {str(key): value for key, value in raw_profile.items()}

    def merged_patterns(key: str) -> tuple[str, ...]:
        """合并默认和平台路径规则。"""

        return tuple([*_string_list(defaults, key), *_string_list(profile, key)])

    min_version = _require_string(defaults, "min_repair_client_version")
    version_key(min_version)
    return RepairBuildProfile(
        name=profile_name,
        app_id=_require_string(defaults, "app_id", APP_ID),
        channel=_require_string(defaults, "channel", "stable"),
        platform_name=_require_string(profile, "platform"),
        arch=_require_string(profile, "arch"),
        min_repair_client_version=min_version,
        rules=FileSelectionRules(
            allow=merged_patterns("allow"),
            include=merged_patterns("include"),
            exclude=merged_patterns("exclude"),
            hard_exclude=merged_patterns("hard_exclude"),
        ),
    )


def _read_package_version(package_root: Path) -> str:
    """读取实际发布包中的版本号。"""

    data = _load_json_object(package_root / "version.json")
    return _require_string(data, "version")


def _prepare_output(output_dir: Path, zip_file: Path | None, overwrite: bool) -> None:
    """按覆盖策略准备干净输出路径。"""

    existing = [path for path in (output_dir, zip_file) if path is not None and path.exists()]
    if existing and not overwrite:
        raise RepairBuildError("输出已存在，请更换路径或添加 --overwrite-output：" + ", ".join(map(str, existing)))
    if output_dir.exists():
        shutil.rmtree(output_dir)
    if zip_file is not None and zip_file.exists():
        zip_file.unlink()
    output_dir.mkdir(parents=True)


def select_package_files(
    source_root: Path,
    package_root: Path,
    profile: RepairBuildProfile,
) -> FileSelectionResult:
    """以 Git 追踪路径和实际发布包交集选择修复文件。"""

    tracked = list_git_tracked_files(source_root, fail_on_error=True)
    scanned = list_directory_files(package_root, profile.rules)
    explicit_includes = {path for path in scanned if path_matches(path, profile.rules.include)}
    return select_files(
        package_root,
        tracked,
        profile.rules,
        include_candidates=explicit_includes,
        tracked_candidates=tracked,
    )


def _file_mode(path: Path) -> str:
    """把文件权限位格式化成 manifest mode。"""

    return f"{stat.S_IMODE(path.stat().st_mode):04o}"


def _build_manifest(
    package_root: Path,
    profile: RepairBuildProfile,
    version: str,
    selected_paths: frozenset[str],
) -> RepairManifest:
    """根据发布包原始文件生成 manifest 模型。"""

    entries = tuple(
        RepairFileEntry(
            path=relative,
            sha256=sha256_file(package_root / relative),
            size=(package_root / relative).stat().st_size,
            mode=_file_mode(package_root / relative),
        )
        for relative in sorted(selected_paths)
    )
    return RepairManifest(
        schema=MANIFEST_SCHEMA,
        app_id=profile.app_id,
        channel=profile.channel,
        version=version,
        platform=profile.platform_name,
        arch=profile.arch,
        min_repair_client_version=profile.min_repair_client_version,
        generated_at=datetime.now(timezone.utc).isoformat(),
        files=entries,
    )


def _write_objects(package_root: Path, output_dir: Path, manifest: RepairManifest) -> int:
    """复制 manifest 引用的全部内容寻址 objects。"""

    written_hashes: set[str] = set()
    for entry in manifest.files:
        if entry.sha256 in written_hashes:
            continue
        destination = output_dir / object_relative_path(entry.sha256)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package_root / entry.path, destination)
        written_hashes.add(entry.sha256)
    return len(written_hashes)


def _write_audit_report(
    output_dir: Path,
    manifest: RepairManifest,
    selection: FileSelectionResult,
    object_count: int,
) -> Path:
    """输出供最终上传方人工复核的 Markdown 报告。"""

    reasons = Counter(selection.excluded_reasons.values())
    missing = sorted(path for path, reason in selection.excluded_reasons.items() if reason == "missing")
    large_files = [entry for entry in manifest.files if entry.size >= 1024 * 1024]
    lines = [
        "# 修复资产审计报告",
        "",
        f"- 应用：`{manifest.app_id}`",
        f"- 版本：`{manifest.version}`",
        f"- 平台：`{manifest.platform}-{manifest.arch}`",
        f"- 纳入文件：{len(manifest.files)}",
        f"- 纳入总大小：{sum(entry.size for entry in manifest.files)} bytes",
        f"- 去重后 objects：{object_count}",
        f"- 强制纳入的未追踪文件：{len(selection.forced_untracked)}",
        "",
        "## 强制纳入的未追踪文件",
        "",
        *([f"- `{path}`" for path in sorted(selection.forced_untracked)] or ["- 无"]),
        "",
        "## 规则排除摘要",
        "",
        *([f"- `{reason}`：{count}" for reason, count in sorted(reasons.items())] or ["- 无"]),
        "",
        "## 发布包缺失的可维护候选",
        "",
        *([f"- `{path}`" for path in missing] or ["- 无"]),
        "",
        "## 大于等于 1 MiB 的纳入文件",
        "",
        *([f"- `{entry.path}`：{entry.size} bytes" for entry in large_files] or ["- 无"]),
        "",
        "## 大小写不敏感路径冲突",
        "",
        "- 无（manifest 生成校验已通过）",
        "",
    ]
    audit_file = output_dir / "audit-report.md"
    audit_file.write_text("\n".join(lines), encoding="utf-8")
    return audit_file


def _write_zip(output_dir: Path, zip_file: Path) -> None:
    """把交付目录打包成自包含 zip。"""

    with zipfile.ZipFile(zip_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(output_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(output_dir).as_posix())


def zip_path_for_output(output_dir: Path) -> Path:
    """在完整交付目录名后追加 .zip，保留版本号和平台字段。"""

    return output_dir.parent / f"{output_dir.name}.zip"


def build_delivery(
    source_root: Path,
    package_root: Path,
    profile: RepairBuildProfile,
    version: str,
    output_dir: Path,
    *,
    zip_file: Path | None,
    overwrite: bool,
) -> RepairBuildResult:
    """生成并自校验完整修复资产交付物。"""

    if not source_root.is_dir() or not package_root.is_dir():
        raise RepairBuildError("source-root 和 package-root 都必须是现有目录")
    version_key(version)
    package_version = _read_package_version(package_root)
    if package_version != version:
        raise RepairBuildError(f"发布包版本 {package_version} 与参数版本 {version} 不一致")
    _prepare_output(output_dir, zip_file, overwrite)
    selection = select_package_files(source_root, package_root, profile)
    manifest = _build_manifest(package_root, profile, version, selection.selected)
    manifest_bytes = (json.dumps(manifest_to_dict(manifest), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    parse_manifest_bytes(manifest_bytes, check_case_conflicts=True)
    manifest_file = output_dir / "manifest.json"
    manifest_file.write_bytes(manifest_bytes)
    object_count = _write_objects(package_root, output_dir, manifest)
    audit_file = _write_audit_report(output_dir, manifest, selection, object_count)
    if zip_file is not None:
        _write_zip(output_dir, zip_file)
    return RepairBuildResult(
        output_dir=output_dir,
        manifest_file=manifest_file,
        audit_file=audit_file,
        zip_file=zip_file,
        manifest=manifest,
        selection=selection,
    )


def main() -> int:
    """命令行入口。"""

    args = parse_args()
    try:
        source_root = Path(args.source_root).expanduser().resolve()
        package_root = Path(args.package_root).expanduser().resolve()
        profile = load_profile(Path(args.profile_file).expanduser().resolve(), args.profile)
        output_dir = (
            Path(args.output).expanduser().resolve()
            if args.output
            else source_root / "dist" / "repair" / f"{profile.app_id}_{args.version}_{args.profile}_repair_assets"
        )
        zip_file = None if args.no_zip else zip_path_for_output(output_dir)
        result = build_delivery(
            source_root,
            package_root,
            profile,
            args.version,
            output_dir,
            zip_file=zip_file,
            overwrite=args.overwrite_output,
        )
    except (RepairBuildError, ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(f"[完成] manifest：{result.manifest_file}")
    print(f"[完成] 审计报告：{result.audit_file}")
    if result.zip_file is not None:
        print(f"[完成] 交付 zip：{result.zip_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
