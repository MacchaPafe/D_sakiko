"""语音调度的数据契约；仅定义输入、可观察结果和启动配置，不加载推理依赖。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Union


@dataclass(frozen=True)
class VoiceProfile:
    """一次任务固定的发音配置；三个路径均为 Python 可读取的本机绝对路径。"""

    gpt_weights_path: str
    sovits_weights_path: str
    reference_audio_path: str
    reference_text: str
    reference_language: str


@dataclass(frozen=True)
class SpeechRequest:
    """仅包含合成所需数据，不接收角色、对话、消息或演出编号。

    text 已完成需要的读音替换，language 是合成语言。
    priority 为非负整数，数字越小越优先，0 优先使用下一个可用执行槽。
    speed 必须为有限正数，sentence_pause_ms 必须为非负整数。
    两个可选值为 None 时，在受理时固定为合成配置的默认值。
    """

    text: str
    language: str
    voice: VoiceProfile
    priority: int
    speed: Optional[float] = None
    sentence_pause_ms: Optional[int] = None


@dataclass(frozen=True)
class SpeechProblem:
    """可交付给客户端的问题；不包含原始堆栈、模型实例或内部设备状态。"""

    code: str
    message: str
    retryable: bool


@dataclass(frozen=True)
class PendingTask:
    """排队或执行中的任务；running 包括占用执行槽后的模型准备与合成。"""

    task_id: str
    status: Literal["queued", "running"]


@dataclass(frozen=True)
class SucceededTask:
    """成功终态；完整音频已写入绝对路径，duration_ms 是实际时长而非文本估算。"""

    task_id: str
    audio_path: str
    duration_ms: float
    status: Literal["succeeded"] = field(default="succeeded", init=False)


@dataclass(frozen=True)
class FailedTask:
    """失败终态；模型加载或合成失败不改变其他任务，也不自动重新提交。"""

    task_id: str
    problem: SpeechProblem
    status: Literal["failed"] = field(default="failed", init=False)


@dataclass(frozen=True)
class CancelledTask:
    """取消终态；不表示底层推理已经退出或资源已经释放。"""

    task_id: str
    status: Literal["cancelled"] = field(default="cancelled", init=False)


TaskSnapshot = Union[PendingTask, SucceededTask, FailedTask, CancelledTask]


@dataclass(frozen=True)
class SchedulerConfig:
    """Python 启动装配配置，不是每条任务的参数，也不是业务侧的控制接口。

    两个资源上限均为正整数；实际并发还取决于设备与模型是否支持安全并行。
    output_directory 是仅存放本次运行临时输出的绝对路径。
    result_retention_ms 为正整数；终态建立或查询后，在这段时间内保留记录与输出。
    max_same_model_overtakes 为非负整数，限制同优先级任务因模型复用被超越的次数。
    默认 0 严格遵守同级 FIFO；该配置始终不能放宽 0 级任务的 FIFO。
    配置在启动时固定；本轮不承诺通过业务接口热调整资源预算。
    """

    max_concurrent_inferences: int
    max_resident_models: int
    output_directory: str
    result_retention_ms: int
    max_same_model_overtakes: int = 0
