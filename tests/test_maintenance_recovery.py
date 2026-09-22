from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'GPT_SoVITS'))
sys.path.insert(0, str(ROOT))

from maintenance.transactions import Transaction, load_transactions, pending_transactions, recommended_version, recover_pending, reconcile_recovery
from tools.repair import choose_version


def digest(value: bytes) -> str:
    """计算模拟文件的内容哈希。"""
    return hashlib.sha256(value).hexdigest()


class RecoveryTest(unittest.TestCase):
    """验证磁盘事务在失败和再次启动后的恢复行为。"""

    def test_failure_does_not_block_other_files_and_retry_is_once(self) -> None:
        """一个备份损坏不阻断其他恢复，自动重试只执行一次。"""
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            for name in ('first.py', 'locked.pyd'):
                (root / name).write_bytes(b'old')
            tx = Transaction.create(root, 'update', '3.2.0', '3.5.0')
            for name in ('first.py', 'locked.pyd'):
                tx.prepare(name, digest(b'new'))
                (root / name).write_bytes(b'new')
            (tx.directory / 'files' / 'locked.pyd').write_bytes(b'bad backup')
            self.assertFalse(tx.rollback())
            self.assertEqual((root / 'first.py').read_bytes(), b'old')
            self.assertIn('locked.pyd', (tx.directory / 'recovery.log').read_text())
            self.assertFalse(recover_pending(root))
            (tx.directory / 'files' / 'locked.pyd').write_bytes(b'old')
            self.assertFalse(recover_pending(root))
            self.assertEqual((root / 'locked.pyd').read_bytes(), b'new')
            self.assertTrue(recover_pending(root, automatic=False))
            self.assertEqual((root / 'locked.pyd').read_bytes(), b'old')

    def test_unchanged_locked_file_needs_no_write(self) -> None:
        """更新写入未发生时，回滚跳过已等于备份的占用文件。"""
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root / 'library.pyd').write_bytes(b'old')
            tx = Transaction.create(root, 'update', '3.2.0', '3.5.0')
            tx.prepare('library.pyd', digest(b'new'))
            with patch('maintenance.transactions.shutil.copy2', side_effect=PermissionError('locked')):
                self.assertTrue(tx.rollback())

    def test_restart_recovers_added_removed_and_user_modified_files(self) -> None:
        """重载记录后恢复删除项和修改项，删除新增项并保留用户改动。"""
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root / 'modified.py').write_bytes(b'old')
            (root / 'removed.py').write_bytes(b'removed old')
            tx = Transaction.create(root, 'update', '3.2.0', '3.5.0')
            tx.prepare('modified.py', digest(b'new'))
            tx.prepare('removed.py', None)
            tx.prepare('added.py', digest(b'new'))
            (root / 'modified.py').write_bytes(b'user edit')
            (root / 'removed.py').unlink()
            (root / 'added.py').write_bytes(b'new')
            self.assertTrue(recover_pending(root))
            self.assertEqual((root / 'modified.py').read_bytes(), b'old')
            self.assertEqual((root / 'removed.py').read_bytes(), b'removed old')
            self.assertFalse((root / 'added.py').exists())
            conflicts = list((tx.directory / 'conflicts').rglob('modified.py'))
            self.assertEqual(conflicts[0].read_bytes(), b'user edit')

    def test_target_version_survives_broken_version_file(self) -> None:
        """未完成更新记录优先于损坏或已经更新的版本文件。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'version.json').write_text('{broken')
            self.assertIsNone(recommended_version(root))
            Transaction.create(root, 'update', '3.2.0', '3.5.0')
            self.assertEqual(recommended_version(root), '3.2.0')
            (root / 'version.json').write_text('{"version":"3.5.0"}')
            self.assertEqual(recommended_version(root), '3.2.0')

    def test_repair_does_not_clear_unrestored_binary(self) -> None:
        """代码修复不能清除仍有原生库差异的更新失败状态。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'binary.pyd').write_bytes(b'old')
            tx = Transaction.create(root, 'update', '3.2.0', '3.5.0')
            tx.prepare('binary.pyd', digest(b'new'))
            (root / 'binary.pyd').write_bytes(b'new')
            reconcile_recovery(root)
            self.assertEqual(len(pending_transactions(root)), 1)
            (root / 'binary.pyd').write_bytes(b'old')
            reconcile_recovery(root)
            self.assertFalse(pending_transactions(root))

    def test_cleanup_retains_pending_and_latest_completed(self) -> None:
        """清理只移除已完成旧事务，未完成备份保持可恢复。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = Transaction.create(root, 'update', '3.2.0', '3.5.0')
            old = Transaction.create(root, 'repair', '3.2.0', '3.2.0')
            old.complete()
            latest = Transaction.create(root, 'repair', '3.2.0', '3.2.0')
            latest.complete()
            self.assertTrue(pending.directory.exists())
            self.assertFalse(old.directory.exists())
            self.assertTrue(latest.directory.exists())

    def test_cli_version_selection(self) -> None:
        """支持接受推荐、选择内置项和输入其他版本。"""
        with contextlib.redirect_stdout(io.StringIO()):
            for recommendation, answer, expected in [('3.2.0', '', '3.2.0'), (None, '1', '3.5.0'), (None, '3.1.0', '3.1.0')]:
                with patch('builtins.input', return_value=answer):
                    self.assertEqual(choose_version(recommendation), expected)

    def test_cli_import_does_not_load_business_or_updater(self) -> None:
        """独立命令行导入不得加载界面、模型或整个更新执行器。"""
        result = subprocess.run([sys.executable, '-c',
            'import tools.repair; import tools.apply_repair; import sys; '
            'assert not any(n.startswith(("PyQt5", "torch", "live2d")) for n in sys.modules); '
            'assert "tools.apply_update_patch" not in sys.modules'], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

class UpdateTransactionIntegrationTest(unittest.TestCase):
    """验证真实更新入口把故障写入独立恢复事务。"""

    def test_partial_binary_write_recovers_other_files_then_retries(self) -> None:
        """模拟 Windows 持续拒绝二进制写入，验证回滚和启动重试。"""
        import argparse
        import shutil
        from tools import apply_update_patch as updater
        original_copy = shutil.copy2
        original_replace = Path.replace
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            root = Path(directory).resolve()
            package = root / 'package'
            package.mkdir()
            (package / 'patch.hdiff').touch()
            binary = root / 'library.pyd'
            (root / 'version.json').write_text('{"version":"3.2.0"}')
            for name in ('first.py', 'library.pyd'):
                (root / name).write_bytes(b'old')
            manifest = {'app_id':'D_sakiko','channel':'stable','platform':'macos','arch':'arm64',
                        'min_updater_version':'1.0.0','mode':'hdiff','base_version':'3.2.0',
                        'target_version':'3.5.0','patch_file':'patch.hdiff', 'files':[
                            {'path':name,'action':'modify','old_file_sha256':digest(b'old'),'sha256':digest(b'new')}
                            for name in ('first.py','library.pyd')]}
            (package / 'manifest.json').write_text(json.dumps(manifest))
            hpatch = root / 'hpatchz'
            hpatch.touch()
            hpatch.chmod(0o755)

            def stage(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                """模拟差分工具生成经过哈希校验的新文件。"""
                destination = Path(command[-1])
                destination.mkdir()
                for name in ('first.py','library.pyd'):
                    (destination / name).write_bytes(b'new')
                return subprocess.CompletedProcess(command, 0, 'patch ok', '')

            def interrupted_copy(source: Path, target: Path, **kwargs: object) -> str:
                """模拟覆盖二进制时发生部分写入然后拒绝访问。"""
                if Path(target) == binary:
                    binary.write_bytes(b'partial')
                    raise PermissionError('injected binary write failure')
                return str(original_copy(source, target))

            def locked_replace(source: Path, target: Path) -> Path:
                """模拟同一个二进制在回滚时依旧拒绝替换。"""
                if Path(target) == binary:
                    raise PermissionError('injected rollback failure')
                return original_replace(source, target)

            args = argparse.Namespace(manifest='manifest.json', version_file='version.json', no_remove_package=True,
                                      after_updater_restart=False)
            recorder = updater.UpdateResultRecorder(root, None, root/'update.log')
            with patch.object(updater, 'detect_platform', return_value='macos'), patch.object(updater, 'detect_arch', return_value='arm64'), patch.object(updater.subprocess, 'run', side_effect=stage), patch.object(shutil, 'copy2', side_effect=interrupted_copy), patch.object(Path, 'replace', locked_replace):
                self.assertEqual(updater.apply_package_chain(root, root/'version.json', [package], hpatch, args, recorder), 1)
            self.assertEqual((root/'first.py').read_bytes(), b'old')
            self.assertEqual(binary.read_bytes(), b'partial')
            self.assertEqual(len(pending_transactions(root)), 1)
            self.assertTrue(recover_pending(root))
            self.assertEqual(binary.read_bytes(), b'old')


if __name__ == '__main__':
    unittest.main()
