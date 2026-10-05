"""用可控推理实例验证真实调度器的并发边界，不需要加载模型。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Callable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from speech.contracts import SchedulerConfig, SpeechRequest, VoiceProfile
from speech.scheduler import RequestError, Scheduler


class FakeWorker:
    """将实际完成与取消受理分开控制的测试实例。"""

    def __init__(self) -> None:
        """创建尚未运行的实例。"""
        self.pending: Optional[asyncio.Future[bool]] = None
        self.closed = False

    async def synthesize(self, request: SpeechRequest, output: Path) -> float:
        """直到测试释放才写出完整结果。"""
        self.pending = asyncio.get_running_loop().create_future()
        if not await self.pending:
            raise RuntimeError("测试推理失败")
        output.write_bytes(b"RIFF" + b"0" * 100)
        return 1000.0

    def finish(self, success: bool = True) -> None:
        """提交一次底层完成。"""
        if self.pending is not None and not self.pending.done():
            self.pending.set_result(success)

    async def close(self) -> None:
        """使测试不会留下永远等待的执行任务。"""
        self.closed = True
        self.finish(False)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    """覆盖预算、优先级、取消、失败与结果续期。"""

    async def asyncSetUp(self) -> None:
        """每个场景使用独立目录和逻辑时钟。"""
        self.directory = tempfile.TemporaryDirectory()
        self.workers: List[FakeWorker] = []
        self.now = 0.0
        self.scheduler = Scheduler(SchedulerConfig(2, 2, self.directory.name, 1000), self.factory, lambda: self.now)
        self.voice = VoiceProfile("gpt", "sovits", "reference", "参考", "ja")

    def factory(self, voice: VoiceProfile) -> FakeWorker:
        """登记新实例，测试可分别释放它们。"""
        worker = FakeWorker()
        self.workers.append(worker)
        return worker

    def request(self, name: str, priority: int = 1) -> SpeechRequest:
        """生成独立声音配置的最小任务。"""
        return SpeechRequest("こんにちは", "ja", replace(self.voice, gpt_weights_path=name), priority)

    async def until(self, predicate: Callable[[], bool]) -> None:
        """给调度循环运行机会，失败时使用有界等待。"""
        for _ in range(100):
            if predicate():
                return
            await asyncio.sleep(0.002)
        self.fail("调度器未达到预期状态")

    async def asyncTearDown(self) -> None:
        """关闭真实调度循环并回收目录。"""
        await self.scheduler.close()
        self.directory.cleanup()

    async def test_budgets_and_exclusive_instances(self) -> None:
        """同模型并发也需要独占实例，并且不会超出常驻上限。"""
        ids = [await self.scheduler.submit(self.request("same")) for _ in range(3)]
        await self.until(lambda: len(self.workers) == 2 and all(worker.pending for worker in self.workers))
        self.assertEqual((await self.scheduler.get_task(ids[2])).status, "queued")
        self.assertEqual(len(self.scheduler.residents), 2)
        self.workers[0].finish()
        await self.until(lambda: self.scheduler.records[ids[2]].active)
        self.assertEqual(len(self.workers), 2)

    async def test_cancel_keeps_physical_slot(self) -> None:
        """终态取消不会使底层未完成的任务释放执行槽。"""
        self.scheduler.config = replace(self.scheduler.config, max_concurrent_inferences=1)
        first = await self.scheduler.submit(self.request("first"))
        second = await self.scheduler.submit(self.request("second"))
        await self.until(lambda: bool(self.workers and self.workers[0].pending))
        await self.scheduler.cancel([first])
        self.assertEqual((await self.scheduler.get_task(first)).status, "cancelled")
        self.assertEqual((await self.scheduler.get_task(second)).status, "queued")
        self.assertTrue(self.scheduler.records[first].active)
        self.workers[0].finish()
        await self.until(lambda: self.scheduler.records[second].active)
        self.assertFalse((Path(self.directory.name) / f"{first}.wav").exists())

    async def test_priority_and_atomic_batch(self) -> None:
        """高优先级先运行，无效批量请求不产生局部修改。"""
        self.scheduler.config = replace(self.scheduler.config, max_concurrent_inferences=1)
        first = await self.scheduler.submit(self.request("first", 2))
        second = await self.scheduler.submit(self.request("second", 2))
        await self.scheduler.set_priority([second], 0)
        await self.until(lambda: self.scheduler.records[second].active)
        with self.assertRaises(RequestError):
            await self.scheduler.cancel([first, "unknown"])
        self.assertEqual((await self.scheduler.get_task(first)).status, "queued")
        self.assertEqual(self.scheduler.records[first].request.priority, 2)

    async def test_resident_eviction_does_not_drop_queue_head(self) -> None:
        """只有空闲实例可以换出，常驻预算小于执行预算时仍保持正确顺序。"""
        self.scheduler.config = replace(self.scheduler.config, max_resident_models=1)
        first = await self.scheduler.submit(self.request("first"))
        second = await self.scheduler.submit(self.request("second"))
        await self.until(lambda: bool(self.workers and self.workers[0].pending))
        self.assertEqual((await self.scheduler.get_task(second)).status, "queued")
        self.workers[0].finish()
        await self.until(lambda: self.scheduler.records[second].active)
        self.assertEqual((await self.scheduler.get_task(first)).status, "succeeded")
        self.assertTrue(self.workers[0].closed)
        self.assertEqual(len(self.scheduler.residents), 1)

    async def test_failure_isolated_and_queue_continues(self) -> None:
        """失败实例被淘汰，同队列后续任务仍能运行。"""
        self.scheduler.config = replace(self.scheduler.config, max_resident_models=1)
        first = await self.scheduler.submit(self.request("first"))
        second = await self.scheduler.submit(self.request("second"))
        await self.until(lambda: bool(self.workers and self.workers[0].pending))
        self.workers[0].finish(False)
        await self.until(lambda: self.scheduler.records[second].active)
        self.assertEqual((await self.scheduler.get_task(first)).status, "failed")

    async def test_result_read_renews_retention(self) -> None:
        """重复查询续期，过期后删除文件并明确报告任务不可用。"""
        task = await self.scheduler.submit(self.request("first"))
        await self.until(lambda: bool(self.workers and self.workers[0].pending))
        self.workers[0].finish()
        await self.until(lambda: self.scheduler.records[task].snapshot.status == "succeeded")
        self.now = 0.8
        self.assertEqual((await self.scheduler.get_task(task)).status, "succeeded")
        self.now = 1.6
        self.assertEqual((await self.scheduler.get_task(task)).status, "succeeded")
        self.now = 3.0
        with self.assertRaises(RequestError):
            await self.scheduler.get_task(task)
        self.assertFalse((Path(self.directory.name) / f"{task}.wav").exists())

    async def test_reference_change_reuses_idle_weights(self) -> None:
        """相同权重的新参考音频不要求重新创建常驻模型。"""
        request = self.request("same")
        first = await self.scheduler.submit(request)
        await self.until(lambda: bool(self.workers and self.workers[0].pending))
        self.workers[0].finish()
        await self.until(lambda: not self.scheduler.records[first].active)
        second = await self.scheduler.submit(replace(request, voice=replace(request.voice, reference_audio_path="another")))
        await self.until(lambda: self.scheduler.records[second].active)
        self.assertEqual(len(self.workers), 1)
