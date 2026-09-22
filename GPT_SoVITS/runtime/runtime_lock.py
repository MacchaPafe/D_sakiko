from __future__ import annotations

import atexit
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout

from .conversation_storage import conversation_lock


class RuntimeLockBusy(RuntimeError):
    """另一个 D_sakiko 运行模式已经占用共享资源。"""


class RuntimeLease:
    """持有某一类运行模式的跨进程租约。"""

    def __init__(self, lock: FileLock, owner_file: Path) -> None:
        """记录锁及诊断用的所有者文件。"""
        self.lock = lock
        self.owner_file = owner_file
        self.released = False

    def release(self) -> None:
        """幂等释放运行租约。"""
        if self.released:
            return
        try:
            self.owner_file.unlink(missing_ok=True)
        finally:
            self.lock.release()
            self.released = True


def other_mode_running(reference_dir: Path, theater: bool) -> bool:
    """在存档锁内探测另一类模式的实际锁占用状态。"""
    (reference_dir / ".runtime").mkdir(parents=True, exist_ok=True)
    name = "runtime.lock" if theater else "theater.lock"
    lock = FileLock(reference_dir / ".runtime" / name, timeout=0)
    try:
        lock.acquire()
    except Timeout:
        return True
    else:
        lock.release()
        return False


def acquire_runtime_lock(project_root: Path | str, mode: str) -> RuntimeLease:
    """登记单人或小剧场实例；两类允许并行，同类保持互斥。"""
    if mode not in {"desktop", "web", "theater"}:
        raise ValueError(f"未知运行模式：{mode}")
    reference_dir = Path(project_root) / "reference_audio"
    state_dir = reference_dir / ".runtime"
    state_dir.mkdir(parents=True, exist_ok=True)
    theater = mode == "theater"
    lock = FileLock(state_dir / ("theater.lock" if theater else "runtime.lock"), timeout=0)
    try:
        with conversation_lock(reference_dir / "all_conversation.json"):
            lock.acquire()
            try:
                owner_file = state_dir / ("theater-owner.json" if theater else "owner.json")
                owner_file.write_text(json.dumps({
                    "pid": os.getpid(), "mode": mode,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            except BaseException:
                lock.release()
                raise
    except Timeout as exc:
        raise RuntimeLockBusy("启动失败：同类模式已运行或存档正忙。Web 与桌面端不能同时启动，小剧场也只能启动一个实例。请稍后重试。") from exc
    lease = RuntimeLease(lock, owner_file)
    atexit.register(lease.release)
    return lease
