#!/usr/bin/env python3
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TextIO


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "GPT_SoVITS"))
sys.path.insert(0, str(ROOT))

from repair.repair_manifest import (
    APP_ID,
    MODE_PATTERN,
    SHA256_PATTERN,
    normalize_manifest_path,
    resolve_under_root,
    sha256_file,
)
from repair.repair_paths import (
    get_repair_backup_root,
    get_repair_log_dir,
    get_repair_plan_dir,
    get_repair_result_file,
    get_repair_staging_root,
)
from tools.apply_update_patch import parse_restart_command, restart_app, setup_logging, wait_for_process_exit
from update.operation_lock import OperationLockBusy, acquire_operation_lock
from update.update_checker import detect_arch, detect_platform, read_current_version
from update.update_paths import get_version_file


PLAN_FIELDS = {"schema", "app_id", "version", "platform", "arch", "manifest_sha256", "staging_dir", "files"}
PLAN_FILE_FIELDS = {
    "path",
    "original_state",
    "original_sha256",
    "sha256",
    "size",
    "mode",
    "staged_file",
}


@dataclass(frozen=True)
class RepairPlanEntry:
    """描述独立修复器要处理的单个文件。"""

    path: str
    original_state: Literal["missing", "modified"]
    original_sha256: str | None
    sha256: str
    size: int
    mode: str
    staged_file: str


@dataclass(frozen=True)
class RepairPlan:
    """描述已经下载完成的一批修复事务。"""

    schema: int
    app_id: str
    version: str
    platform: str
    arch: str
    manifest_sha256: str
    staging_dir: str
    files: tuple[RepairPlanEntry, ...]


@dataclass(frozen=True)
class TouchRecord:
    """记录修复事务已经触达的目标及其原始状态。"""

    path: str
    existed_before: bool


class RepairExecutionFailure(RuntimeError):
    """保存修复失败后的回滚执行状态。"""

    def __init__(
        self,
        message: str,
        *,
        rollback_performed: bool,
        rollback_succeeded: bool | None,
        repaired_count: int,
    ) -> None:
        """保存错误文本和结果记录需要的事务状态。"""

        super().__init__(message)
        self.rollback_performed = rollback_performed
        self.rollback_succeeded = rollback_succeeded
        self.repaired_count = repaired_count


class RepairResultRecorder:
    """原子写入主程序下次启动时读取的修复结果。"""

    def __init__(self, app_root: Path, status_file: Path, log_file: Path) -> None:
        """保存结果文件上下文和修复开始时间。"""

        self.app_root = app_root.resolve()
        self.status_file = status_file
        self.log_file = log_file
        self.version = ""
        self.platform_name = ""
        self.arch = ""
        self.backup_dir: Path | None = None
        self.started_at = datetime.now(timezone.utc).isoformat()

    def set_identity(self, plan: RepairPlan) -> None:
        """从已校验计划记录版本与平台。"""

        self.version = plan.version
        self.platform_name = plan.platform
        self.arch = plan.arch

    def set_backup_dir(self, backup_dir: Path) -> None:
        """记录本次事务使用的备份目录。"""

        self.backup_dir = backup_dir

    def _relative(self, path: Path | None) -> str:
        """尽量把结果中的本地路径保存为 app_root 相对路径。"""

        if path is None:
            return ""
        try:
            return path.resolve(strict=False).relative_to(self.app_root).as_posix()
        except ValueError:
            return str(path.resolve(strict=False))

    def write(
        self,
        status: Literal["running", "success", "failed"],
        *,
        rollback_performed: bool = False,
        rollback_succeeded: bool | None = None,
        repaired_count: int = 0,
        failed_count: int = 0,
    ) -> None:
        """原子写入一次修复结果快照。"""

        content: dict[str, object] = {
            "schema_version": 1,
            "status": status,
            "version": self.version,
            "platform": self.platform_name,
            "arch": self.arch,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat() if status != "running" else "",
            "rollback_performed": rollback_performed,
            "rollback_succeeded": rollback_succeeded,
            "repaired_count": repaired_count,
            "failed_count": failed_count,
            "backup_dir": self._relative(self.backup_dir),
            "log_file": self._relative(self.log_file),
            "notified": status != "failed",
        }
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.status_file.with_name(f".{self.status_file.name}.tmp")
        temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.status_file)


