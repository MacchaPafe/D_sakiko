"""验证运行目录归属、崩溃回收和清理失败后的重试。"""

from __future__ import annotations

import argparse
import io
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Dict, cast
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from speech.runtime import OWNER_FILE, RunDirectory, process_exists
from speech.server import serve


def create_crash_fixture(root: str, pipe: Connection) -> None:
    """创建真实归属目录后保持存活，供父进程模拟异常终止。"""
    runtime = RunDirectory.create(Path(root), os.getpid())
    (runtime.path / "partial.wav.tmp.wav").write_bytes(b"partial")
    pipe.send(str(runtime.path))
    pipe.close()
    threading.Event().wait()


class RuntimeTests(unittest.TestCase):
    """只删除归属明确且所有使用者已经退出的目录。"""

    def setUp(self) -> None:
        """每次使用独立缓存根目录，不接触开发者真实缓存。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def owner(self, runtime: RunDirectory, **changes: object) -> None:
        """改变归属记录以覆盖失效、占用与保守保留分支。"""
        marker = runtime.path / OWNER_FILE
        value = cast(Dict[str, object], json.loads(marker.read_text()))
        value.update(changes)
        marker.write_text(json.dumps(value))

    def test_cleanup_removes_outputs_and_caches_but_not_external_files(self) -> None:
        """清理幂等，内部链接也不能使删除越过本次目录。"""
        runtime = RunDirectory.create(self.root, os.getpid())
        external = self.root / "model"
        external.mkdir()
        (external / "weights").write_bytes(b"model")
        (runtime.path / "result.wav").write_bytes(b"audio")
        cache = runtime.path / "worker" / "matplotlib"
        cache.mkdir(parents=True)
        (cache / "font.json").write_text("{}")
        (runtime.path / "link").symlink_to(external, target_is_directory=True)
        runtime.cleanup()
        runtime.cleanup()
        self.assertFalse(runtime.path.exists())
        self.assertEqual((external / "weights").read_bytes(), b"model")

    def test_crashed_process_is_collected_after_exit(self) -> None:
        """真实进程存活期间保留，强制终止后下次创建运行目录会回收。"""
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=create_crash_fixture, args=(str(self.root), child))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(5))
            path = Path(parent.recv())
            RunDirectory.collect_stale(self.root)
            self.assertTrue(path.exists())
            process.kill()
            process.join(5)
            self.assertFalse(process.is_alive())
            self.assertFalse(process_exists(process.pid))
            current = RunDirectory.create(self.root, os.getpid())
            self.assertFalse(path.exists())
            self.assertTrue(current.path.exists())
        finally:
            if process.is_alive():
                process.kill()
            process.join()
            parent.close()

    def test_host_python_and_group_each_protect_directory(self) -> None:
        """宿主可能还在导入音频，任何使用者存活都不得清理。"""
        for alive in (101, 102, -102):
            with self.subTest(alive=alive):
                runtime = RunDirectory.create(self.root, os.getpid())
                self.owner(runtime, hostPid=101, pythonPid=102, processGroupId=102)
                with patch("speech.runtime.process_exists", side_effect=lambda pid: pid == alive):
                    if alive < 0 and os.name == "nt":
                        continue
                    RunDirectory.collect_stale(self.root)
                self.assertTrue(runtime.path.exists())
                runtime.cleanup()

    def test_invalid_unknown_and_symlink_directories_are_preserved(self) -> None:
        """损坏记录、旧目录和目录链接均不提供自动删除依据。"""
        runtime = RunDirectory.create(self.root, os.getpid())
        (runtime.path / OWNER_FILE).write_text("broken")
        unknown = self.root / "old-unmarked"
        unknown.mkdir()
        link = self.root / ("run-" + "f" * 32)
        link.symlink_to(runtime.path, target_is_directory=True)
        with patch("speech.runtime.process_exists", return_value=False), self.assertLogs("speech.runtime", "WARNING"):
            RunDirectory.collect_stale(self.root)
        self.assertTrue(runtime.path.exists())
        self.assertTrue(unknown.exists())
        self.assertTrue(link.is_symlink())

    def test_partial_cleanup_keeps_owner_for_next_start(self) -> None:
        """占用的缓存删除失败后仍保留归属，下次启动可成功重试。"""
        runtime = RunDirectory.create(self.root, os.getpid())
        (runtime.path / "cache").mkdir()
        with patch("speech.runtime.shutil.rmtree", side_effect=PermissionError("busy")), self.assertLogs("speech.runtime", "WARNING"):
            runtime.cleanup()
        self.assertTrue((runtime.path / OWNER_FILE).is_file())
        with patch("speech.runtime.process_exists", return_value=False):
            RunDirectory.collect_stale(self.root)
        self.assertFalse(runtime.path.exists())

    def test_failed_owner_registration_removes_new_directory(self) -> None:
        """还未启动 worker 时登记失败，可以立即回收本次空目录。"""
        with patch.object(Path, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                RunDirectory.create(self.root, os.getpid())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_permission_error_is_not_process_exit(self) -> None:
        """POSIX 无权查询时必须保留引用，不能当作 PID 已退出。"""
        if os.name == "nt":
            self.skipTest("此分支验证 POSIX kill 探测")
        with patch("speech.runtime.os.kill", side_effect=PermissionError("denied")):
            self.assertTrue(process_exists(123))


class ServerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """验证服务启动失败和退出失败时的目录处理顺序。"""

    async def test_startup_failure_cleans_directory(self) -> None:
        """HTTP 绑定失败也要清理，不能只覆盖就绪后的退出流程。"""
        await self.check_startup_failure(False)

    async def test_unconfirmed_worker_exit_preserves_directory(self) -> None:
        """关闭失败必须保留运行目录，不能在 finally 中无条件删除。"""
        await self.check_startup_failure(True)

    async def check_startup_failure(self, close_fails: bool) -> None:
        """提供最小启动数据，隔离模型加载并模拟服务启动失败。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            startup = {"resources": {}, "device": "cpu", "precision": "float32", "architecture": "v2"}
            stdin = io.TextIOWrapper(io.BytesIO(json.dumps(startup).encode()))
            args = argparse.Namespace(assets_root=directory, output=str(root / "runs"), concurrent=1, resident=1)
            close = AsyncMock(side_effect=RuntimeError("close") if close_fails else None)
            with patch("speech.server.sys.stdin", stdin), patch.dict(os.environ, {"DSAKIKO_SPEECH_TOKEN": "test", "DSAKIKO_HOST_PROBE": "", "DSAKIKO_SPEECH_HOST_PID": str(os.getpid())}), patch("speech.server.GptSovitsResources.parse"), patch("speech.server.asyncio.start_server", side_effect=OSError("bind")), patch("speech.server.Scheduler.close", close):
                with self.assertRaisesRegex((RuntimeError, OSError), "close" if close_fails else "bind"):
                    await serve(args)
            self.assertEqual(len(list((root / "runs").iterdir())), 1 if close_fails else 0)
            close.assert_awaited_once()


class InferenceImportTests(unittest.TestCase):
    """验证训练演示的绘图模块不会由调度器导入触发。"""

    def test_scheduler_import_does_not_import_matplotlib(self) -> None:
        """在新解释器阻止 Matplotlib 导入，实际导入学习率调度器仍应成功。"""
        vendor = Path(__file__).resolve().parents[1] / "vendor" / "gpt_sovits"
        script = '''import sys
sys.path.insert(0, sys.argv[1])
sys.modules["matplotlib"] = None
from AR.modules.lr_schedulers import WarmupCosineLRSchedule
assert WarmupCosineLRSchedule.__name__ == "WarmupCosineLRSchedule"
'''
        result = subprocess.run([sys.executable, "-B", "-c", script, str(vendor)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
