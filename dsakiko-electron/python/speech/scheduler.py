"""单一权威队列：优先级、执行预算、独占模型和结果保留。"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from .contracts import CancelledTask, FailedTask, PendingTask, SchedulerConfig, SpeechProblem, SpeechRequest, SucceededTask, TaskSnapshot, VoiceProfile


class RequestError(Exception):
    """携带可序列化问题的请求错误。"""

    def __init__(self, code: str, message: str) -> None:
        """固定问题类别，避免把内部异常交付客户端。"""
        super().__init__(message)
        self.code = code


class Worker(Protocol):
    """一个独占声音配置的推理实例；实现不能阻塞调度事件循环。"""

    async def synthesize(self, request: SpeechRequest, output: Path) -> float:
        """发布完整音频并返回实际毫秒时长。"""
        ...

    async def close(self) -> None:
        """等待实例退出并释放资源。"""
        ...


@dataclass
class TaskRecord:
    """终态与实际执行生命周期分开保存，取消不会提前释放执行槽。"""

    request: SpeechRequest
    order: int
    snapshot: TaskSnapshot
    touched: float
    active: bool = False


@dataclass
class Resident:
    """已创建或正在加载的独占推理实例。"""

    voice: VoiceProfile
    worker: Worker
    busy: bool = False
    last_used: float = 0.0


class Scheduler:
    """拥有全部调度状态；公开异步操作在同一事件循环内原子提交。"""

    def __init__(self, config: SchedulerConfig, factory: Callable[[VoiceProfile], Worker], clock: Callable[[], float] = time.monotonic) -> None:
        """装配资源预算与实例工厂，不在构造时加载模型。"""
        for value in (config.max_concurrent_inferences, config.max_resident_models, config.result_retention_ms):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("资源预算必须为正整数")
        if config.max_same_model_overtakes != 0:
            raise ValueError("原型调度使用严格 FIFO，max_same_model_overtakes 必须为 0")
        self.config = config
        self.factory = factory
        self.clock = clock
        self.records: Dict[str, TaskRecord] = {}
        self.residents: List[Resident] = []
        self.executions: List[asyncio.Task[None]] = []
        self.serial = 0
        self.run_id = uuid.uuid4().hex
        self.wakeup = asyncio.Event()
        self.closed = False
        self.dispatcher: Optional[asyncio.Task[None]] = None
        Path(config.output_directory).mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        """启动唯一调度循环。"""
        if self.dispatcher is None:
            self.dispatcher = asyncio.create_task(self._dispatch())

    async def submit(self, request: SpeechRequest) -> str:
        """固定任务材料并登记新身份，不等待推理。"""
        if self.closed:
            raise RequestError("service_unavailable", "语音服务已关闭")
        if not request.text.strip() or isinstance(request.priority, bool) or not isinstance(request.priority, int) or request.priority < 0:
            raise RequestError("invalid_request", "文本或优先级无效")
        self.serial += 1
        task_id = f"{self.run_id}-{self.serial}"
        self.records[task_id] = TaskRecord(request, self.serial, PendingTask(task_id, "queued"), self.clock())
        self.start()
        self.wakeup.set()
        return task_id

    def _known(self, task_ids: Sequence[str]) -> List[TaskRecord]:
        """先验证完整集合，避免批量控制只修改一半。"""
        self._expire()
        ids = list(dict.fromkeys(task_ids))
        if any(task_id not in self.records for task_id in ids):
            raise RequestError("task_unavailable", "任务未知、过期或来自旧语音进程")
        return [self.records[task_id] for task_id in ids]

    async def get_task(self, task_id: str) -> TaskSnapshot:
        """查询不会消费结果，终态查询续期文件交接时间。"""
        record = self._known([task_id])[0]
        record.touched = self.clock()
        return record.snapshot

    async def set_priority(self, task_ids: Sequence[str], priority: int) -> None:
        """原子修改排队优先级，保留原受理序号。"""
        if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
            raise RequestError("invalid_request", "优先级无效")
        records = self._known(task_ids)
        from dataclasses import replace
        for record in records:
            if record.snapshot.status == "queued":
                record.request = replace(record.request, priority=priority)
        self.wakeup.set()

    async def cancel(self, task_ids: Sequence[str]) -> None:
        """取消提交终态；底层推理继续占用实例和执行槽直到退出。"""
        for record in self._known(task_ids):
            if record.snapshot.status in ("queued", "running"):
                record.snapshot = CancelledTask(record.snapshot.task_id)
                record.touched = self.clock()
        self.wakeup.set()

    def _expire(self) -> None:
        """只回收本运行目录内且实际执行已经结束的过期终态。"""
        for task_id, record in list(self.records.items()):
            if record.active or record.snapshot.status in ("queued", "running"):
                continue
            if (self.clock() - record.touched) * 1000 <= self.config.result_retention_ms:
                continue
            if isinstance(record.snapshot, SucceededTask):
                Path(record.snapshot.audio_path).unlink(missing_ok=True)
            del self.records[task_id]

    async def _dispatch(self) -> None:
        """按优先级领取任务；没有合适资源时等待，不绕过队首。"""
        while not self.closed:
            self.wakeup.clear()
            self._expire()
            while not self.closed:
                active = sum(resident.busy for resident in self.residents)
                queued = sorted((record for record in self.records.values() if record.snapshot.status == "queued"), key=lambda record: (record.request.priority, record.order))
                if active >= self.config.max_concurrent_inferences or not queued:
                    break
                record = queued[0]
                resident = next((item for item in self.residents if not item.busy and self._same_model(item.voice, record.request.voice)), None)
                if resident is None:
                    if len(self.residents) >= self.config.max_resident_models:
                        idle = sorted((item for item in self.residents if not item.busy), key=lambda item: item.last_used)
                        if not idle:
                            break
                        victim = idle[0]
                        await victim.worker.close()
                        self.residents.remove(victim)
                        # 关闭期间仍可接受取消与更高优先级请求，重新选取队首。
                        continue
                    try:
                        resident = Resident(record.request.voice, self.factory(record.request.voice))
                    except Exception:
                        record.snapshot = FailedTask(record.snapshot.task_id, SpeechProblem("worker_failed", "无法创建语音推理进程", True))
                        record.touched = self.clock()
                        continue
                    self.residents.append(resident)
                resident.busy = True
                record.active = True
                record.snapshot = PendingTask(record.snapshot.task_id, "running")
                self.executions = [task for task in self.executions if not task.done()]
                self.executions.append(asyncio.create_task(self._execute(record, resident)))
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _same_model(left: VoiceProfile, right: VoiceProfile) -> bool:
        """参考音频属于单次输入；两份权重相同即可复用空闲实例。"""
        return left.gpt_weights_path == right.gpt_weights_path and left.sovits_weights_path == right.sovits_weights_path

    async def _execute(self, record: TaskRecord, resident: Resident) -> None:
        """先完成文件，再提交唯一终态；已取消任务的迟到输出被删除。"""
        task_id = record.snapshot.task_id
        output = Path(self.config.output_directory) / f"{task_id}.wav"
        failed = False
        try:
            duration = await resident.worker.synthesize(record.request, output)
            if duration <= 0 or not output.is_file():
                raise ValueError("推理未产生完整音频")
            if record.snapshot.status == "running":
                record.snapshot = SucceededTask(task_id, str(output), duration)
            else:
                output.unlink(missing_ok=True)
        except Exception:
            failed = True
            output.unlink(missing_ok=True)
            if record.snapshot.status == "running":
                record.snapshot = FailedTask(task_id, SpeechProblem("synthesis_failed", "语音推理失败，请检查模型、依赖与设备；详细原因见语音日志", True))
        finally:
            if failed:
                await resident.worker.close()
                if resident in self.residents:
                    self.residents.remove(resident)
            record.active = False
            record.touched = self.clock()
            resident.busy = False
            resident.last_used = self.clock()
            self.wakeup.set()

    async def close(self) -> None:
        """应用退出时终止所有实例，不保留续跑任务。"""
        self.closed = True
        self.wakeup.set()
        for record in self.records.values():
            if record.snapshot.status in ("queued", "running"):
                record.snapshot = CancelledTask(record.snapshot.task_id)
        await asyncio.gather(*(resident.worker.close() for resident in list(self.residents)), return_exceptions=True)
        if self.dispatcher is not None:
            await self.dispatcher
        await asyncio.gather(*self.executions, return_exceptions=True)
