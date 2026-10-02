from __future__ import annotations

# ruff: noqa: E402

import contextlib
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'GPT_SoVITS'))
sys.path.insert(0, str(ROOT))

from maintenance import file_operations as files, windows_file_users as users
from maintenance.transactions import Transaction, pending_transactions
from repair.repair_manifest import sha256_file
from tools import apply_update_patch as updater


def sharing_error(code: int = 32) -> OSError:
    error = PermissionError('file is temporarily locked')
    error.winerror = code
    return error


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FileOperationTest(unittest.TestCase):
    def test_other_work_finishes_before_retry_and_attempts_are_five_seconds_apart(self) -> None:
        clock = FakeClock()
        events = []

        def locked() -> None:
            events.append(('locked', clock.now))
            if len([event for event in events if event[0] == 'locked']) < 3:
                raise sharing_error()

        def other() -> None:
            events.append(('other', clock.now))
            clock.now += 8.0

        with patch.object(files.time, 'monotonic', side_effect=lambda: clock.now), \
                patch.object(files.time, 'sleep', side_effect=clock.sleep), \
                patch.object(files, 'describe_file_users', side_effect=RuntimeError('query failed')):
            files.run_file_operations([
                files.FileOperation(ROOT / 'locked.pyd', 'update', locked),
                files.FileOperation(ROOT / 'other.py', 'update', other),
            ], log=lambda message: None)
        self.assertEqual(events, [('locked', 0.0), ('other', 0.0), ('locked', 8.0), ('locked', 13.0)])

    def test_persistent_lock_has_exactly_three_attempts_and_does_not_stop_other_rollback(self) -> None:
        clock = FakeClock()
        blocked, successful = Mock(side_effect=sharing_error()), Mock()
        failures = []
        with patch.object(files.time, 'monotonic', side_effect=lambda: clock.now), \
                patch.object(files.time, 'sleep', side_effect=clock.sleep), \
                patch.object(files, 'describe_file_users', return_value='diagnostic') as diagnose:
            files.run_file_operations([
                files.FileOperation(ROOT / 'locked.pyd', 'rollback', blocked, failed=failures.append),
                files.FileOperation(ROOT / 'other.py', 'rollback', successful),
            ], continue_on_error=True, log=lambda message: None)
        self.assertEqual(blocked.call_count, 3)
        successful.assert_called_once()
        self.assertEqual(len(failures), 3)
        self.assertEqual(clock.now, 10.0)
        self.assertEqual(diagnose.call_count, 2)

    def test_non_lock_failure_is_not_retried(self) -> None:
        for error in (OSError('disk full'), RuntimeError('hash mismatch')):
            operation = Mock(side_effect=error)
            with patch.object(files.time, 'sleep') as sleep:
                with self.assertRaises(type(error)):
                    files.run_file_operations([
                        files.FileOperation(ROOT / 'file.py', 'update', operation),
                    ], log=lambda message: None)
            operation.assert_called_once()
            sleep.assert_not_called()

    def test_atomic_copy_failure_does_not_truncate_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, target = root / 'source', root / 'target.pyd'
            source.write_bytes(b'new')
            target.write_bytes(b'old')

            def partial(source: Path, temporary: Path) -> None:
                temporary.write_bytes(b'partial')
                raise sharing_error()

            with patch.object(files.shutil, 'copy2', side_effect=partial):
                with self.assertRaises(OSError):
                    files.atomic_copy_file(source, target, sha256_file(source))
            self.assertEqual(target.read_bytes(), b'old')
            self.assertFalse(list(root.glob('.target.pyd.*')))

    def test_replace_exception_is_success_only_when_target_content_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, target = root / 'source', root / 'target.pyd'
            source.write_bytes(b'new')
            target.write_bytes(b'old')
            real_replace = os.replace

            def replace_then_error(source: Path, target: Path) -> None:
                real_replace(source, target)
                raise sharing_error()

            with patch.object(files.os, 'replace', side_effect=replace_then_error):
                files.atomic_copy_file(source, target, sha256_file(source))
            self.assertEqual(target.read_bytes(), b'new')

    def test_update_defers_locked_diff_until_replace_payload_has_finished(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory).resolve()
            staging, package = root / 'staging', root / 'package'
            staging.mkdir()
            package.mkdir()
            (staging / 'locked.pyd').write_bytes(b'new')
            (package / 'payload').write_bytes(b'other')
            (root / 'locked.pyd').write_bytes(b'old')
            tx = Transaction.create(root, 'update', '3.5.0', '4.0.0')
            manifest = {'files': [
                dict(path='locked.pyd', action='modify', sha256=sha256_file(staging / 'locked.pyd')),
                dict(path='other.py', action='replace', payload='payload', size=5,
                     sha256=sha256_file(package / 'payload')),
            ]}
            real_replace = os.replace
            attempts = []

            def replace(source: Path, target: Path) -> None:
                if Path(target) == root / 'locked.pyd':
                    attempts.append((root / 'other.py').exists())
                    if len(attempts) == 1:
                        raise sharing_error()
                real_replace(source, target)

            with patch.object(files.os, 'replace', side_effect=replace), \
                    patch.object(files.time, 'sleep'), patch.object(files, 'describe_file_users', return_value='query unavailable'):
                updater.apply_staged_files(root, package, manifest, staging, tx.directory, [], tx)
            self.assertEqual(attempts, [False, True])
            self.assertEqual((root / 'locked.pyd').read_bytes(), b'new')
            self.assertEqual((tx.directory / 'files' / 'locked.pyd').read_bytes(), b'old')
            self.assertEqual(len(tx.records), 2)

    def test_persistent_rollback_lock_retains_backup_and_can_be_recovered_later(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory).resolve()
            for name in ('other.py', 'locked.pyd'):
                (root / name).write_bytes(b'old')
            tx = Transaction.create(root, 'update', '3.5.0', '4.0.0')
            for name in ('other.py', 'locked.pyd'):
                tx.prepare(name, None)
                (root / name).write_bytes(b'new')
            real_replace = os.replace
            attempts = []

            def replace(source: Path, target: Path) -> None:
                if Path(target) == root / 'locked.pyd':
                    attempts.append((root / 'other.py').read_bytes())
                    raise sharing_error()
                real_replace(source, target)

            with patch.object(files.os, 'replace', side_effect=replace), \
                    patch.object(files.time, 'sleep'), patch.object(files, 'describe_file_users', return_value='PID=123'):
                self.assertFalse(tx.rollback())
            self.assertEqual(attempts, [b'new', b'old', b'old'])
            self.assertEqual((tx.directory / 'files' / 'locked.pyd').read_bytes(), b'old')
            self.assertEqual(len(pending_transactions(root)), 1)
            self.assertIn('PID=123', (tx.directory / 'recovery.log').read_text())
            self.assertTrue(tx.rollback())
            self.assertEqual((root / 'locked.pyd').read_bytes(), b'old')

    def test_unreadable_original_is_not_marked_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory).resolve()
            target = root / 'locked.pyd'
            target.write_bytes(b'old')
            tx = Transaction.create(root, 'update', '3.5.0', '4.0.0')
            tx.prepare('locked.pyd', None)
            with patch('maintenance.transactions.sha256_file', side_effect=sharing_error()), \
                    patch.object(files.time, 'sleep'), patch.object(files, 'describe_file_users', return_value='unknown'):
                self.assertFalse(tx.rollback())
            self.assertFalse(tx.records[0].restored)
            self.assertEqual(tx.status, 'recovery_failed')

    def test_bad_later_payload_is_rejected_before_any_target_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            staging, package = root / 'staging', root / 'package'
            staging.mkdir()
            package.mkdir()
            (staging / 'first.py').write_bytes(b'new')
            (root / 'first.py').write_bytes(b'old')
            (package / 'payload').write_bytes(b'bad')
            tx = Transaction.create(root, 'update', '3.5.0', '4.0.0')
            manifest = {'files': [
                dict(path='first.py', action='modify', sha256=sha256_file(staging / 'first.py')),
                dict(path='second.py', action='replace', payload='payload', size=3, sha256='0' * 64),
            ]}
            with self.assertRaises(RuntimeError):
                updater.apply_staged_files(root, package, manifest, staging, tx.directory, [], tx)
            self.assertEqual((root / 'first.py').read_bytes(), b'old')
            self.assertEqual(tx.records, [])

    def test_parent_operation_waits_for_pending_child(self) -> None:
        attempts = []

        def child() -> None:
            attempts.append('child')
            if attempts.count('child') == 1:
                raise sharing_error()

        with patch.object(files.time, 'sleep'), patch.object(files, 'describe_file_users', return_value='unknown'):
            files.run_file_operations([
                files.FileOperation(ROOT / 'folder' / 'child', 'remove', child),
                files.FileOperation(ROOT / 'folder', 'remove', lambda: attempts.append('parent')),
            ], log=lambda message: None)
        self.assertEqual(attempts, ['child', 'child', 'parent'])

    def test_version_write_failure_keeps_previous_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            target = Path(directory).resolve() / 'version.json'
            original = b'{"version":"3.5.0"}'
            target.write_bytes(original)
            with patch.object(files.os, 'replace', side_effect=sharing_error()), \
                    patch.object(files.time, 'sleep'), patch.object(files, 'describe_file_users', return_value='unknown'):
                with self.assertRaises(OSError):
                    updater.write_json(target, {'version': '4.0.0'})
            self.assertEqual(target.read_bytes(), original)

    @unittest.skipUnless(os.name == 'nt', '需要真实 Windows 文件共享语义')
    def test_real_windows_handle_prevents_replace_until_closed(self) -> None:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'new.pyd', root / 'locked.pyd'
            source.write_bytes(b'new')
            target.write_bytes(b'old')
            handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0x80, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            try:
                with self.assertRaises(OSError):
                    files.atomic_copy_file(source, target, sha256_file(source))
                self.assertEqual(target.read_bytes(), b'old')
            finally:
                kernel.CloseHandle(handle)
            files.atomic_copy_file(source, target, sha256_file(source))
            self.assertEqual(target.read_bytes(), b'new')


