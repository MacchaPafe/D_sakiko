from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout

from update.update_paths import get_update_root


class OperationLockBusy(RuntimeError):
    """表示已有更新或修复事务正在修改程序文件。"""


def get_operation_lock_file(app_root: Path) -> Path:
    """返回更新和修复共用的锁文件路径。"""

    return get_update_root(app_root) / "operation.lock"


def get_operation_owner_file(app_root: Path) -> Path:
    """返回仅用于诊断的锁占用者记录。"""

    return get_update_root(app_root) / "operation_owner.json"


def _write_owner(owner_file: Path, operation: str) -> None:
    """原子写入不参与互斥判断的占用者信息。"""

    owner_file.parent.mkdir(parents=True, exist_ok=True)
    content = {
        "operation": operation,
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = owner_file.with_name(f".{owner_file.name}.tmp")
    temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(owner_file)


@contextmanager
def acquire_operation_lock(app_root: Path, operation: str) -> Iterator[None]:
    """非阻塞获取程序文件操作锁，并在结束时释放。"""

    lock_file = get_operation_lock_file(app_root)
    owner_file = get_operation_owner_file(app_root)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(lock_file, timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise OperationLockBusy("当前已有更新或修复任务正在进行，请稍后再试") from exc
    try:
        _write_owner(owner_file, operation)
        yield
    finally:
        try:
            owner_file.unlink(missing_ok=True)
        finally:
            lock.release()