def parse_args() -> argparse.Namespace:
    """解析独立修复执行器参数。"""

    parser = argparse.ArgumentParser(description="应用已经完整下载并校验的 D_sakiko repair plan。")
    parser.add_argument("--app-root", required=True, help="程序根目录。")
    parser.add_argument("--plan", required=True, help="客户端生成的 repair plan。")
    parser.add_argument("--wait-pid", type=int, default=0, help="开始事务前等待退出的主程序 PID。")
    parser.add_argument("--restart-command", default="", help="成功后的 JSON 重启命令数组。")
    parser.add_argument("--log-file", default="", help="修复日志路径。")
    parser.add_argument("--status-file", default="", help="修复结果记录路径。")
    return parser.parse_args()


def _exact_fields(data: dict[str, object], expected: set[str], location: str) -> None:
    """要求计划对象字段与本地 schema 完全一致。"""

    missing = expected - data.keys()
    unknown = data.keys() - expected
    if missing or unknown:
        raise RuntimeError(
            f"{location} 字段不匹配，缺少={sorted(missing)}，未知={sorted(unknown)}"
        )


def _required_string(data: dict[str, object], key: str, location: str) -> str:
    """读取计划中的非空字符串字段。"""

    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{location}.{key} 必须是非空字符串")
    return value.strip()


