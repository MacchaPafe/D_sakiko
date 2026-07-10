from __future__ import annotations

import fnmatch
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileSelectionRules:
    """描述文件选择器的五层规则。"""

    allow: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    hard_exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileSelectionResult:
    """保存文件选择结果及其审计信息。"""

    selected: frozenset[str]
    excluded_reasons: dict[str, str]
    forced_untracked: frozenset[str]


def normalize_relative_path(path: str) -> str:
    """把候选路径统一成不带开头斜杠的 POSIX 表示。"""

    return path.replace("\\", "/").lstrip("/")


def matching_pattern(path: str, patterns: Iterable[str]) -> str | None:
    """返回路径命中的第一条 fnmatch 规则。"""

    relative = normalize_relative_path(path)
    name = Path(relative).name
    for pattern in patterns:
        normalized = normalize_relative_path(pattern)
        if fnmatch.fnmatch(relative, normalized) or fnmatch.fnmatch(name, normalized):
            return pattern
    return None


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    """判断路径是否命中任意 fnmatch 规则。"""

    return matching_pattern(path, patterns) is not None


def directory_might_contain_include(relative_dir: str, include_patterns: Iterable[str]) -> bool:
    """判断被排除目录中是否可能存在显式 include 文件。"""

    directory = normalize_relative_path(relative_dir).strip("/")
    if not directory:
        return True
    directory_parts = directory.split("/")
    for pattern in include_patterns:
        normalized = normalize_relative_path(pattern).strip("/")
        if not normalized:
            continue
        if "**" in normalized:
            return True
        pattern_parts = normalized.split("/")
        if len(directory_parts) < len(pattern_parts):
            if all(fnmatch.fnmatch(part, rule) for part, rule in zip(directory_parts, pattern_parts)):
                return True
        elif fnmatch.fnmatch(directory, normalized):
            return True
    return False


def list_directory_files(root: Path, rules: FileSelectionRules | None = None) -> set[str]:
    """扫描目录中的普通文件，并在安全时剪枝排除目录。"""

    effective = rules or FileSelectionRules()
    files_found: set[str] = set()
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        kept_directories: list[str] = []
        for directory_name in directories:
            relative_dir = (current_path / directory_name).relative_to(root).as_posix()
            probe = f"{relative_dir}/__selection_probe__"
            if path_matches(probe, effective.hard_exclude):
                continue
            if path_matches(probe, effective.exclude) and not directory_might_contain_include(
                relative_dir, effective.include
            ):
                continue
            kept_directories.append(directory_name)
        directories[:] = kept_directories

        for file_name in files:
            file_path = current_path / file_name
            if file_path.is_file():
                files_found.add(file_path.relative_to(root).as_posix())
    return files_found


def list_git_tracked_files(root: Path, *, fail_on_error: bool = False) -> set[str]:
    """读取 Git 追踪文件；可选择在读取失败时抛出异常。"""

    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        if fail_on_error:
            raise RuntimeError(f"无法读取 Git 追踪文件：{root}") from exc
        return set()
    return {
        normalize_relative_path(item.decode("utf-8", errors="surrogateescape"))
        for item in completed.stdout.split(b"\0")
        if item
    }


def list_git_untracked_files(root: Path, *, fail_on_error: bool = False) -> set[str]:
    """读取未被忽略的 Git 未追踪文件。"""

    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        if fail_on_error:
            raise RuntimeError(f"无法读取 Git 未追踪文件：{root}") from exc
        return set()
    return {
        normalize_relative_path(item.decode("utf-8", errors="surrogateescape"))
        for item in completed.stdout.split(b"\0")
        if item
    }


def select_files(
    root: Path,
    base_candidates: Iterable[str],
    rules: FileSelectionRules,
    *,
    include_candidates: Iterable[str] = (),
    tracked_candidates: Iterable[str] | None = None,
) -> FileSelectionResult:
    """按固定五层顺序选择存在于 root 中的文件。"""

    base = {normalize_relative_path(path) for path in base_candidates}
    additions = {normalize_relative_path(path) for path in include_candidates}
    tracked = None if tracked_candidates is None else {
        normalize_relative_path(path) for path in tracked_candidates
    }
    selected: set[str] = set()
    excluded: dict[str, str] = {}
    forced_untracked: set[str] = set()

    for relative in sorted(base | additions):
        hard_pattern = matching_pattern(relative, rules.hard_exclude)
        if hard_pattern is not None:
            excluded[relative] = f"hard_exclude:{hard_pattern}"
            continue
        include_pattern = matching_pattern(relative, rules.include)
        allow_pattern = matching_pattern(relative, rules.allow)
        if rules.allow and allow_pattern is None and include_pattern is None:
            excluded[relative] = "not_allowed"
            continue
        exclude_pattern = matching_pattern(relative, rules.exclude)
        if exclude_pattern is not None and include_pattern is None:
            excluded[relative] = f"exclude:{exclude_pattern}"
            continue
        target = root / relative
        if not target.is_file():
            excluded[relative] = "missing"
            continue
        selected.add(relative)
        if tracked is not None and relative not in tracked and include_pattern is not None:
            forced_untracked.add(relative)

    return FileSelectionResult(
        selected=frozenset(selected),
        excluded_reasons=excluded,
        forced_untracked=frozenset(forced_untracked),
    )


def collect_selected_files(
    root: Path,
    rules: FileSelectionRules,
    *,
    use_git_tracked: bool,
    fail_on_git_error: bool = False,
) -> FileSelectionResult:
    """从 Git 追踪集合或目录扫描集合收集并选择文件。"""

    scanned = list_directory_files(root, rules)
    if not use_git_tracked:
        return select_files(root, scanned, rules)
    tracked = list_git_tracked_files(root, fail_on_error=fail_on_git_error)
    if not tracked and not fail_on_git_error:
        return select_files(root, scanned, rules)
    include_candidates = {path for path in scanned if path_matches(path, rules.include)}
    return select_files(
        root,
        tracked,
        rules,
        include_candidates=include_candidates,
        tracked_candidates=tracked,
    )
