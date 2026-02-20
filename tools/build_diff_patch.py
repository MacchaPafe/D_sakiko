#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_IGNORE_PATTERNS = [
    "._*",
    ".DS_Store",
    "*.wav",
    "__pycache__/*",
    ".venv/*",
    ".idea/*",
    ".git/*",
    ".python-version",
    ".vscode/*",
    "*.bat",
    "d_sakiko_config.json",
    "dsakiko_config.json",
    ".github/*",
    ".gitignore",
    "tools/*",
    "GPT_SoVITS/live2d_1_generate.py",
    "GPT_SoVITS/setup_live2d.py"
]


DEFAULT_INCLUDE_PATTERNS = [
    "GPT_SoVITS/live2d_1.cpython-311-darwin.so"
]


@dataclass
class FileRecord:
    """描述单个文件在更新中的动作与校验信息。"""

    path: str
    action: str
    sha256: str
    size: int


def parse_args() -> argparse.Namespace:
    """解析构建 hdiff 补丁所需的命令行参数。"""

    parser = argparse.ArgumentParser(
        description="hdiffz 构建包装器：按黑白名单筛选目录并生成目录差分补丁。"
    )
    parser.add_argument("--current", required=True, help="当前工作目录路径（新版本）。")
    parser.add_argument("--old", required=True, help="旧版本目录路径。")
    parser.add_argument("--output", required=True, help="补丁输出目录路径。")
    parser.add_argument("--base-version", required=True, help="补丁生成针对哪个旧版本。")
    parser.add_argument("--target-version", required=True, help="补丁应用后的新版本号。")
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="忽略哪些文件（支持 glob），可多次传入。",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="强制包含哪些文件（支持 glob），可多次传入。",
    )
    parser.add_argument(
        "--read-gitignore",
        action="store_true",
        help="附加读取 current 目录下 .gitignore 作为忽略规则（推荐）。",
    )
    parser.add_argument(
        "--use-git-tracked",
        action="store_true",
        help="优先基于 git 跟踪文件构建 current 文件集（推荐）。",
    )
    parser.add_argument("--clean-output", action="store_true", help="若输出目录已存在，先清空再写入。")
    parser.add_argument(
        "--warn-remove-file",
        default="warnings_remove_files.txt",
        help="将被删除文件的告警清单文件名（保存在输出目录内）。",
    )
    parser.add_argument(
        "--manifest",
        default="manifest.json",
        help="更新清单文件名（保存在输出目录内）。",
    )
    parser.add_argument(
        "--patch-file",
        default="patch.hdiff",
        help="输出差分文件名（保存在输出目录内）。",
    )
    parser.add_argument(
        "--hdiff-bin",
        default="tools/hdiffz",
        help="hdiffz 可执行文件路径（可相对 current 目录）。",
    )
    parser.add_argument(
        "--hdiff-option",
        action="append",
        default=[],
        help="传递给 hdiffz 的额外选项，可多次传入。",
    )
    return parser.parse_args()


def sha256_file(file_path: Path, block_size: int = 1024 * 1024) -> str:
    """计算文件 SHA256，用于差异判断与补丁校验。"""

    digest = hashlib.sha256()
    with file_path.open("rb") as file:
        while True:
            data = file.read(block_size)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def read_gitignore_patterns(base_dir: Path) -> list[str]:
    """读取并规范化 .gitignore（仅取忽略规则，不处理反向规则）。"""

    gitignore_file = base_dir / ".gitignore"
    if not gitignore_file.exists():
        return []

    patterns: list[str] = []
    for raw in gitignore_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        normalized = line.lstrip("/")
        if normalized.endswith("/"):
            normalized = normalized + "**"
        patterns.append(normalized)
    return patterns


def should_ignore(rel_path: str, ignore_patterns: Iterable[str]) -> bool:
    """判断相对路径是否命中忽略规则。"""

    rel_posix = rel_path.replace("\\", "/")
    name = Path(rel_posix).name
    for pattern in ignore_patterns:
        normalized = pattern.replace("\\", "/")
        if fnmatch.fnmatch(rel_posix, normalized) or fnmatch.fnmatch(name, normalized):
            return True
    return False


