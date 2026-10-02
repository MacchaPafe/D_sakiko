from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools import build_diff_patch as builder
from tools import apply_update_patch as updater


class DiffPackagingTest(unittest.TestCase):
    """验证打包输入和实际文件恢复边界。"""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.old, self.new, self.output = (self.root / name for name in ('old', 'new', 'package'))
        self.old.mkdir()
        self.new.mkdir()
        self.tool = self.root / 'hdiffz'
        self.tool.touch()
        self.tool.chmod(0o755)
        self.inputs = {}

    def write(self, root: Path, path: str, value: bytes) -> None:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)

    def arguments(self, *extra: str) -> list[str]:
        return ['build_diff_patch', '--current', str(self.new), '--old', str(self.old),
                '--output', str(self.output), '--base-version', '3.5.0', '--target-version', '4.0.0',
                '--platform', 'macos', '--arch', 'arm64', '--hdiff-bin', str(self.tool),
                '--no-platform-includes', *extra]

    def build(self, *extra: str) -> dict:
        def diff(hdiff_bin, old_stage, new_stage, out_diff_file, options):
            self.inputs = {name: {p.relative_to(root).as_posix(): p.read_bytes()
                                  for p in root.rglob('*') if p.is_file()}
                           for name, root in (('old', old_stage), ('new', new_stage))}
            out_diff_file.write_text(json.dumps({
                'old': {p: builder.sha256_file(old_stage / p) for p in self.inputs['old']},
                'new': {p: value.hex() for p, value in self.inputs['new'].items()},
            }), encoding='utf-8')

        with patch.object(sys, 'argv', self.arguments(*extra)), patch.object(builder, 'run_hdiff', side_effect=diff), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(builder.main(), 0)
        return json.loads((self.output / 'manifest.json').read_text(encoding='utf-8'))

    def test_changed_small_files_only_and_exact_boundary(self) -> None:
        pairs = {
            'small.py': (b'old', b'new'),
            'empty.txt': (b'old', b''),
            'boundary.bin': (b'o' * 64, b'n' * 64),
            'grew.bin': (b'o', b'n' * 65),
            'shrank.bin': (b'o' * 100, b'n' * 63),
            'same.py': (b'unchanged', b'unchanged'),
            'manual.bin': (b'x' * 100, b'x' * 100),
        }
        for name, (old, new) in pairs.items():
            self.write(self.old, name, old)
            self.write(self.new, name, new)
        self.write(self.new, 'added.py', b'added')
        self.write(self.old, 'removed.py', b'removed')
        archive = self.root / 'patch.zip'
        manifest = self.build('--small-file-replace-threshold', '64', '--replace', 'manual.bin',
                              '--zip-output', str(archive))
        records = {item['path']: item for item in manifest['files']}
        self.assertEqual({name: item['action'] for name, item in records.items()}, {
            'small.py': 'replace', 'empty.txt': 'replace', 'shrank.bin': 'replace',
            'manual.bin': 'replace', 'boundary.bin': 'modify', 'grew.bin': 'modify',
            'added.py': 'add', 'removed.py': 'remove',
        })
        self.assertEqual(set(self.inputs['old']), {'boundary.bin', 'grew.bin'})
        self.assertEqual(set(self.inputs['new']), {'boundary.bin', 'grew.bin', 'added.py'})
        self.assertEqual(manifest['min_updater_version'], '1.1.0')
        self.assertEqual(manifest['stats']['replace_count'], 4)
        for name in ('small.py', 'empty.txt', 'shrank.bin', 'manual.bin'):
            self.assertEqual(records[name]['old_file_sha256'], '')
            self.assertEqual((self.output / records[name]['payload']).read_bytes(), pairs[name][1])
        with zipfile.ZipFile(archive) as zip_file:
            self.assertNotIn('payload/same.py', zip_file.namelist())
            self.assertIn('payload/small.py', zip_file.namelist())

    def test_default_is_strictly_below_500_kib_and_can_be_disabled(self) -> None:
        threshold = builder.DEFAULT_SMALL_FILE_REPLACE_THRESHOLD
        self.write(self.old, 'below.bin', b'o')
        self.write(self.new, 'below.bin', b'n' * (threshold - 1))
        self.write(self.old, 'at.bin', b'o')
        self.write(self.new, 'at.bin', b'n' * threshold)
        manifest = self.build()
        self.assertEqual({item['path']: item['action'] for item in manifest['files']},
                         {'below.bin': 'replace', 'at.bin': 'modify'})
        manifest = self.build('--small-file-replace-threshold', '0', '--clean-output')
        self.assertTrue(all(item['action'] == 'modify' for item in manifest['files']))
        self.assertEqual(set(self.inputs['old']), {'at.bin', 'below.bin'})
        self.assertEqual(manifest['min_updater_version'], '1.0.0')
        self.assertFalse((self.output / 'payload').exists())

    def test_excluded_files_are_not_automatically_replaced(self) -> None:
        for name in ('kept.py', 'ignored.py', 'blocked.py'):
            self.write(self.old, name, b'old')
            self.write(self.new, name, b'new')
        manifest = self.build('--ignore', 'ignored.py', '--include', 'blocked.py', '--hard-exclude', 'blocked.py')
        self.assertEqual([item['path'] for item in manifest['files']], ['kept.py'])

    def apply(self, app: Path, hpatch: Path) -> int:
        args = argparse.Namespace(manifest='manifest.json', version_file='version.json',
                                  no_remove_package=True, after_updater_restart=False)
        recorder = updater.UpdateResultRecorder(app, None, app / 'update.log')
        with patch.object(updater, 'detect_platform', return_value='macos'), \
                patch.object(updater, 'detect_arch', return_value='arm64'), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return updater.apply_package_chain(app, app / 'version.json', [self.output], hpatch, args, recorder)

    def test_corrupted_changed_small_and_missing_unchanged_files_do_not_block_update(self) -> None:
        for name, old, new in (
            ('version.json', b'{"version":"3.5.0"}', b'{"version":"4.0.0"}'),
            ('small.py', b'official old', b'official new'),
            ('same.py', b'unchanged', b'unchanged'),
            ('large.bin', b'o' * 128, b'n' * 128),
        ):
            self.write(self.old, name, old)
            self.write(self.new, name, new)
        self.build('--small-file-replace-threshold', '64')
        app = self.root / 'app'
        app.mkdir()
        self.write(app, 'version.json', b'{"version":"3.5.0"}')
        self.write(app, 'small.py', b'locally broken')
        self.write(app, 'large.bin', b'o' * 128)

        def patch_files(command, **kwargs):
            data = json.loads(Path(command[-2]).read_text(encoding='utf-8'))
            app_root, destination = Path(command[-3]), Path(command[-1])
            for name, expected in data['old'].items():
                if builder.sha256_file(app_root / name) != expected:
                    return subprocess.CompletedProcess(command, 1, '', 'incorrect old data')
            destination.mkdir()
            for name, value in data['new'].items():
                self.write(destination, name, bytes.fromhex(value))
            return subprocess.CompletedProcess(command, 0, '', '')

        with patch.object(updater.subprocess, 'run', side_effect=patch_files):
            self.assertEqual(self.apply(app, self.tool), 0)
        self.assertEqual((app / 'small.py').read_bytes(), b'official new')
        self.assertEqual((app / 'large.bin').read_bytes(), b'n' * 128)
        self.assertFalse((app / 'same.py').exists())
        backups = list((app / '.updates' / 'transactions').glob('*/files/small.py'))
        self.assertEqual(backups[0].read_bytes(), b'locally broken')

    def test_only_replace_and_remove_produces_empty_hdiff_inputs(self) -> None:
        self.write(self.old, 'small.py', b'old')
        self.write(self.new, 'small.py', b'new')
        self.write(self.old, 'gone.py', b'gone')
        manifest = self.build()
        self.assertEqual(self.inputs, {'old': {}, 'new': {}})
        self.assertEqual({item['action'] for item in manifest['files']}, {'remove', 'replace'})

    @unittest.skipUnless(os.environ.get('HDIFFZ_TEST_BIN') and os.environ.get('HPATCHZ_TEST_BIN'),
                         '需要 HDIFFZ_TEST_BIN 和 HPATCHZ_TEST_BIN 指向真实工具')
    def test_native_sparse_and_empty_directory_patches(self) -> None:
        self.tool = Path(os.environ['HDIFFZ_TEST_BIN'])
        hpatch = Path(os.environ['HPATCHZ_TEST_BIN'])
        for only_replace in (False, True):
            with self.subTest(only_replace=only_replace):
                for name, old, new in (
                    ('version.json', b'{"version":"3.5.0"}', b'{"version":"4.0.0"}'),
                    ('small.py', b'official old', b'official new'),
                    ('same.py', b'unchanged', b'unchanged'),
                ):
                    self.write(self.old, name, old)
                    self.write(self.new, name, new)
                if not only_replace:
                    old_large = b'x' * builder.DEFAULT_SMALL_FILE_REPLACE_THRESHOLD + b'old'
                    new_large = b'x' * builder.DEFAULT_SMALL_FILE_REPLACE_THRESHOLD + b'new'
                    self.write(self.old, 'large.bin', old_large)
                    self.write(self.new, 'large.bin', new_large)
                else:
                    (self.old / 'large.bin').unlink()
                    (self.new / 'large.bin').unlink()
                with patch.object(sys, 'argv', self.arguments('--clean-output')), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(builder.main(), 0)
                app = self.root / ('app_empty' if only_replace else 'app_sparse')
                app.mkdir()
                self.write(app, 'version.json', b'{"version":"3.5.0"}')
                self.write(app, 'small.py', b'broken')
                if not only_replace:
                    self.write(app, 'large.bin', old_large)
                self.assertEqual(self.apply(app, hpatch), 0)
                self.assertEqual((app / 'small.py').read_bytes(), b'official new')
                self.assertFalse((app / 'same.py').exists())
                if not only_replace:
                    self.assertEqual((app / 'large.bin').read_bytes(), new_large)


if __name__ == '__main__':
    unittest.main()
