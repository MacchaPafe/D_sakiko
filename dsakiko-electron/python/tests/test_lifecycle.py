"""验证关闭确认与宿主心跳，不加载模型或依赖实际 GPU。"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import sys
import tempfile
import threading
import unittest
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event
from pathlib import Path
from typing import Optional, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from speech.contracts import SchedulerConfig, VoiceProfile
from speech.heartbeat import HostProbe, watch_host
from speech.scheduler import Resident, Scheduler, Worker
from speech.worker import ProcessWorker


def idle_worker(ready: Event) -> None:
    """真实子进程等待关闭信号，不加载推理依赖。"""
    ready.set()
    threading.Event().wait()


class ControlledProcess:
    """模拟 kill 已发出但操作系统尚未报告退出的进程。"""

    def __init__(self) -> None:
        """用线程事件控制 join，确保关闭调用不能提前完成。"""
        self.exitcode: Optional[int] = None
        self.waiting = threading.Event()
        self.finished = threading.Event()
        self.terminated = False
        self.killed = False

    def is_alive(self) -> bool:
        """只有完成 join 后才确认进程消失。"""
        return self.exitcode is None

    def terminate(self) -> None:
        """记录第一次终止请求。"""
        self.terminated = True

    def kill(self) -> None:
        """记录升级请求，但不把请求发送成功当成退出。"""
        self.killed = True

    def join(self, timeout: Optional[float] = None) -> None:
        """第一次模拟超时，第二次等待测试明确允许退出。"""
        if timeout is not None:
            return
        self.waiting.set()
        if not self.finished.wait(3):
            raise RuntimeError("测试未释放进程")
        self.exitcode = -9


class ControlledPipe:
    """只记录确认退出后的管道关闭。"""

    def __init__(self) -> None:
        """初始仍可使用。"""
        self.closed = False

    def close(self) -> None:
        """记录释放。"""
        self.closed = True


class WaitingWorker:
    """让调度器关闭必须等待明确确认。"""

    def __init__(self) -> None:
        """创建进入关闭和退出确认两个信号。"""
        self.entered = asyncio.Event()
        self.finished = asyncio.Event()

    async def synthesize(self, request: object, output: Path) -> float:
        """此场景只验证驻留实例关闭，不执行推理。"""
        raise AssertionError("不应执行合成")

    async def close(self) -> None:
        """等待测试模拟操作系统确认结束。"""
        self.entered.set()
        await self.finished.wait()


class FailingWorker(WaitingWorker):
    """验证调度器不会吞掉退出确认失败。"""

    async def close(self) -> None:
        """明确报告无法确认退出。"""
        raise RuntimeError("退出确认失败")


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    """覆盖重复关闭、取消等待和心跳连续失败的语义。"""

    async def test_worker_close_waits_for_actual_exit(self) -> None:
        """并发关闭都等 join，取消一个等待者也不取消实际回收。"""
        process = ControlledProcess()
        pipe = ControlledPipe()
        worker = ProcessWorker.__new__(ProcessWorker)
        worker.process = cast(BaseProcess, process)
        worker.pipe = cast(Connection, pipe)
        worker.closing = False
        worker.close_task = None
        first = asyncio.create_task(worker.close())
        try:
            self.assertTrue(await asyncio.to_thread(process.waiting.wait, 1))
            second = asyncio.create_task(worker.close())
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertFalse(pipe.closed)
            process.finished.set()
            await second
            self.assertTrue(process.terminated and process.killed and pipe.closed)
            self.assertFalse(process.is_alive())
        finally:
            process.finished.set()
            await worker.close()

    async def test_worker_close_joins_real_process(self) -> None:
        """真实 spawn 子进程结束后，关闭调用才返回并关闭管道。"""
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        process = context.Process(target=idle_worker, args=(ready,))
        parent_pipe, child_pipe = context.Pipe()
        process.start()
        worker = ProcessWorker.__new__(ProcessWorker)
        worker.process = process
        worker.pipe = parent_pipe
        worker.closing = False
        worker.close_task = None
        try:
            self.assertTrue(await asyncio.to_thread(ready.wait, 3))
            await worker.close()
            self.assertFalse(process.is_alive())
            self.assertIsNotNone(process.exitcode)
            self.assertTrue(parent_pipe.closed)
        finally:
            if process.is_alive():
                process.kill()
            await asyncio.to_thread(process.join)
            parent_pipe.close()
            child_pipe.close()

    async def test_scheduler_reports_shutdown_failure(self) -> None:
        """关闭失败不能被当作 worker 全部结束。"""
        with tempfile.TemporaryDirectory() as directory:
            worker = FailingWorker()
            scheduler = Scheduler(SchedulerConfig(1, 1, directory, 1000), lambda _voice: cast(Worker, worker))
            scheduler.residents.append(Resident(VoiceProfile("g", "s", "r", "text", "ja"), cast(Worker, worker)))
            with self.assertRaisesRegex(RuntimeError, "退出确认失败"):
                await scheduler.close()

    async def test_scheduler_close_shares_confirmation(self) -> None:
        """调度器第二次关闭也必须等待全部驻留实例。"""
        with tempfile.TemporaryDirectory() as directory:
            worker = WaitingWorker()
            scheduler = Scheduler(SchedulerConfig(1, 1, directory, 1000), lambda _voice: cast(Worker, worker))
            scheduler.residents.append(Resident(VoiceProfile("g", "s", "r", "text", "ja"), cast(Worker, worker)))
            first = asyncio.create_task(scheduler.close())
            await worker.entered.wait()
            second = asyncio.create_task(scheduler.close())
            await asyncio.sleep(0)
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            worker.finished.set()
            await asyncio.gather(first, second)

    async def test_heartbeat_failure_requires_continuous_interval(self) -> None:
        """一次恢复会重置失败计时，休眠后成功也不被旧时间误判。"""
        stopping = asyncio.Event()
        samples = iter([("unavailable", 0.0), ("alive", 1000.0), ("unavailable", 1001.0), ("unavailable", 1060.0), ("unavailable", 1061.0)])
        now = 0.0
        count = 0

        async def probe() -> str:
            """提供带逻辑时间的探测结果。"""
            nonlocal now, count
            status, now = next(samples)
            count += 1
            return status

        await watch_host(stopping, probe, interval=0.001, timeout=60, clock=lambda: now)
        self.assertTrue(stopping.is_set())
        self.assertEqual(count, 5)

    async def test_heartbeat_changed_run_stops_immediately(self) -> None:
        """服务被替换或已开始关闭时，不等待超时窗口。"""
        stopping = asyncio.Event()

        async def probe() -> str:
            """模拟旧宿主身份已经失效。"""
            return "gone"

        await watch_host(stopping, probe)
        self.assertTrue(stopping.is_set())

    def test_probe_configuration_only_accepts_loopback(self) -> None:
        """探测凭据只能发送到宿主提供的固定回环接口。"""
        self.assertIsNone(HostProbe.parse(""))
        valid = {"url": "http://127.0.0.1:1234/api/v1/health", "token": "limited", "backendRunId": "host"}
        self.assertIsNotNone(HostProbe.parse(json.dumps(valid)))
        with self.assertRaises(ValueError):
            HostProbe.parse(json.dumps({**valid, "url": "https://example.com/api/v1/health"}))