def load_repair_plan(plan_file: Path) -> RepairPlan:
    """严格读取客户端生成的 repair plan。"""

    raw: object = json.loads(plan_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("repair plan 顶层必须是对象")
    data = {str(key): value for key, value in raw.items()}
    _exact_fields(data, PLAN_FIELDS, "plan")
    schema = data["schema"]
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != 1:
        raise RuntimeError(f"不支持的 repair plan schema：{schema!r}")
    manifest_sha = _required_string(data, "manifest_sha256", "plan").lower()
    if SHA256_PATTERN.fullmatch(manifest_sha) is None:
        raise RuntimeError("plan.manifest_sha256 格式无效")
    staging_dir = normalize_manifest_path(_required_string(data, "staging_dir", "plan"), "plan.staging_dir")
    raw_files = data["files"]
    if not isinstance(raw_files, list) or not raw_files:
        raise RuntimeError("plan.files 必须是非空数组")
    entries: list[RepairPlanEntry] = []
    seen: set[str] = set()
    for index, raw_entry in enumerate(raw_files):
        if not isinstance(raw_entry, dict):
            raise RuntimeError(f"plan.files[{index}] 必须是对象")
        entry = {str(key): value for key, value in raw_entry.items()}
        location = f"plan.files[{index}]"
        _exact_fields(entry, PLAN_FILE_FIELDS, location)
        path = normalize_manifest_path(_required_string(entry, "path", location), f"{location}.path")
        if path in seen:
            raise RuntimeError(f"repair plan 路径重复：{path}")
        original_state_value = _required_string(entry, "original_state", location)
        if original_state_value not in {"missing", "modified"}:
            raise RuntimeError(f"{location}.original_state 无效")
        original_state: Literal["missing", "modified"] = (
            "missing" if original_state_value == "missing" else "modified"
        )
        original_sha_value = entry["original_sha256"]
        if original_state == "missing":
            if original_sha_value is not None:
                raise RuntimeError(f"{location}.original_sha256 在 missing 状态下必须为 null")
            original_sha: str | None = None
        else:
            if not isinstance(original_sha_value, str) or SHA256_PATTERN.fullmatch(original_sha_value.lower()) is None:
                raise RuntimeError(f"{location}.original_sha256 格式无效")
            original_sha = original_sha_value.lower()
        target_sha = _required_string(entry, "sha256", location).lower()
        if SHA256_PATTERN.fullmatch(target_sha) is None:
            raise RuntimeError(f"{location}.sha256 格式无效")
        size = entry["size"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise RuntimeError(f"{location}.size 必须是非负整数")
        mode = _required_string(entry, "mode", location)
        if MODE_PATTERN.fullmatch(mode) is None:
            raise RuntimeError(f"{location}.mode 格式无效")
        staged_file = normalize_manifest_path(
            _required_string(entry, "staged_file", location), f"{location}.staged_file"
        )
        entries.append(
            RepairPlanEntry(
                path=path,
                original_state=original_state,
                original_sha256=original_sha,
                sha256=target_sha,
                size=size,
                mode=mode,
                staged_file=staged_file,
            )
        )
        seen.add(path)
    return RepairPlan(
        schema=schema,
        app_id=_required_string(data, "app_id", "plan"),
        version=_required_string(data, "version", "plan"),
        platform=_required_string(data, "platform", "plan"),
        arch=_required_string(data, "arch", "plan"),
        manifest_sha256=manifest_sha,
        staging_dir=staging_dir,
        files=tuple(entries),
    )


def _require_within(path: Path, root: Path, field_name: str) -> None:
    """要求 path 解析后位于 root 内。"""

    resolved = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise RuntimeError(f"{field_name} 超出允许目录：{path}")


def validate_repair_plan(app_root: Path, plan: RepairPlan) -> Path:
    """在独立进程内重新校验计划身份与全部 staging 文件。"""

    if plan.app_id != APP_ID:
        raise RuntimeError(f"plan.app_id 不匹配：{plan.app_id}")
    current_version = read_current_version(get_version_file(app_root))
    if plan.version != current_version:
        raise RuntimeError(f"plan 版本不匹配：当前 {current_version}，计划 {plan.version}")
    current_platform = detect_platform()
    current_arch = detect_arch()
    if plan.platform != current_platform or plan.arch not in {current_arch, "universal"}:
        raise RuntimeError(
            f"plan 平台架构不匹配：当前 {current_platform}-{current_arch}，计划 {plan.platform}-{plan.arch}"
        )
    staging_dir = resolve_under_root(app_root, plan.staging_dir, "plan.staging_dir")
    _require_within(staging_dir, get_repair_staging_root(app_root), "plan.staging_dir")
    for entry in plan.files:
        staged_file = resolve_under_root(app_root, entry.staged_file, "plan.files[].staged_file")
        _require_within(staged_file, staging_dir, "plan.files[].staged_file")
        if not staged_file.is_file():
            raise RuntimeError(f"staging 文件不存在：{entry.path}")
        if staged_file.stat().st_size != entry.size or sha256_file(staged_file) != entry.sha256:
            raise RuntimeError(f"staging 文件校验失败：{entry.path}")
    return staging_dir


def precheck_targets(app_root: Path, plan: RepairPlan) -> tuple[RepairPlanEntry, ...]:
    """整批检查目标现场，并返回仍需替换的条目。"""

    pending: list[RepairPlanEntry] = []
    conflicts: list[str] = []
    for entry in plan.files:
        target = resolve_under_root(app_root, entry.path)
        if target.is_file():
            actual_sha = sha256_file(target)
            if actual_sha == entry.sha256:
                continue
            if entry.original_state == "modified" and actual_sha == entry.original_sha256:
                pending.append(entry)
                continue
            conflicts.append(f"{entry.path}：检查后内容发生变化")
            continue
        if entry.original_state == "missing" and not target.exists():
            pending.append(entry)
        else:
            conflicts.append(f"{entry.path}：检查后存在状态发生变化")
    if conflicts:
        raise RuntimeError("修复前现场预检失败，未修改任何文件：\n" + "\n".join(conflicts))
    return tuple(pending)


def _backup_target(app_root: Path, backup_root: Path, entry: RepairPlanEntry) -> bool:
    """在替换前备份存在的目标文件。"""

    target = resolve_under_root(app_root, entry.path)
    if not target.exists():
        return False
    if not target.is_file():
        raise RuntimeError(f"目标不是普通文件：{entry.path}")
    backup = backup_root / "files" / entry.path
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)
    return True


def rollback_repair(app_root: Path, backup_root: Path, records: list[TouchRecord]) -> None:
    """按触达顺序逆序恢复修复前状态。"""

    for record in reversed(records):
        target = resolve_under_root(app_root, record.path)
        if record.existed_before:
            backup = backup_root / "files" / record.path
            if not backup.is_file():
                raise RuntimeError(f"回滚备份不存在：{record.path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.repair_rollback.tmp")
            shutil.copy2(backup, temporary)
            os.replace(temporary, target)
        else:
            if target.is_file() or target.is_symlink():
                target.unlink(missing_ok=True)


def apply_repair_plan(app_root: Path, plan: RepairPlan, backup_root: Path) -> int:
    """执行整批修复，并在任一步骤失败后自动回滚。"""

    validate_repair_plan(app_root, plan)
    pending = precheck_targets(app_root, plan)
    records: list[TouchRecord] = []
    repaired_count = 0
    try:
        for entry in pending:
            target = resolve_under_root(app_root, entry.path)
            staged_file = resolve_under_root(app_root, entry.staged_file, "plan.files[].staged_file")
            existed_before = _backup_target(app_root, backup_root, entry)
            records.append(TouchRecord(path=entry.path, existed_before=existed_before))
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{backup_root.name}.tmp")
            try:
                shutil.copy2(staged_file, temporary)
                if sys.platform != "win32":
                    temporary.chmod(int(entry.mode, 8))
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            if sha256_file(target) != entry.sha256:
                raise RuntimeError(f"替换后 SHA256 校验失败：{entry.path}")
            repaired_count += 1
            print(f"[修复] {entry.path}")
        return repaired_count
    except Exception as exc:
        rollback_performed = bool(records)
        rollback_succeeded: bool | None = None
        if rollback_performed:
            try:
                rollback_repair(app_root, backup_root, records)
                rollback_succeeded = True
                print("[回滚] 已恢复本次触达的全部文件", file=sys.stderr)
            except Exception as rollback_error:
                rollback_succeeded = False
                print(f"[回滚] 自动回滚失败：{rollback_error}", file=sys.stderr)
        raise RepairExecutionFailure(
            str(exc),
            rollback_performed=rollback_performed,
            rollback_succeeded=rollback_succeeded,
            repaired_count=repaired_count,
        ) from exc


def _resolve_plan_file(app_root: Path, value: str) -> Path:
    """解析并限制 plan 必须位于本程序的 repair plan 目录。"""

    path = Path(value).expanduser()
    resolved = path.resolve() if path.is_absolute() else (app_root / path).resolve()
    _require_within(resolved, get_repair_plan_dir(app_root), "--plan")
    return resolved


def main() -> int:
    """独立修复器入口。"""

    args = parse_args()
    app_root = Path(args.app_root).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = (
        Path(args.log_file).expanduser().resolve()
        if args.log_file
        else get_repair_log_dir(app_root) / f"repair_{timestamp}.log"
    )
    status_file = (
        Path(args.status_file).expanduser().resolve()
        if args.status_file
        else get_repair_result_file(app_root)
    )
    recorder = RepairResultRecorder(app_root, status_file, log_file)
    log_handle: TextIO | None = None
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    try:
        log_handle = setup_logging(log_file)
        recorder.write("running")
        if args.wait_pid:
            print(f"[等待] 等待主程序退出：PID={args.wait_pid}")
            wait_for_process_exit(args.wait_pid)
        plan_file = _resolve_plan_file(app_root, args.plan)
        try:
            with acquire_operation_lock(app_root, "repair"):
                plan = load_repair_plan(plan_file)
                recorder.set_identity(plan)
                backup_root = get_repair_backup_root(app_root) / plan_file.stem
                backup_root.mkdir(parents=True, exist_ok=False)
                recorder.set_backup_dir(backup_root)
                recorder.write("running")
                repaired_count = apply_repair_plan(app_root, plan, backup_root)
                recorder.write("success", repaired_count=repaired_count)
                restart_app(parse_restart_command(args.restart_command))
                print(f"[完成] 已修复 {repaired_count} 个文件，备份目录：{backup_root}")
                return 0
        except OperationLockBusy as exc:
            raise RuntimeError(str(exc)) from exc
    except RepairExecutionFailure as exc:
        print(f"[错误] 修复失败：{exc}", file=sys.stderr)
        recorder.write(
            "failed",
            rollback_performed=exc.rollback_performed,
            rollback_succeeded=exc.rollback_succeeded,
            repaired_count=exc.repaired_count,
            failed_count=1,
        )
        return 1
    except Exception as exc:
        print(f"[错误] 修复失败：{exc}", file=sys.stderr)
        recorder.write("failed", failed_count=1)
        return 1
    finally:
        if log_handle is not None:
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            log_handle.flush()
            log_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