class FileUsersTest(unittest.TestCase):
    def test_query_failure_and_timeout_are_diagnostic_only(self) -> None:
        path = ROOT / 'locked.pyd'
        with patch.object(users, 'os', SimpleNamespace(name='nt')), \
                patch.object(users, 'query_file_users', side_effect=OSError('denied')):
            self.assertIn('查询失败', users.describe_file_users(path))
        release = threading.Event()
        try:
            with patch.object(users, 'os', SimpleNamespace(name='nt')), \
                    patch.object(users, 'query_file_users', side_effect=lambda path: release.wait(1)):
                self.assertIn('查询超时', users.describe_file_users(path, timeout=0.01))
        finally:
            release.set()

    def test_restart_manager_reports_processes_and_always_ends_session(self) -> None:
        path = ROOT / 'locked.pyd'
        manager = Mock()
        manager.RmStartSession.return_value = 0
        manager.RmRegisterResources.return_value = 0
        manager.RmEndSession.return_value = 0

        def get_list(handle, needed, count, processes, reason):
            if processes is None:
                needed._obj.value = 1
                return 234
            processes[0].process.pid = 123
            processes[0].name = 'scanner'
            processes[0].service = 'scan-service'
            count._obj.value = 1
            return 0

        manager.RmGetList.side_effect = get_list
        with patch.object(users, 'os', SimpleNamespace(name='nt')), patch('ctypes.WinDLL', return_value=manager, create=True):
            result = users.query_file_users(path)
            self.assertEqual(result[0]['pid'], 123)
            self.assertEqual(result[0]['name'], 'scanner')
            manager.RmEndSession.assert_called_once()
            manager.RmEndSession.reset_mock()
            manager.RmRegisterResources.return_value = 5
            with self.assertRaises(OSError):
                users.query_file_users(path)
            manager.RmEndSession.assert_called_once()


if __name__ == '__main__':
    unittest.main()
