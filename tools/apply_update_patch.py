#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TouchRecord:
    """记录一次更新过程中被触达的路径及其更新前是否存在。"""

    path: str
    existed_before: bool


def parse_args() -> argparse.Namespace:
    """解析应用 hdiff 补丁所需参数。"""

    parser = argparse.ArgumentParser(description="应用 hdiffpatch 更新包。")
    parser.add_argument("--app-root", default=".", help="程序根目录（默认当前目录）。")
    parser.add_argument(
        "--package",
        default="",
        help="指定更新包目录；不传时自动扫描 app-root 下所有含 manifest.json+patch.hdiff 的子目录。",
    )
    parser.add_argument("--manifest", default="manifest.json", help="更新包清单文件名。")
    parser.add_argument("--version-file", default="version.json", help="程序版本文件名。")
    parser.add_argument("--backup-dir", default=".update_backup", help="回滚备份目录名。")
    parser.add_argument("--hpatch-bin", default="tools/hpatchz", help="hpatchz 可执行文件路径（可相对 app-root）。")
    return parser.parse_args()


def sha256_file(file_path: Path, block_size: int = 1024 * 1024) -> str:
    """计算文件 SHA256，用于应用后校验。"""

    digest = hashlib.sha256()
    with file_path.open("rb") as file:
        while True:
            data = file.read(block_size)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def load_json(file_path: Path) -> dict:
    """读取 JSON 文件并返回字典对象。"""

    return json.loads(file_path.read_text(encoding="utf-8"))


def write_json(file_path: Path, content: dict) -> None:
    """将字典按 UTF-8 JSON 格式写回文件。"""

    file_path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_hpatch_bin(app_root: Path, hpatch_bin_arg: str) -> Path:
    """解析 hpatchz 路径：优先 cwd 相对路径，其次 app_root 相对路径。"""

    hpatch_bin = Path(hpatch_bin_arg)
    if not hpatch_bin.is_absolute():
        cwd_candidate = (Path.cwd() / hpatch_bin).resolve()
        if cwd_candidate.exists():
            hpatch_bin = cwd_candidate
        else:
            hpatch_bin = (app_root / hpatch_bin).resolve()
    return hpatch_bin


def resolve_package_root(app_root: Path, package_arg: str) -> Path:
    """将用户传入的更新包路径解析为绝对路径。"""

    package_path = Path(package_arg)
    if package_path.is_absolute():
        return package_path
    return (app_root / package_path).resolve()


def discover_package_roots(app_root: Path, manifest_name: str) -> list[Path]:
    """自动发现更新包目录：主目录下同时含 manifest.json 和 patch.hdiff 的子目录。"""

    package_roots: list[Path] = []
    for child in sorted(app_root.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        if (child / manifest_name).exists() and (child / "patch.hdiff").exists():
            package_roots.append(child)
    return package_roots


def ensure_parent(path: Path) -> None:
    """确保目标路径的父目录存在。"""

    path.parent.mkdir(parents=True, exist_ok=True)


def backup_if_exists(app_root: Path, backup_root: Path, relative_path: str) -> bool:
    """若目标路径存在则备份到回滚目录，并返回是否成功备份。"""

    src = app_root / relative_path
    if not src.exists():
        return False
    dst = backup_root / "files" / relative_path
    ensure_parent(dst)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return True


def restore_backup(app_root: Path, backup_root: Path, touch_records: list[TouchRecord]) -> None:
    """按触达记录逆序回滚，恢复更新前文件状态。"""

    for item in reversed(touch_records):
        # 逆序恢复可避免父子路径相互覆盖导致的数据错位。
        target = app_root / item.path
        backup_path = backup_root / "files" / item.path
        if item.existed_before:
            if backup_path.exists():
                if backup_path.is_dir():
                    if target.exists() and target.is_file():
                        target.unlink()
                    shutil.copytree(backup_path, target, dirs_exist_ok=True)
                else:
                    ensure_parent(target)
                    shutil.copy2(backup_path, target)
        else:
            if target.is_file() or target.is_symlink():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target, ignore_errors=True)


