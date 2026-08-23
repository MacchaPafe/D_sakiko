from __future__ import annotations

import atexit
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout


class RuntimeLockBusy(RuntimeError):
    """另一个 D_sakiko 运行模式已经占用共享资源。"""


class RuntimeLease:
    def __init__(self, lock: FileLock, owner_file: Path) -> None:
        self.lock = lock
        self.owner_file = owner_file
        self.released = False

    def release(self) -> None:
        if self.released:
            return
        self.owner_file.unlink(missing_ok=True)
        self.lock.release()
        self.released = True


def acquire_runtime_lock(project_root: Path | str, mode: str) -> RuntimeLease:
    """非阻塞占用桌面/WebUI 共用的聊天与语音运行时。"""
    project_root = Path(project_root)
    state_dir = project_root / "reference_audio" / ".runtime"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(state_dir / "runtime.lock", timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise RuntimeLockBusy("D_sakiko 已在另一个窗口或运行模式中启动。") from exc

    owner_file = state_dir / "owner.json"
    owner_file.write_text(json.dumps({
        "pid": os.getpid(),
        "mode": mode,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    lease = RuntimeLease(lock, owner_file)
    atexit.register(lease.release)
    return lease
