"""世界书同步独立进程入口，stdout 仅输出 NDJSON 协议。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from filelock import FileLock, Timeout

from .adapters import create_default_registry
from .package_loader import WorldbookPackageLoader
from .paths import WorldbookPaths
from .qdrant_index import QdrantWorldbookIndex
from .sync import WorldbookSyncCoordinator
from .user_state import WorldbookUserStateRepository


def emit_event(event: str, **fields: object) -> None:
    """向 stdout 写入一行不可混入普通日志的协议事件。"""

    payload = {"event": event, **fields}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _acquire_lock(lock: FileLock, timeout_seconds: int) -> bool:
    """轮询获取文件锁，并定期输出等待进度。"""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            lock.acquire(timeout=0)
            return True
        except Timeout:
            emit_event("waiting_for_lock", remaining_seconds=max(0, int(deadline - time.monotonic())))
            time.sleep(1)
    return False


def run_worker(app_root: Path, command: str, lock_timeout: int = 600) -> int:
    """运行一次同步命令并返回稳定 exit code。"""

    paths = WorldbookPaths(app_root.resolve())
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(paths.lock))
    emit_event("started", command=command)
    if not _acquire_lock(lock, lock_timeout):
        emit_event("failed", reason="lock_timeout")
        return 4
    index: QdrantWorldbookIndex | None = None
    try:
        emit_event("progress", stage="loading_packages")
        index = QdrantWorldbookIndex(paths.index, paths.embedding_model)
        fingerprint = index.index_fingerprint()
        coordinator = WorldbookSyncCoordinator(
            WorldbookPackageLoader(paths.official_packages),
            WorldbookUserStateRepository(paths.user_state),
            create_default_registry(),
            index,
            fingerprint,
        )
        report = coordinator.reconcile_all(force_rebuild=command == "rebuild")
        event = "completed" if report.success else "failed"
        emit_event(event, report=report.model_dump(mode="json"))
        return 0 if report.success else 2
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        print(f"世界书同步失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        emit_event("failed", reason="sync_error", message=str(exc))
        return 3
    finally:
        if index is not None:
            index.close()
        lock.release()


def main(argv: list[str] | None = None) -> int:
    """解析 worker 命令行并运行同步。"""

    parser = argparse.ArgumentParser(description="世界书 Qdrant 同步 worker")
    parser.add_argument("command", choices=["reconcile-all", "sync-package", "rebuild"])
    parser.add_argument("package_id", nargs="?")
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--lock-timeout", type=int, default=600)
    args = parser.parse_args(argv)
    return run_worker(args.app_root, args.command, args.lock_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
