#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import platform
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

APP_ID = "D_sakiko"
CHANNEL = "stable"
REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_FILE = REPO_ROOT / "tools" / "release" / "update_patch_profiles.json"
LOCAL_FILE = REPO_ROOT / "tools" / "release" / "update_patch.local.json"
LOCAL_EXAMPLE_FILE = REPO_ROOT / "tools" / "release" / "update_patch.local.example.json"
USER_ASSET_PATTERNS = (
    "d_sakiko_config.json",
    "dsakiko_config.json",
    "live2d_related/*",
    "live2d_related/**",
    "reference_audio/*",
    "reference_audio/**",
    "font/*",
    "font/**",
)


class BuildError(RuntimeError):
    """表示发布补丁构建入口发现了不可继续的错误。"""


@dataclass(frozen=True)
class CliOptions:
    """保存命令行中与确认和覆盖行为相关的选项。"""

    allow_non_forward_version: bool
    allow_cross_platform_build: bool
    allow_pyproject_version_mismatch: bool
    confirm_untracked_include: bool
    confirm_user_assets_change: bool
    yes: bool
    no_zip: bool
    dry_run: bool


@dataclass(frozen=True)
class BuildConfig:
    """描述一次发布补丁构建所需的完整配置。"""

    profile: str
    current: Path
    old: Path
    output: Path
    base_version: str
    target_version: str
    platform_name: str
    arch: str
    app_id: str
    channel: str
    min_updater_version: str
    manifest: str
    patch_file: str
    hdiff_bin: Path
    hpatch_bin: Path
    read_gitignore: bool
    use_git_tracked: bool
    clean_output: bool
    no_platform_includes: bool
    ignore: list[str]
    include: list[str]
    zip_output: Path


