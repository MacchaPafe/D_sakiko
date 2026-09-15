from __future__ import annotations

import json
import shutil
import traceback
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from repair.repair_manifest import resolve_under_root, sha256_file, version_key


@dataclass
class FileRecord:
    """保存写入前的文件身份和回滚进度。"""

    path: str
    original_sha: str | None
    target_sha: str | None
    restored: bool = False
    error: str = ""


@dataclass
class Transaction:
    """在文件修改前持久化备份与恢复进度。"""

    root: Path
    directory: Path
    operation: str
    base_version: str
    target_version: str
    status: str = "running"
    startup_attempted: bool = False
    records: list[FileRecord] = field(default_factory=list)

    @classmethod
    def create(cls, root: Path, operation: str, base_version: str, target_version: str) -> Transaction:
        """为每次操作创建不可复用的备份目录。"""
        directory = root / '.updates' / 'transactions' / (
            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-' + uuid4().hex[:8]
        )
        directory.mkdir(parents=True, exist_ok=False)
        transaction = cls(root.resolve(), directory, operation, base_version, target_version)
        transaction.save()
        return transaction

    def save(self) -> None:
        """原子保存事务状态，写入失败时禁止继续修改文件。"""
        data = {
            'schema': 1, 'operation': self.operation, 'base_version': self.base_version,
            'target_version': self.target_version, 'status': self.status,
            'startup_attempted': self.startup_attempted,
            'records': [asdict(record) for record in self.records],
        }
        temporary = self.directory / 'transaction.json.tmp'
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(self.directory / 'transaction.json')

    def prepare(self, relative: str, target_sha: str | None) -> None:
        """先完成并验证备份，再记录即将触达的路径。"""
        if any(record.path == relative for record in self.records):
            return
        target = resolve_under_root(self.root, relative)
        original_sha = None
        if target.exists():
            if not target.is_file():
                raise RuntimeError(f'事务目标不是普通文件：{relative}')
            original_sha = sha256_file(target)
            backup = resolve_under_root(self.directory / 'files', relative)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            if sha256_file(backup) != original_sha:
                raise RuntimeError(f'事务备份校验失败：{relative}')
        self.records.append(FileRecord(relative, original_sha, target_sha))
        self.save()

    def log_exception(self, context: str) -> None:
        """保存完整异常链，避免脱离终端后丢失诊断信息。"""
        detail = context + '\n' + traceback.format_exc()
        print(detail)
        try:
            with (self.directory / 'recovery.log').open('a', encoding='utf-8') as stream:
                stream.write(detail + '\n')
        except OSError:
            traceback.print_exc(file=sys.stderr)

    def rollback(self) -> bool:
        """逐文件恢复，保留额外修改，单项失败不阻断其他文件。"""
        self.status = 'recovering'
        self.save()
        for record in reversed(self.records):
            try:
                target = resolve_under_root(self.root, record.path)
                current_sha = sha256_file(target) if target.is_file() else None
                if not target.exists() and record.original_sha is None:
                    record.restored = True
                elif current_sha is not None and current_sha == record.original_sha:
                    record.restored = True
                else:
                    if target.exists() and not target.is_file():
                        raise RuntimeError(f'恢复目标不是普通文件：{record.path}')
                    if current_sha is not None and current_sha != record.target_sha:
                        conflict = resolve_under_root(self.directory / 'conflicts' / uuid4().hex, record.path)
                        conflict.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, conflict)
                    if record.original_sha is None:
                        target.unlink(missing_ok=True)
                    else:
                        backup = resolve_under_root(self.directory / 'files', record.path)
                        if not backup.is_file() or sha256_file(backup) != record.original_sha:
                            raise RuntimeError(f'恢复备份不存在或哈希不符：{record.path}')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        temporary = target.with_name(f'.{target.name}.{uuid4().hex}.restore.tmp')
                        try:
                            shutil.copy2(backup, temporary)
                            temporary.replace(target)
                        finally:
                            temporary.unlink(missing_ok=True)
                        if sha256_file(target) != record.original_sha:
                            raise RuntimeError(f'恢复后校验失败：{record.path}')
                    record.restored = True
                record.error = ''
                print(f'[回滚] 已恢复：{record.path}')
            except Exception as exc:
                record.restored = False
                record.error = str(exc)
                self.log_exception(f'[回滚] 恢复失败：{record.path}')
            self.save()
        self.status = 'rolled_back' if all(record.restored for record in self.records) else 'recovery_failed'
        self.save()
        return self.status == 'rolled_back'

    def complete(self) -> None:
        """提交成功状态，之后才能清理已完成的旧事务。"""
        self.status = 'committed'
        self.save()
        try:
            prune_completed(self.root)
        except OSError:
            self.log_exception('[清理] 旧备份清理失败，事务已经完成')


