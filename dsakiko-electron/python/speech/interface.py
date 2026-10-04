"""Python 语音调度接口原型；没有 HTTP、任务队列或 GPT-SoVITS 的具体实现。"""

from __future__ import annotations

from typing import Protocol, Sequence

from .contracts import SpeechRequest, TaskSnapshot


class SpeechScheduler(Protocol):
    """唯一持有合成队列、执行槽、模型复用和结果记录的调度模块。

    HTTP 层只负责校验报文、调用这四个方法并序列化结果，不另行调度。
    编号关联、优先级来源和合成失败后的演出策略属于调用方。
    接口不暴露模型池、队列位置、GPU 状态或可修改的内部对象。
    异步方法用于短暂的受理与查询；不能在事件循环内同步执行模型加载或推理。
    """

    async def submit(self, request: SpeechRequest) -> str:
        """校验并登记完整请求，返回本次运行唯一的 task_id，不等待合成。

        输入在受理时固定，初始状态为 queued；生成编号不由客户端负责。
        无效输入在登记前拒绝；受理后的模型加载或合成故障记录为 failed。
        编号包含不可复用的运行身份，Python 重启后不能指向新任务。
        HTTP 响应丢失可能发生在受理之后；客户端不得盲目自动重交。
        """
        ...

    async def get_task(self, task_id: str) -> TaskSnapshot:
        """返回只读快照；成功时一并返回音频绝对路径和实际时长。

        不等待排队或推理结束；查询终态不消费结果，多个等待者可读取相同终态。
        有效终态查询延长记录及临时输出的保留期，便于客户端完成文件导入。
        过期或未知编号报 task_unavailable，不返回虚假的 queued 状态。
        状态始终来自调度模块，不能通过探测文件是否出现来推断合成已经完成。
        """
        ...

    async def set_priority(self, task_ids: Sequence[str], priority: int) -> None:
        """原子调整指定 queued 任务的优先级，原始提交序号保持不变。

        priority 为非负整数；running 和终态任务不受影响。
        先校验全部编号，再一次性应用，期间不能穿插任务领取；未知或过期编号整批拒绝。
        空集合无副作用，重复编号按一个处理；修改不隐式作用于后续提交的任务。
        无法由任务编号推测对话归属，也不接收调整优先级的业务原因。
        """
        ...

    async def cancel(self, task_ids: Sequence[str]) -> None:
        """原子取消指定未完成任务；已进入终态的任务保持原结果。

        先校验全部编号，未知或过期编号整批拒绝；空集合无副作用，重复编号去重。
        取消与完成在同一状态提交机制中确定先后，先写入的终态不能被覆盖。
        queued 任务移出候选队列；running 任务尽力中断，迟到输出不对外发布。
        返回只表示取消已生效；推理实际退出前，执行槽和模型仍计入资源占用。
        """
        ...