def parse_args() -> argparse.Namespace:
    """解析统一发布补丁入口的命令行参数。"""

    parser = argparse.ArgumentParser(description="统一构建 D_sakiko 更新补丁并执行发布前校验。")
    parser.add_argument("--profile", required=True, help="发布 profile 名称，例如 macos-arm64。")
    parser.add_argument("--base-version", required=True, help="补丁基础版本。")
    parser.add_argument("--target-version", required=True, help="补丁目标版本。")
    parser.add_argument("--current", default="", help="当前新版本目录，默认来自 profile。")
    parser.add_argument("--old", default="", help="旧版本目录；省略时按 local 配置自动查找。")
    parser.add_argument("--old-versions-root", default="", help="旧版本目录根路径，覆盖 local 配置。")
    parser.add_argument("--old-dir-pattern", default="", help="旧版本目录匹配模板，支持 {base_version}。")
    parser.add_argument("--output", default="", help="补丁输出目录，默认来自 profile。")
    parser.add_argument("--platform", default="", choices=["", "macos", "windows"], help="覆盖 profile 平台。")
    parser.add_argument("--arch", default="", choices=["", "x64", "arm64", "universal"], help="覆盖 profile 架构。")
    parser.add_argument("--ignore", action="append", default=[], help="追加忽略规则，可重复。")
    parser.add_argument("--include", action="append", default=[], help="追加强制包含规则，可重复。")
    parser.add_argument("--zip-output", default="", help="patch zip 输出路径；默认使用规范文件名。")
    parser.add_argument("--app-id", default="", help="覆盖 app_id。")
    parser.add_argument("--channel", default="", help="覆盖 channel。")
    parser.add_argument("--min-updater-version", default="", help="覆盖最低更新器版本。")
    parser.add_argument("--hdiff-bin", default="", help="覆盖 hdiffz 路径。")
    parser.add_argument("--hpatch-bin", default="", help="覆盖 hpatchz 路径。")
    parser.add_argument("--read-gitignore", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--use-git-tracked", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--clean-output", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--no-platform-includes", action="store_true", default=None)
    parser.add_argument("--allow-non-forward-version", action="store_true", help="允许 target_version 不大于 base_version。")
    parser.add_argument("--allow-cross-platform-build", action="store_true", help="允许当前机器平台与 profile 平台不一致。")
    parser.add_argument("--allow-pyproject-version-mismatch", action="store_true", help="允许 pyproject.toml 版本与 target 不一致。")
    parser.add_argument("--confirm-untracked-include", action="store_true", help="自动确认未跟踪文件命中 include。")
    parser.add_argument("--confirm-user-assets-change", action="store_true", help="自动确认用户资源文件变更。")
    parser.add_argument("--yes", action="store_true", help="自动同意所有确认型风险，不绕过硬错误。")
    parser.add_argument("--no-zip", action="store_true", help="只生成 patch_files，不生成 patch zip。")
    parser.add_argument("--dry-run", action="store_true", help="只执行构建前校验并打印底层命令。")
    return parser.parse_args()


def load_json_object(path: Path, required: bool) -> dict[str, JsonValue]:
    """读取 JSON 对象文件，缺失的可选文件返回空字典。"""

    if not path.exists():
        if required:
            raise BuildError(f"缺少配置文件：{path}")
        return {}
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BuildError(f"配置文件顶层必须是对象：{path}")
    return {str(key): value for key, value in data.items()}


def object_field(data: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    """读取对象字段，字段缺失时返回空对象。"""

    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BuildError(f"配置字段 {key} 必须是对象")
    return {str(item_key): item_value for item_key, item_value in value.items()}


def string_field(data: dict[str, JsonValue], key: str, default: str = "") -> str:
    """读取字符串配置字段。"""

    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise BuildError(f"配置字段 {key} 必须是字符串")
    return value


def bool_field(data: dict[str, JsonValue], key: str, default: bool) -> bool:
    """读取布尔配置字段。"""

    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise BuildError(f"配置字段 {key} 必须是布尔值")
    return value


def string_list_field(data: dict[str, JsonValue], key: str) -> list[str]:
    """读取字符串列表配置字段。"""

    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise BuildError(f"配置字段 {key} 必须是字符串数组")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise BuildError(f"配置字段 {key} 必须只包含字符串")
        result.append(item)
    return result


def overlay_profile_override(local_config: dict[str, JsonValue], profile: str) -> dict[str, JsonValue]:
    """读取 local 配置中针对当前 profile 的覆盖项。"""

    overrides = object_field(local_config, "profile_overrides")
    value = overrides.get(profile)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BuildError(f"profile_overrides.{profile} 必须是对象")
    return {str(key): item for key, item in value.items()}


def choose_string(
    key: str,
    cli_value: str,
    profile_data: dict[str, JsonValue],
    defaults: dict[str, JsonValue],
    local_override: dict[str, JsonValue],
    default: str = "",
) -> str:
    """按命令行、local 覆盖、profile、defaults 的顺序选择字符串字段。"""

    if cli_value:
        return cli_value
    local_value = string_field(local_override, key, "")
    if local_value:
        return local_value
    profile_value = string_field(profile_data, key, "")
    if profile_value:
        return profile_value
    return string_field(defaults, key, default)


def choose_bool(
    key: str,
    cli_value: bool | None,
    profile_data: dict[str, JsonValue],
    defaults: dict[str, JsonValue],
    local_override: dict[str, JsonValue],
    default: bool,
) -> bool:
    """按命令行、local 覆盖、profile、defaults 的顺序选择布尔字段。"""

    if cli_value is not None:
        return cli_value
    if key in local_override:
        return bool_field(local_override, key, default)
    if key in profile_data:
        return bool_field(profile_data, key, default)
    return bool_field(defaults, key, default)


def version_key(version: str) -> tuple[int, ...]:
    """将版本号转换成可比较的数字元组。"""

    numbers = [int(item) for item in re.findall(r"\d+", version)]
    return tuple(numbers or [0])


def compare_versions(left: str, right: str) -> int:
    """比较两个版本号，返回 -1、0 或 1。"""

    left_parts = list(version_key(left))
    right_parts = list(version_key(right))
    max_len = max(len(left_parts), len(right_parts))
    left_parts.extend([0] * (max_len - len(left_parts)))
    right_parts.extend([0] * (max_len - len(right_parts)))
    if left_parts < right_parts:
        return -1
    if left_parts > right_parts:
        return 1
    return 0


def validate_version_text(version: str, field_name: str) -> None:
    """检查版本文本可用于路径和 manifest。"""

    if not version.strip():
        raise BuildError(f"{field_name} 不能为空")
    if "/" in version or "\\" in version or ".." in version:
        raise BuildError(f"{field_name} 包含非法路径字符：{version!r}")


def resolve_relative_to_root(root: Path, value: str) -> Path:
    """将路径按仓库根目录解析为绝对路径。"""

    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def detect_current_platform() -> str:
    """返回当前运行机器对应的发布平台名称。"""

    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return sys.platform


def find_old_directory(
    root: Path,
    base_version: str,
    explicit_old: str,
    old_versions_root: str,
    old_dir_pattern: str,
) -> Path:
    """根据显式参数或本地配置定位旧版本目录。"""

    if explicit_old:
        return Path(explicit_old).expanduser().resolve()
    if not old_versions_root:
        raise BuildError(
            f"未指定 --old，且未配置 old_versions_root。可复制 {LOCAL_EXAMPLE_FILE.relative_to(REPO_ROOT)} "
            f"为 {LOCAL_FILE.relative_to(REPO_ROOT)} 后修改。"
        )
    pattern = old_dir_pattern or "D_sakiko_{base_version}_*"
    search_root = resolve_relative_to_root(root, old_versions_root)
    if not search_root.exists() or not search_root.is_dir():
        raise BuildError(f"旧版本根目录不存在或不是目录：{search_root}")
    matches = sorted(item for item in search_root.glob(pattern.format(base_version=base_version)) if item.is_dir())
    if not matches:
        raise BuildError(f"未找到旧版本目录：{search_root / pattern.format(base_version=base_version)}")
    if len(matches) > 1:
        text = "\n".join(f"  - {item}" for item in matches)
        raise BuildError(f"旧版本目录匹配到多个结果，请用 --old 明确指定：\n{text}")
    return matches[0].resolve()


def build_config(args: argparse.Namespace, repo_root: Path) -> BuildConfig:
    """合并共享 profile、本地配置和命令行参数。"""

    profile_config = load_json_object(PROFILE_FILE, required=True)
    local_config = load_json_object(LOCAL_FILE, required=False)
    defaults = object_field(profile_config, "defaults")
    profiles = object_field(profile_config, "profiles")
    raw_profile = profiles.get(args.profile)
    if raw_profile is None:
        names = ", ".join(sorted(profiles))
        raise BuildError(f"profile 不存在：{args.profile}。可用 profile：{names}")
    if not isinstance(raw_profile, dict):
        raise BuildError(f"profile {args.profile} 必须是对象")
    profile_data = {str(key): value for key, value in raw_profile.items()}
    local_override = overlay_profile_override(local_config, args.profile)

    base_version = str(args.base_version).strip()
    target_version = str(args.target_version).strip()
    validate_version_text(base_version, "base_version")
    validate_version_text(target_version, "target_version")

    old_versions_root = args.old_versions_root or string_field(local_override, "old_versions_root", "")
    if not old_versions_root:
        old_versions_root = string_field(local_config, "old_versions_root", "")
    old_dir_pattern = args.old_dir_pattern or string_field(local_override, "old_dir_pattern", "")
    if not old_dir_pattern:
        old_dir_pattern = string_field(local_config, "old_dir_pattern", "D_sakiko_{base_version}_*")

    current_text = choose_string("current", args.current, profile_data, defaults, local_override, ".")
    output_text = choose_string("output", args.output, profile_data, defaults, local_override, "patch_files")
    platform_name = choose_string("platform", args.platform, profile_data, defaults, local_override)
    arch = choose_string("arch", args.arch, profile_data, defaults, local_override)
    app_id = choose_string("app_id", args.app_id, profile_data, defaults, local_override, APP_ID)
    channel = choose_string("channel", args.channel, profile_data, defaults, local_override, CHANNEL)
    min_updater_version = choose_string("min_updater_version", args.min_updater_version, profile_data, defaults, local_override, "1.0.0")
    manifest = choose_string("manifest", "", profile_data, defaults, local_override, "manifest.json")
    patch_file = choose_string("patch_file", "", profile_data, defaults, local_override, "patch.hdiff")
    hdiff_text = choose_string("hdiff_bin", args.hdiff_bin, profile_data, defaults, local_override, "tools/hdiffz")
    hpatch_text = choose_string("hpatch_bin", args.hpatch_bin, profile_data, defaults, local_override, "tools/hpatchz")

    if platform_name not in {"macos", "windows"}:
        raise BuildError(f"profile 展开后 platform 必须是 macos 或 windows，当前为：{platform_name or '<缺失>'}")
    if arch not in {"x64", "arm64", "universal"}:
        raise BuildError(f"profile 展开后 arch 必须是 x64、arm64 或 universal，当前为：{arch or '<缺失>'}")

    ignore = [
        *string_list_field(defaults, "ignore"),
        *string_list_field(profile_data, "ignore"),
        *string_list_field(local_config, "extra_ignore"),
        *string_list_field(local_override, "ignore"),
        *args.ignore,
    ]
    include = [
        *string_list_field(defaults, "include"),
        *string_list_field(profile_data, "include"),
        *string_list_field(local_config, "extra_include"),
        *string_list_field(local_override, "include"),
        *args.include,
    ]

    current = resolve_relative_to_root(repo_root, current_text)
    old = find_old_directory(
        root=repo_root,
        base_version=base_version,
        explicit_old=args.old,
        old_versions_root=old_versions_root,
        old_dir_pattern=old_dir_pattern,
    )
    output = resolve_relative_to_root(current, output_text)
    if args.zip_output:
        zip_output = Path(args.zip_output).expanduser().resolve()
    else:
        zip_output = current / f"D_sakiko_{base_version}_to_{target_version}_{platform_name}_{arch}.patch.zip"

    return BuildConfig(
        profile=args.profile,
        current=current,
        old=old,
        output=output,
        base_version=base_version,
        target_version=target_version,
        platform_name=platform_name,
        arch=arch,
        app_id=app_id,
        channel=channel,
        min_updater_version=min_updater_version,
        manifest=manifest,
        patch_file=patch_file,
        hdiff_bin=resolve_relative_to_root(current, hdiff_text),
        hpatch_bin=resolve_relative_to_root(current, hpatch_text),
        read_gitignore=choose_bool("read_gitignore", args.read_gitignore, profile_data, defaults, local_override, True),
        use_git_tracked=choose_bool("use_git_tracked", args.use_git_tracked, profile_data, defaults, local_override, True),
        clean_output=choose_bool("clean_output", args.clean_output, profile_data, defaults, local_override, True),
        no_platform_includes=choose_bool("no_platform_includes", args.no_platform_includes, profile_data, defaults, local_override, False),
        ignore=ignore,
        include=include,
        zip_output=zip_output,
    )


def read_version_file(path: Path) -> str:
    """读取 version.json 中的版本号。"""

    if not path.exists():
        raise BuildError(f"缺少版本文件：{path}")
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BuildError(f"版本文件顶层必须是对象：{path}")
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise BuildError(f"版本文件缺少 version 字段：{path}")
    return version.strip()


def read_pyproject_version(path: Path) -> str:
    """读取 pyproject.toml 中的 project.version。"""

    if not path.exists():
        raise BuildError(f"缺少 pyproject.toml：{path}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict):
        raise BuildError("pyproject.toml 缺少 [project] 表")
    version = project.get("version")
    if not isinstance(version, str) or not version.strip():
        raise BuildError("pyproject.toml 缺少 project.version")
    return version.strip()


def run_command(command: list[str], cwd: Path, check: bool) -> subprocess.CompletedProcess[bytes]:
    """运行命令并返回结果。"""

    return subprocess.run(command, cwd=cwd, capture_output=True, check=check)


def ensure_git_ignored(repo_root: Path, path: Path) -> None:
    """确认本地配置文件已被 Git 忽略。"""

    if not path.exists():
        return
    relative = path.relative_to(repo_root).as_posix()
    completed = run_command(["git", "check-ignore", "-q", relative], cwd=repo_root, check=False)
    if completed.returncode != 0:
        raise BuildError(f"{relative} 存在但没有被 .gitignore 忽略，请先补充忽略规则，避免提交本地路径。")


def assert_git_ready(config: BuildConfig) -> None:
    """检查 Git 仓库状态是否满足发布入口要求。"""

    if not config.use_git_tracked:
        return
    rev_parse = run_command(["git", "rev-parse", "--is-inside-work-tree"], cwd=config.current, check=False)
    if rev_parse.returncode != 0 or rev_parse.stdout.strip() != b"true":
        raise BuildError("启用了 use_git_tracked，但当前目录不是 Git 仓库。")
    ls_files = run_command(["git", "ls-files", "-z"], cwd=config.current, check=False)
    if ls_files.returncode != 0:
        stderr = ls_files.stderr.decode("utf-8", errors="replace").strip()
        raise BuildError(f"git ls-files 失败，拒绝回退到目录扫描：{stderr or '<无 stderr>'}")


def rel_path_matches(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    """判断相对路径是否命中 glob 规则。"""

    rel_posix = path.replace("\\", "/")
    name = Path(rel_posix).name
    for pattern in patterns:
        normalized = pattern.replace("\\", "/")
        if fnmatch.fnmatch(rel_posix, normalized) or fnmatch.fnmatch(name, normalized):
            return True
    return False


def find_untracked_includes(config: BuildConfig) -> list[str]:
    """找出命中 include 规则的未跟踪文件。"""

    if not config.use_git_tracked or not config.include:
        return []
    completed = run_command(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=config.current, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise BuildError(f"git ls-files --others 失败：{stderr or '<无 stderr>'}")
    result: list[str] = []
    for raw_item in completed.stdout.split(b"\0"):
        if not raw_item:
            continue
        relative_path = raw_item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        if rel_path_matches(relative_path, config.include):
            result.append(relative_path)
    return sorted(result)


def confirm_or_raise(title: str, details: list[str], accepted: bool) -> None:
    """对可确认风险进行命令行确认，非交互环境中要求显式参数。"""

    if not details:
        return
    if accepted:
        return
    print(title, file=sys.stderr)
    for item in details:
        print(f"  - {item}", file=sys.stderr)
    if not sys.stdin.isatty():
        raise BuildError("当前不是交互终端，请添加对应确认参数或 --yes 后重试。")
    answer = input("是否继续？[y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        raise BuildError("用户取消构建。")


def assert_tool_executable(path: Path, name: str) -> None:
    """确认工具文件存在且可执行。"""

    if not path.exists():
        raise BuildError(f"缺少 {name}：{path}")
    if not os.access(path, os.X_OK):
        raise BuildError(f"{name} 不可执行，请先 chmod +x：{path}")


def assert_output_clean(config: BuildConfig) -> None:
    """确认输出目录不会被旧产物污染。"""

    if not config.output.exists():
        return
    if not config.output.is_dir():
        raise BuildError(f"输出路径已存在但不是目录：{config.output}")
    has_content = next(config.output.iterdir(), None) is not None
    if has_content and not config.clean_output:
        raise BuildError(f"输出目录已存在且非空，但未启用 clean_output：{config.output}")


def preflight(config: BuildConfig, options: CliOptions, repo_root: Path) -> None:
    """执行构建前校验。"""

    ensure_git_ignored(repo_root, repo_root / LOCAL_FILE)
    if config.base_version == config.target_version:
        raise BuildError("base_version 不能等于 target_version。")
    if compare_versions(config.target_version, config.base_version) <= 0 and not options.allow_non_forward_version:
        raise BuildError("target_version 不大于 base_version；如确需这样做，请添加 --allow-non-forward-version。")
    if not config.current.exists() or not config.current.is_dir():
        raise BuildError(f"当前目录不存在或不是目录：{config.current}")
    if not config.old.exists() or not config.old.is_dir():
        raise BuildError(f"旧版本目录不存在或不是目录：{config.old}")
    if config.current == config.old:
        raise BuildError("--current 和 --old 解析到了同一个目录。")
    current_version = read_version_file(config.current / "version.json")
    if current_version != config.target_version:
        raise BuildError(f"当前 version.json 版本为 {current_version}，不等于 target_version {config.target_version}。")
    old_version = read_version_file(config.old / "version.json")
    if old_version != config.base_version:
        raise BuildError(f"旧目录 version.json 版本为 {old_version}，不等于 base_version {config.base_version}。")
    pyproject_version = read_pyproject_version(config.current / "pyproject.toml")
    if pyproject_version != config.target_version and not options.allow_pyproject_version_mismatch:
        raise BuildError(
            f"pyproject.toml 版本为 {pyproject_version}，不等于 target_version {config.target_version}。"
        )
    current_platform = detect_current_platform()
    if current_platform != config.platform_name and not options.allow_cross_platform_build:
        raise BuildError(
            f"当前机器平台为 {current_platform}，profile 平台为 {config.platform_name}；"
            "如需交叉构建，请添加 --allow-cross-platform-build。"
        )
    assert_git_ready(config)
    assert_output_clean(config)
    assert_tool_executable(config.hdiff_bin, "hdiffz")
    assert_tool_executable(config.hpatch_bin, "hpatchz")
    untracked_includes = find_untracked_includes(config)
    confirm_or_raise(
        "发现未跟踪文件命中 include 规则，将被强制纳入更新包：",
        untracked_includes,
        options.confirm_untracked_include or options.yes,
    )


def build_command(config: BuildConfig, no_zip: bool) -> list[str]:
    """构造传给底层 build_diff_patch.py 的命令。"""

    command = [
        sys.executable,
        str(config.current / "tools" / "build_diff_patch.py"),
        "--current",
        str(config.current),
        "--old",
        str(config.old),
        "--output",
        str(config.output),
        "--base-version",
        config.base_version,
        "--target-version",
        config.target_version,
        "--platform",
        config.platform_name,
        "--arch",
        config.arch,
        "--app-id",
        config.app_id,
        "--channel",
        config.channel,
        "--min-updater-version",
        config.min_updater_version,
        "--manifest",
        config.manifest,
        "--patch-file",
        config.patch_file,
        "--hdiff-bin",
        str(config.hdiff_bin),
    ]
    if config.read_gitignore:
        command.append("--read-gitignore")
    if config.use_git_tracked:
        command.append("--use-git-tracked")
    if config.clean_output:
        command.append("--clean-output")
    if config.no_platform_includes:
        command.append("--no-platform-includes")
    for pattern in config.ignore:
        command.extend(["--ignore", pattern])
    for pattern in config.include:
        command.extend(["--include", pattern])
    if not no_zip:
        command.extend(["--zip-output", str(config.zip_output)])
    return command


def normalize_manifest_path(value: object, field_name: str) -> str:
    """校验并规范化 manifest 中的相对路径。"""

    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"{field_name} 必须是非空字符串")
    if "\\" in value:
        raise BuildError(f"{field_name} 不能包含反斜杠：{value!r}")
    path = PurePosixPath(value.strip())
    if path.is_absolute():
        raise BuildError(f"{field_name} 必须是相对路径：{value!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise BuildError(f"{field_name} 不能包含空路径、当前目录或上级目录：{value!r}")
    return path.as_posix()


def require_manifest_string(manifest: dict[str, JsonValue], key: str) -> str:
    """读取 manifest 中的必填字符串字段。"""

    value = manifest.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"manifest.{key} 必须是非空字符串")
    return value.strip()


def require_manifest_list(manifest: dict[str, JsonValue], key: str) -> list[JsonValue]:
    """读取 manifest 中的必填数组字段。"""

    value = manifest.get(key)
    if not isinstance(value, list):
        raise BuildError(f"manifest.{key} 必须是数组")
    return value


def check_manifest_file_record(item: JsonValue) -> tuple[str, str]:
    """校验 manifest.files 中的一条文件记录。"""

    if not isinstance(item, dict):
        raise BuildError("manifest.files[] 必须是对象")
    record = {str(key): value for key, value in item.items()}
    relative_path = normalize_manifest_path(record.get("path"), "files[].path")
    action = record.get("action")
    if action not in {"add", "modify", "remove"}:
        raise BuildError(f"不支持的文件动作：{action!r}")
    sha256 = record.get("sha256")
    old_sha256 = record.get("old_file_sha256")
    size = record.get("size")
    if action in {"add", "modify"}:
        if not isinstance(sha256, str) or not sha256.strip():
            raise BuildError(f"{relative_path} 缺少 sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise BuildError(f"{relative_path} size 异常：{size!r}")
    if action in {"modify", "remove"}:
        if not isinstance(old_sha256, str) or not old_sha256.strip():
            raise BuildError(f"{relative_path} 缺少 old_file_sha256")
    return relative_path, str(action)


def load_manifest(path: Path) -> dict[str, JsonValue]:
    """读取生成后的 manifest.json。"""

    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BuildError(f"manifest 顶层必须是对象：{path}")
    return {str(key): value for key, value in data.items()}


def postflight(config: BuildConfig, options: CliOptions) -> None:
    """执行构建后 manifest 校验和确认型风险检查。"""

    manifest_path = config.output / config.manifest
    if not manifest_path.exists():
        raise BuildError(f"构建后缺少 manifest：{manifest_path}")
    manifest = load_manifest(manifest_path)
    expected_values = {
        "app_id": config.app_id,
        "channel": config.channel,
        "base_version": config.base_version,
        "target_version": config.target_version,
        "platform": config.platform_name,
        "arch": config.arch,
    }
    for key, expected in expected_values.items():
        actual = require_manifest_string(manifest, key)
        if actual != expected:
            raise BuildError(f"manifest.{key} = {actual!r}，不等于预期 {expected!r}")
    if require_manifest_string(manifest, "min_updater_version") == "":
        raise BuildError("manifest.min_updater_version 不能为空")
    patch_file = normalize_manifest_path(manifest.get("patch_file"), "patch_file")
    if patch_file != config.patch_file:
        raise BuildError(f"manifest.patch_file = {patch_file!r}，不等于预期 {config.patch_file!r}")
    if not (config.output / patch_file).exists():
        raise BuildError(f"manifest.patch_file 指向的文件不存在：{config.output / patch_file}")

    changed_user_assets: list[str] = []
    for item in require_manifest_list(manifest, "files"):
        relative_path, action = check_manifest_file_record(item)
        if rel_path_matches(relative_path, USER_ASSET_PATTERNS):
            changed_user_assets.append(f"{action}: {relative_path}")
    confirm_or_raise(
        "manifest 声明会新增、修改或删除用户资源/配置文件：",
        changed_user_assets,
        options.confirm_user_assets_change or options.yes,
    )


def current_git_commit(current: Path) -> str:
    """读取当前 Git commit，失败时返回空字符串。"""

    completed = run_command(["git", "rev-parse", "HEAD"], cwd=current, check=False)
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode("utf-8", errors="replace").strip()


def write_build_metadata(config: BuildConfig, command: list[str]) -> None:
    """写出本次补丁构建的追踪元数据。"""

    metadata: dict[str, JsonValue] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": config.profile,
        "base_version": config.base_version,
        "target_version": config.target_version,
        "platform": config.platform_name,
        "arch": config.arch,
        "current": str(config.current),
        "old": str(config.old),
        "output": str(config.output),
        "zip_output": str(config.zip_output),
        "git_commit": current_git_commit(config.current),
        "read_gitignore": config.read_gitignore,
        "use_git_tracked": config.use_git_tracked,
        "clean_output": config.clean_output,
        "ignore": config.ignore,
        "include": config.include,
        "command": command,
    }
    metadata_path = config.output / "build_metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[输出] 构建元数据：{metadata_path}")


def run_build(config: BuildConfig, options: CliOptions) -> None:
    """运行底层补丁构建命令并执行构建后检查。"""

    command = build_command(config, no_zip=options.no_zip)
    print("[信息] 底层构建命令：")
    print(" ".join(json.dumps(part, ensure_ascii=False) for part in command))
    if options.dry_run:
        print("[信息] dry-run：已跳过实际构建。")
        return
    completed = subprocess.run(command, cwd=config.current)
    if completed.returncode != 0:
        raise BuildError(f"build_diff_patch.py 执行失败，退出码：{completed.returncode}")
    postflight(config, options)
    write_build_metadata(config, command)
    if not options.no_zip:
        if not config.zip_output.exists():
            raise BuildError(f"构建后缺少 patch zip：{config.zip_output}")
        print(f"[输出] patch zip：{config.zip_output}")


def main() -> int:
    """脚本入口。"""

    args = parse_args()
    options = CliOptions(
        allow_non_forward_version=args.allow_non_forward_version,
        allow_cross_platform_build=args.allow_cross_platform_build,
        allow_pyproject_version_mismatch=args.allow_pyproject_version_mismatch,
        confirm_untracked_include=args.confirm_untracked_include,
        confirm_user_assets_change=args.confirm_user_assets_change,
        yes=args.yes,
        no_zip=args.no_zip,
        dry_run=args.dry_run,
    )
    repo_root = REPO_ROOT
    try:
        config = build_config(args, repo_root)
        preflight(config, options, repo_root)
        run_build(config, options)
    except BuildError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