def load_transactions(root: Path) -> list[Transaction]:
    """读取独立事务并严格限制恢复路径，损坏记录保留供诊断。"""
    result: list[Transaction] = []
    for path in sorted((root / '.updates' / 'transactions').glob('*/transaction.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data['schema'] != 1 or data['operation'] not in {'update', 'repair'}:
                raise ValueError('不支持的事务记录')
            version_key(data['base_version'])
            version_key(data['target_version'])
            if data['status'] not in {'running', 'recovering', 'recovery_failed', 'rolled_back', 'committed'}:
                raise ValueError('无效事务状态')
            if not isinstance(data['startup_attempted'], bool):
                raise ValueError('无效启动重试状态')
            records = [FileRecord(**entry) for entry in data['records']]
            for record in records:
                resolve_under_root(root, record.path)
                for value in (record.original_sha, record.target_sha):
                    if value is not None and (not isinstance(value, str) or len(value) != 64
                                              or any(char not in '0123456789abcdef' for char in value)):
                        raise ValueError('无效文件哈希')
            result.append(Transaction(root.resolve(), path.parent, data['operation'], data['base_version'],
                                      data['target_version'], data['status'], data['startup_attempted'], records))
        except Exception:
            print(f'[恢复] 无法读取事务：{path}\n{traceback.format_exc()}')
            result.append(Transaction(root.resolve(), path.parent, 'update', '', '', 'invalid', True))
    return result


def pending_transactions(root: Path) -> list[Transaction]:
    """返回尚未确认恢复或提交的事务。"""
    return [item for item in load_transactions(root) if item.status not in {'committed', 'rolled_back'}]


def recommended_version(root: Path) -> str | None:
    """优先采用未完成更新的基础版本，否则读取当前版本。"""
    pending = pending_transactions(root)
    pending = [item for item in pending if item.status != 'invalid']
    updates = [item for item in pending if item.operation == 'update']
    if updates:
        return updates[-1].base_version
    if pending:
        return pending[-1].base_version
    from maintenance.identity import read_current_version
    try:
        version = read_current_version(root / 'version.json')
        version_key(version)
        return version
    except Exception:
        return None


def reconcile_recovery(root: Path) -> None:
    """修复后仅依据实际文件内容关闭已完整恢复的事务。"""
    for transaction in pending_transactions(root):
        if transaction.status == 'invalid':
            continue
        for record in transaction.records:
            target = resolve_under_root(root, record.path)
            record.restored = (sha256_file(target) == record.original_sha if target.is_file()
                               else not target.exists() and record.original_sha is None)
        if all(record.restored for record in transaction.records):
            transaction.status = 'rolled_back'
        transaction.save()
    prune_completed(root)


def prune_completed(root: Path) -> None:
    """保留最近一次完成事务和所有未完成事务，不清理冲突备份。"""
    completed = [item for item in load_transactions(root) if item.status in {'committed', 'rolled_back'}]
    for transaction in completed[:-1]:
        if not (transaction.directory / 'conflicts').exists():
            shutil.rmtree(transaction.directory)


def recover_pending(root: Path, *, automatic: bool = True) -> bool:
    """持有操作锁重试本地恢复，每个事务最多自动尝试一次。"""
    from update.operation_lock import acquire_operation_lock
    from filelock import FileLock
    runtime_lock = root / 'reference_audio' / '.runtime' / 'runtime.lock'
    runtime_lock.parent.mkdir(parents=True, exist_ok=True)
    with acquire_operation_lock(root, 'recovery'), FileLock(runtime_lock, timeout=0):
        for transaction in reversed(pending_transactions(root)):
            if transaction.status == 'invalid' or (automatic and transaction.startup_attempted):
                continue
            transaction.startup_attempted = True
            transaction.save()
            succeeded = transaction.rollback()
            if transaction.operation == 'update':
                status_file = root / 'logs' / 'update' / 'last_update_result.json'
                if status_file.is_file():
                    try:
                        status = json.loads(status_file.read_text(encoding='utf-8'))
                        if (status.get('base_version'), status.get('target_version')) == (transaction.base_version, transaction.target_version):
                            status.update(status='failed', rollback_performed=True, rollback_succeeded=succeeded, notified=False)
                            temporary = status_file.with_suffix('.json.tmp')
                            temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
                            temporary.replace(status_file)
                    except Exception:
                        transaction.log_exception('[恢复] 更新结果提示记录写入失败')
        prune_completed(root)
        return not pending_transactions(root)
