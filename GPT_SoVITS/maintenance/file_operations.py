from __future__ import annotations

import os
import shutil
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from repair.repair_manifest import sha256_file
from maintenance.windows_file_users import describe_file_users


MAX_FILE_ATTEMPTS = 3
FILE_RETRY_INTERVAL = 5.0


@dataclass
class FileOperation:
    """描述独立文件操作及其持久化进度回调。"""

    path: Path
    action: str
    run: Callable[[], None]
    succeeded: Optional[Callable[[], None]] = None
    failed: Optional[Callable[[Exception], None]] = None
    attempts: int = 0
    retry_at: float = 0.0


def retryable_file_error(exc: Exception) -> bool:
    """仅对 Windows 拒绝访问、共享冲突和锁冲突有限重试。"""
    return isinstance(exc, OSError) and getattr(exc, 'winerror', None) in {5, 32, 33}


def atomic_copy_file(source: Path, target: Path, expected_sha: Optional[str]) -> None:
    """先校验同目录临时文件再替换，失败时不直接覆盖目标。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'.{target.name}.{uuid4().hex}.update.tmp')
    try:
        shutil.copy2(source, temporary)
        if expected_sha and sha256_file(temporary) != expected_sha:
            raise RuntimeError(f'临时文件 SHA256 校验失败：{target}')
        try:
            os.replace(temporary, target)
        except OSError:
            # 若替换实际已经完成，以磁盘内容确认；读取失败不能算成功。
            try:
                already_replaced = bool(expected_sha and target.is_file()
                                        and sha256_file(target) == expected_sha)
            except OSError:
                already_replaced = False
            if not already_replaced:
                raise
        if expected_sha and sha256_file(target) != expected_sha:
            raise RuntimeError(f'替换后 SHA256 校验失败：{target}')
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as exc:
            print(f'[清理] 临时文件暂未删除：{temporary}，错误：{exc}')


def run_file_operations(
    operations: list[FileOperation], *, continue_on_error: bool = False,
    log: Callable[[str], None] = print,
) -> None:
    """先处理其他独立文件，失败项包含首次尝试最多三次，间隔至少五秒。"""
    pending: list[FileOperation] = []

    def attempt(operation: FileOperation) -> bool:
        operation.attempts += 1
        try:
            operation.run()
        except Exception as exc:
            # 从失败时刻计算间隔，不让诊断时间减少两次尝试间的等待。
            operation.retry_at = time.monotonic() + FILE_RETRY_INTERVAL
            retry = retryable_file_error(exc) and operation.attempts < MAX_FILE_ATTEMPTS
            log(f'[文件错误] {operation.action}: {operation.path}，'
                f'尝试 {operation.attempts}/{MAX_FILE_ATTEMPTS}，'
                f'winerror={getattr(exc, "winerror", None)}，'
                f'{"稍后重试" if retry else "停止重试"}\n'
                + ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            if retryable_file_error(exc) and operation.attempts in {1, MAX_FILE_ATTEMPTS}:
                # 诊断及其格式化都不能改变重试或回滚结果。
                try:
                    log(describe_file_users(operation.path))
                except Exception:
                    pass
            if operation.failed is not None:
                operation.failed(exc)
            if retry:
                return False
            if not continue_on_error:
                raise
            return True
        if operation.succeeded is not None:
            operation.succeeded()
        return True

    def finish_pending() -> None:
        while pending:
            for operation in list(pending):
                delay = operation.retry_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                if attempt(operation):
                    pending.remove(operation)

    for operation in operations:
        # 有父子路径关系时保留原始操作顺序，不能越过尚未完成的操作。
        if any(operation.path == previous.path or operation.path in previous.path.parents
               or previous.path in operation.path.parents for previous in pending):
            finish_pending()
        if not attempt(operation):
            pending.append(operation)
    finish_pending()