def should_force_include(rel_path: str, include_patterns: Iterable[str]) -> bool:
    """判断相对路径是否命中强制包含规则。"""

    rel_posix = rel_path.replace("\\", "/")
    name = Path(rel_posix).name
    for pattern in include_patterns:
        normalized = pattern.replace("\\", "/")
        if fnmatch.fnmatch(rel_posix, normalized) or fnmatch.fnmatch(name, normalized):
            return True
    return False


def list_files(base_dir: Path, ignore_patterns: list[str], include_patterns: list[str]) -> set[str]:
    """列出目录内满足筛选规则的全部文件相对路径集合。"""

    result: set[str] = set()
    for file in base_dir.rglob("*"):
        if not file.is_file():
            continue
        rel = file.relative_to(base_dir).as_posix()
        # 筛选规则为：
        # 1. 命中白名单，强制保留（即使文件同时在黑名单）
        # 2. 命中黑名单，忽略
        # 3. 其他正常保留
        if should_ignore(rel, ignore_patterns) and not should_force_include(rel, include_patterns):
            continue
        result.add(rel)
    return result


def list_git_tracked_files(base_dir: Path) -> set[str]:
    """读取 Git 跟踪文件列表，失败时返回空集合。"""

    try:
        completed = subprocess.run(
            ["git", "-C", str(base_dir), "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()

    tracked: set[str] = set()
    # 使用 -z 输出避免中文路径被 quotePath 转义；按字节解码后再标准化路径分隔符。
    for raw_item in completed.stdout.split(b"\0"):
        if not raw_item:
            continue
        file = raw_item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        if file:
            tracked.add(file)
    return tracked


def resolve_filtered_files(
    current_root: Path,
    old_root: Path,
    ignore_patterns: list[str],
    include_patterns: list[str],
    use_git_tracked: bool,
) -> tuple[set[str], set[str]]:
    """按黑白名单与 Git 跟踪策略，得到新旧版本的可比较文件集合。"""
    # 列出旧版本目录下的所有文件
    # 在旧版本目录下不使用 git 跟踪文件列表，因为旧版本可能不在 git 仓库中，或跟踪状态不可靠。
    old_files = list_files(old_root, ignore_patterns, include_patterns)

    # 如果扫描新版本文件夹时，不使用 git 跟踪文件列表，则直接按黑白名单规则扫描目录，得到当前版本文件集合。
    if not use_git_tracked:
        current_files = list_files(current_root, ignore_patterns, include_patterns)
        return current_files, old_files

    # 如果使用 git 跟踪文件列表，优先获取 git 跟踪文件集合，作为当前版本文件的初始候选集合。
    tracked_files = list_git_tracked_files(current_root)
    if not tracked_files:
        print("[警告] 未获取到 git 跟踪文件，回退到目录扫描。")
        current_files = list_files(current_root, ignore_patterns, include_patterns)
        return current_files, old_files

    # 寻找强制包含的文件集合，补充到 git 跟踪文件集合中，确保它们不会被忽略规则过滤掉。
    forced_include = {
        file
        for file in list_files(current_root, [], include_patterns)
        if should_force_include(file, include_patterns)
    }

    selected: set[str] = set()
    for file in tracked_files | forced_include:
        # 白名单优先于黑名单：若同时命中，仍保留该文件。
        if should_ignore(file, ignore_patterns) and not should_force_include(file, include_patterns):
            continue
        if (current_root / file).is_file():
            selected.add(file)

    return selected, old_files


def calculate_changes(
    current_root: Path,
    old_root: Path,
    current_files: set[str],
    old_files: set[str],
) -> tuple[list[str], list[str], list[str]]:
    """比较两个文件集合，返回删除/新增/修改三类路径。"""

    remove_files = sorted(old_files - current_files)
    added_files = sorted(current_files - old_files)

    changed_files: list[str] = []
    for relative_path in sorted(current_files & old_files):
        if sha256_file(current_root / relative_path) != sha256_file(old_root / relative_path):
            changed_files.append(relative_path)

    return remove_files, added_files, changed_files


def build_file_records(
    current_root: Path,
    remove_files: list[str],
    added_files: list[str],
    changed_files: list[str],
) -> list[FileRecord]:
    """将差异结果转换为清单记录，供 manifest 和应用端使用。"""

    records: list[FileRecord] = []
    for rel in added_files:
        path = current_root / rel
        records.append(FileRecord(path=rel, action="add", sha256=sha256_file(path), size=path.stat().st_size))
    for rel in changed_files:
        path = current_root / rel
        records.append(FileRecord(path=rel, action="modify", sha256=sha256_file(path), size=path.stat().st_size))
    for rel in remove_files:
        records.append(FileRecord(path=rel, action="remove", sha256="", size=0))
    return records


def stage_tree(source_root: Path, relative_paths: Iterable[str], stage_root: Path) -> None:
    """按给定相对路径把源目录文件复制到临时 staging 目录。"""

    for relative_path in sorted(set(relative_paths)):
        source_file = source_root / relative_path
        if not source_file.is_file():
            continue
        target = stage_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)


def run_hdiff(
    hdiff_bin: Path,
    old_stage: Path,
    new_stage: Path,
    out_diff_file: Path,
    options: list[str],
) -> None:
    """调用 hdiffz 生成目录差分文件，失败时抛出异常。"""

    command = [str(hdiff_bin), *options, str(old_stage), str(new_stage), str(out_diff_file)]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        raise RuntimeError(f"hdiffz 执行失败：{stderr or stdout or '未知错误'}")


def write_remove_warning_file(output_root: Path, warn_file_name: str, remove_files: list[str]) -> Path:
    """写出“将删除文件”告警清单，便于发布前人工复核。"""

    warning_file = output_root / warn_file_name
    if remove_files:
        text = "更新时将执行删除（已在清单中声明）的文件：\n" + "\n".join(remove_files) + "\n"
    else:
        text = "未发现需要删除的文件。\n"
    warning_file.write_text(text, encoding="utf-8")
    return warning_file


def write_manifest(
    output_root: Path,
    manifest_name: str,
    patch_file_name: str,
    current_root: Path,
    old_root: Path,
    base_version: str,
    target_version: str,
    ignore_patterns: list[str],
    include_patterns: list[str],
    records: list[FileRecord],
    remove_files: list[str],
    added_files: list[str],
    changed_files: list[str],
) -> Path:
    """生成更新清单 manifest.json，记录版本、文件动作和统计信息。"""

    manifest_file = output_root / manifest_name
    manifest = {
        "format_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "hdiff",
        "base_version": base_version,
        "target_version": target_version,
        "patch_file": patch_file_name,
        "current": str(current_root),
        "old": str(old_root),
        "ignore_patterns": ignore_patterns,
        "include_patterns": include_patterns,
        "files": [
            {
                "path": item.path,
                "action": item.action,
                "sha256": item.sha256,
                "size": item.size,
            }
            for item in records
        ],
        "remove_files": remove_files,
        "stats": {
            "remove_count": len(remove_files),
            "added_count": len(added_files),
            "changed_count": len(changed_files),
            "file_count": len(records),
        },
    }
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_file


def main() -> int:
    """脚本入口：筛选文件、生成 hdiff 补丁并输出 manifest。"""

    args = parse_args()

    # 当前（新版本）目录路径
    current_root = Path(args.current).expanduser().resolve()
    # 旧版本目录路径
    old_root = Path(args.old).expanduser().resolve()
    # 补丁输出目录路径
    output_root = Path(args.output).expanduser().resolve()

    if not current_root.exists() or not current_root.is_dir():
        print(f"错误：当前目录不存在或不是文件夹：{current_root}", file=sys.stderr)
        return 1
    if not old_root.exists() or not old_root.is_dir():
        print(f"错误：旧版本目录不存在或不是文件夹：{old_root}", file=sys.stderr)
        return 1

    hdiff_bin = Path(args.hdiff_bin)
    # 如果 hdiffz 路径是相对路径，通过两种方式搜索可执行文件
    if not hdiff_bin.is_absolute():
        # 优先按当前工作目录解析，便于在仓库根目录直接执行。
        cwd_candidate = (Path.cwd() / hdiff_bin).resolve()
        if cwd_candidate.exists():
            hdiff_bin = cwd_candidate
        else:
            # 回退到以 --current 为基准解析，兼容打包机脚本化调用。
            hdiff_bin = (current_root / hdiff_bin).resolve()
    if not hdiff_bin.exists():
        print(f"错误：未找到 hdiffz 可执行文件：{hdiff_bin}", file=sys.stderr)
        return 1
    if not os.access(hdiff_bin, os.X_OK):
        print(f"错误：hdiffz 不可执行，请先 chmod +x：{hdiff_bin}", file=sys.stderr)
        return 1

    # 列出需要忽略的文件模式
    ignore_patterns = list(DEFAULT_IGNORE_PATTERNS)
    if args.read_gitignore:
        ignore_patterns.extend(read_gitignore_patterns(current_root))
    ignore_patterns.extend(args.ignore)
    # 列出需要强制包含的文件模式
    include_patterns = list(DEFAULT_INCLUDE_PATTERNS)
    include_patterns.extend(args.include)

    # 准备输出目录：如果已存在且 --clean-output，则先清空再创建；否则直接创建（已存在则覆盖）。
    if output_root.exists() and args.clean_output:
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    # 列出所有新文件，旧文件并比较
    current_files, old_files = resolve_filtered_files(
        current_root=current_root,
        old_root=old_root,
        ignore_patterns=ignore_patterns,
        include_patterns=include_patterns,
        use_git_tracked=args.use_git_tracked,
    )
    # 计算差异，得到删除/新增/修改三类文件列表
    remove_files, added_files, changed_files = calculate_changes(
        current_root=current_root,
        old_root=old_root,
        current_files=current_files,
        old_files=old_files,
    )
    # 构建文件记录列表，供 manifest 和应用端使用
    records = build_file_records(
        current_root=current_root,
        remove_files=remove_files,
        added_files=added_files,
        changed_files=changed_files,
    )

    patch_path = output_root / args.patch_file
    with tempfile.TemporaryDirectory(prefix="d_sakiko_hdiff_") as temp_dir:
        # 创建新/旧版本两个目录，只放入所有满足扫描条件的旧文件和新文件
        temp_root = Path(temp_dir)
        old_stage = temp_root / "old"
        new_stage = temp_root / "new"
        old_stage.mkdir(parents=True, exist_ok=True)
        new_stage.mkdir(parents=True, exist_ok=True)
        stage_tree(old_root, old_files, old_stage)
        stage_tree(current_root, current_files, new_stage)
        # 调用 hdiffz 生成差分文件，输入为两个 staging 目录，输出为指定路径的差分文件
        run_hdiff(hdiff_bin=hdiff_bin, old_stage=old_stage, new_stage=new_stage, out_diff_file=patch_path, options=args.hdiff_option)

    warning_file = write_remove_warning_file(output_root, args.warn_remove_file, remove_files)
    manifest_file = write_manifest(
        output_root=output_root,
        manifest_name=args.manifest,
        patch_file_name=args.patch_file,
        current_root=current_root,
        old_root=old_root,
        base_version=args.base_version,
        target_version=args.target_version,
        ignore_patterns=ignore_patterns,
        include_patterns=include_patterns,
        records=records,
        remove_files=remove_files,
        added_files=added_files,
        changed_files=changed_files,
    )

    if remove_files:
        print("[警告] 发现需要删除的文件，请确认：")
        for relative_path in remove_files:
            print(f"  - {relative_path}")
    else:
        print("[信息] 未发现需要删除的文件。")

    print(f"[完成] hdiff 模式：已生成差分文件 {patch_path.name}。")
    print(f"[输出] 删除告警：{warning_file}")
    print(f"[输出] 更新清单：{manifest_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