def remove_path(path: Path) -> None:
    """删除文件/链接/目录（若不存在则忽略）。"""

    if path.is_file() or path.is_symlink():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def set_executable(file_path: Path) -> None:
    """为目标文件增加可执行权限位。"""

    if not file_path.exists() or not file_path.is_file():
        return
    mode = file_path.stat().st_mode
    file_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def remove_quarantine(file_path: Path) -> None:
    """移除 macOS quarantine 属性，减少首次运行拦截。"""

    if not file_path.exists():
        return
    subprocess.run(
        ["xattr", "-d", "com.apple.quarantine", str(file_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def should_make_executable(relative_path: str) -> bool:
    """判断路径是否需要在补丁后设为可执行。"""

    path = relative_path.replace("\\", "/")
    return path.endswith(".command") or path in {"tools/hdiffz", "tools/hpatchz"}


def should_clear_quarantine(relative_path: str) -> bool:
    """判断路径是否需要清除 quarantine 属性。"""

    path = relative_path.replace("\\", "/")
    return path.endswith(".command") or path.endswith(".so") or path in {"tools/hdiffz", "tools/hpatchz"}


def verify_manifest_and_version(manifest: dict, app_version: str) -> tuple[str, str]:
    """校验 manifest 模式与版本匹配关系，返回 base/target 版本。"""

    mode = str(manifest.get("mode", "")).strip()
    if mode != "hdiff":
        raise RuntimeError(f"仅支持 hdiff 更新包，当前 mode={mode!r}")

    base_version = str(manifest.get("base_version", "")).strip()
    target_version = str(manifest.get("target_version", "")).strip()
    if not base_version or not target_version:
        raise RuntimeError("manifest 缺少 base_version/target_version")
    if app_version != base_version:
        raise RuntimeError(f"版本不匹配：当前 {app_version}，补丁要求的基础版本为 {base_version}")
    return base_version, target_version


def read_current_version(version_file: Path) -> str:
    """读取当前版本号。"""

    version_data = load_json(version_file)
    app_version = str(version_data.get("version", "")).strip()
    if not app_version:
        raise RuntimeError("version.json 缺少 version 字段")
    return app_version


def remove_applied_package(package_root: Path) -> None:
    """删除已成功应用的更新包目录。"""

    if not package_root.exists():
        return
    if not package_root.is_dir():
        raise RuntimeError(f"更新包路径不是目录，拒绝删除：{package_root}")
    shutil.rmtree(package_root)


def apply_hdiff(
    app_root: Path,
    package_root: Path,
    manifest: dict,
    backup_root: Path,
    touch_records: list[TouchRecord],
    hpatch_bin: Path,
) -> list[str]:
    """调用 hpatchz 应用目录差分，并按清单落地文件与删除动作。"""

    patch_file = manifest.get("patch_file")
    if not patch_file:
        raise RuntimeError("manifest 缺少 patch_file")

    patch_path = package_root / str(patch_file)
    if not patch_path.exists():
        raise RuntimeError(f"找不到差分文件：{patch_path}")

    if not hpatch_bin.exists():
        raise RuntimeError(f"找不到 hpatchz：{hpatch_bin}")
    if not os.access(hpatch_bin, os.X_OK):
        raise RuntimeError(f"hpatchz 不可执行，请先 chmod +x：{hpatch_bin}")

    with tempfile.TemporaryDirectory(prefix="d_sakiko_hpatch_") as temp_dir:
        # 先把补丁输出到临时目录，校验通过后再覆盖应用目录，便于失败回滚。
        staged_new_root = Path(temp_dir) / "new_root"
        command = [str(hpatch_bin), "-f", str(app_root), str(patch_path), str(staged_new_root)]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            raise RuntimeError(f"hpatchz 执行失败：{stderr or stdout or '未知错误'}")

        processed_paths: list[str] = []
        for item in manifest.get("files", []):
            action = item.get("action")
            relative_path = item.get("path")
            if not relative_path:
                continue

            target = app_root / relative_path
            existed_before = backup_if_exists(app_root, backup_root, relative_path)
            touch_records.append(TouchRecord(path=relative_path, existed_before=existed_before))

            if action in {"add", "modify"}:
                source = staged_new_root / relative_path
                if not source.exists():
                    raise RuntimeError(f"hpatch 输出缺少文件：{relative_path}")

                expected_sha = str(item.get("sha256") or "").strip()
                # 只要清单提供了哈希，就强制校验，防止损坏补丁写入运行目录。
                if expected_sha and sha256_file(source) != expected_sha:
                    raise RuntimeError(f"hpatch 输出 SHA256 校验失败：{relative_path}")

                ensure_parent(target)
                shutil.copy2(source, target)
                processed_paths.append(relative_path)
            elif action == "remove":
                remove_path(target)
                processed_paths.append(relative_path)

    return processed_paths


def apply_post_process(app_root: Path, relative_paths: list[str]) -> None:
    """执行补丁后处理：可执行权限修复与 quarantine 清理。"""

    for relative_path in sorted(set(relative_paths)):
        target = app_root / relative_path
        if should_make_executable(relative_path):
            set_executable(target)
        if should_clear_quarantine(relative_path):
            remove_quarantine(target)


def main() -> int:
    """脚本入口：校验版本、应用 hpatch、后处理并在失败时回滚。"""

    args = parse_args()

    app_root = Path(args.app_root).expanduser().resolve()
    if not app_root.exists() or not app_root.is_dir():
        print(f"错误：程序根目录不存在或不是文件夹：{app_root}", file=sys.stderr)
        return 1

    version_file = app_root / args.version_file
    if not version_file.exists():
        print(f"错误：未找到版本文件：{version_file}", file=sys.stderr)
        return 1

    if args.package:
        package_roots = [resolve_package_root(app_root, args.package)]
    else:
        package_roots = discover_package_roots(app_root, args.manifest)

    if not package_roots:
        print("错误：未找到可用更新包目录（需要同时包含 manifest.json 和 patch.hdiff）。", file=sys.stderr)
        return 1

    hpatch_bin = resolve_hpatch_bin(app_root, args.hpatch_bin)
    pending: list[tuple[Path, dict]] = []
    for package_root in package_roots:
        manifest_file = package_root / args.manifest
        if not manifest_file.exists():
            print(f"[跳过] 缺少更新清单：{manifest_file}")
            continue
        try:
            manifest = load_json(manifest_file)
        except Exception as exc:
            print(f"[跳过] 清单解析失败：{manifest_file}，错误：{exc}")
            continue
        pending.append((package_root, manifest))

    if not pending:
        print("错误：未找到可解析的更新清单。", file=sys.stderr)
        return 1

    applied_count = 0
    while True:
        try:
            app_version = read_current_version(version_file)
        except Exception as exc:
            print(f"错误：读取当前版本失败：{exc}", file=sys.stderr)
            return 1

        matched: list[tuple[Path, dict]] = []
        for package_root, manifest in pending:
            base_version = str(manifest.get("base_version", "")).strip()
            if base_version == app_version:
                matched.append((package_root, manifest))

        if not matched:
            break

        if len(matched) > 1:
            matched_paths = "\n".join(str(item[0]) for item in matched)
            print("错误：发现多个可应用更新包，请只保留一个：", file=sys.stderr)
            print(matched_paths, file=sys.stderr)
            return 1

        package_root, manifest = matched[0]
        target_version = str(manifest.get("target_version", "")).strip()
        if target_version and app_version == target_version:
            print(f"[信息] 当前已是目标版本 {target_version}，跳过：{package_root}")
            pending.remove((package_root, manifest))
            continue

        try:
            base_version, target_version = verify_manifest_and_version(manifest, app_version)
            print(f"[信息] 版本校验通过：{base_version} -> {target_version}")
        except Exception as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1

        backup_root = app_root / args.backup_dir / f"{target_version}"
        backup_root.mkdir(parents=True, exist_ok=True)
        touch_records: list[TouchRecord] = []

        try:
            processed_paths = apply_hdiff(
                app_root=app_root,
                package_root=package_root,
                manifest=manifest,
                backup_root=backup_root,
                touch_records=touch_records,
                hpatch_bin=hpatch_bin,
            )
            apply_post_process(app_root, processed_paths)

            version_data = load_json(version_file)
            version_data["version"] = target_version
            write_json(version_file, version_data)

            try:
                remove_applied_package(package_root)
                print(f"[清理] 已删除更新包：{package_root}")
            except Exception as cleanup_error:
                print(f"[警告] 更新包删除失败，请手动删除：{package_root}，错误：{cleanup_error}")

            print(f"[完成] 更新成功：{base_version} -> {target_version}")
            print(f"[输出] 备份目录：{backup_root}")
            pending.remove((package_root, manifest))
            applied_count += 1
        except Exception as exc:
            print(f"[错误] 更新失败，开始回滚：{exc}", file=sys.stderr)
            restore_backup(app_root, backup_root, touch_records)
            print(f"[回滚] 已恢复到更新前状态。备份目录：{backup_root}", file=sys.stderr)
            return 1

    if applied_count == 0:
        print("[信息] 未找到可应用到当前版本的更新包。")
    else:
        print(f"[完成] 已应用 {applied_count} 个更新包。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
