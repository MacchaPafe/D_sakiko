from __future__ import annotations

from dataclasses import dataclass
import multiprocessing
from typing import Protocol


class QueueWriter(Protocol):
    """描述 Live2D 客户端需要的队列写入接口。"""

    def put(self, item: object) -> None:
        """写入一个跨进程队列载荷。"""


class QueueSignal(QueueWriter, Protocol):
    """描述可读写的信号队列接口。"""

    def empty(self) -> bool:
        """返回队列是否为空。"""

    def get(self) -> object:
        """读取一个队列载荷。"""


class SharedBoolValue(Protocol):
    """描述 multiprocessing.Value 暴露的布尔 value 接口。"""

    value: bool


@dataclass(frozen=True)
class Live2DProcessHandle:
    """保存 Live2D 客户端和其跨进程窗口进程。"""

    client: Live2DClient
    process: multiprocessing.Process


@dataclass(frozen=True)
class Live2DClient:
    """把现有 Live2D 多队列协议包装成面向控制器的小接口。"""

    live2d_text_queue: QueueWriter
    audio_file_path_queue: QueueWriter
    emotion_queue: QueueWriter
    motion_complete_value: SharedBoolValue
    change_char_queue: QueueWriter | None = None
    thinking_queue: QueueSignal | None = None
    text_display_value: SharedBoolValue | None = None
    char_is_converted_queue: QueueWriter | None = None

    def play_segment(self, *, audio_path: str, emotion: str, display_text: str = "") -> bool:
        """在 Live2D 空闲时播放一段音频并可同步更新显示文本。"""
        if not self.is_playback_complete():
            return False
        if display_text:
            self.set_text(display_text)
        self.audio_file_path_queue.put(audio_path)
        self.emotion_queue.put(emotion)
        return True

    def is_playback_complete(self) -> bool:
        """读取 Live2D 共享动作完成状态。"""
        return bool(self.motion_complete_value.value)

    def set_text(self, text: str) -> None:
        """更新 Live2D 文本框显示内容。"""
        self.live2d_text_queue.put(text)

    def switch_model(self, character_name: str, model_json: str | None) -> bool:
        """发送 Live2D 模型切换命令。"""
        return self._send_change_command({
            "type": "switch_live2d",
            "character_name": character_name,
            "model_json": model_json or "",
        })

    def switch_fps(self, fps: int) -> bool:
        """发送 Live2D 渲染帧率切换命令。"""
        return self._send_change_command({
            "type": "switch_l2d_fps",
            "fps": fps,
        })

    def change_background(self) -> bool:
        """发送 Live2D 背景切换命令。"""
        return self._send_change_command({"type": "change_l2d_background"})

    def start_talking(self) -> bool:
        """发送录音开始时的说话动作命令。"""
        return self._send_change_command({"type": "start_talking"})

    def switch_sakiko_state(self, sakiko_state: bool) -> bool:
        """发送黑白祥模型切换状态。"""
        if self.char_is_converted_queue is None:
            return False
        self.char_is_converted_queue.put(sakiko_state)
        return True

    def toggle_sakiko_mask(self) -> bool:
        """发送黑祥面具切换命令。"""
        if self.char_is_converted_queue is None:
            return False
        self.char_is_converted_queue.put("maskoff")
        return True

    def toggle_text_display(self) -> bool:
        """切换 Live2D 文本显示状态。"""
        if self.text_display_value is None:
            return False
        self.text_display_value.value = not bool(self.text_display_value.value)
        return bool(self.text_display_value.value)

    def is_text_display_enabled(self) -> bool:
        """返回 Live2D 文本显示是否开启。"""
        if self.text_display_value is None:
            return False
        return bool(self.text_display_value.value)

    def cancel_turn_playback(self, *, chat_id: str, turn_id: str) -> bool:
        """发送取消当前对话轮次 Live2D 播放的命令。"""
        return self._send_change_command({
            "type": "cancel_turn",
            "chat_id": chat_id,
            "turn_id": turn_id,
        })

    def stop_playback(self) -> bool:
        """发送停止 Live2D 说话动作的命令。"""
        return self._send_change_command({"type": "stop_talking"})

    def start_thinking(self) -> bool:
        """向 Live2D 思考队列写入开始思考标记。"""
        if self.thinking_queue is None:
            return False
        if self.thinking_queue.empty():
            self.thinking_queue.put("no_complete")
        return True

    def stop_thinking(self) -> bool:
        """从 Live2D 思考队列移除一个思考标记。"""
        if self.thinking_queue is None:
            return False
        if not self.thinking_queue.empty():
            self.thinking_queue.get()
        return True

    def shutdown(self) -> None:
        """发送 Live2D 关闭流程使用的现有退出信号。"""
        if self.change_char_queue is not None:
            self.change_char_queue.put("exit")
        self.emotion_queue.put("bye")

    def _send_change_command(self, payload: dict[str, object]) -> bool:
        """向 Live2D 控制队列写入结构化命令。"""
        if self.change_char_queue is None:
            return False
        self.change_char_queue.put(payload)
        return True


def create_live2d_client_process(
    *,
    desktop_w: int,
    desktop_h: int,
    log_queue: object | None = None,
) -> Live2DProcessHandle:
    """创建 Live2D 跨进程通信边界并返回客户端和进程。"""
    import live2d_module

    emotion_queue = multiprocessing.Queue()
    audio_file_path_queue = multiprocessing.Queue()
    thinking_queue = multiprocessing.Queue()
    char_is_converted_queue = multiprocessing.Queue()
    change_char_queue = multiprocessing.Queue()
    live2d_text_queue = multiprocessing.Queue()
    is_display_text_value = multiprocessing.Value("b", True)
    motion_complete_value = multiprocessing.Value("b", True)

    process = multiprocessing.Process(
        target=live2d_module.run_live2d_process,
        args=(
            emotion_queue,
            audio_file_path_queue,
            thinking_queue,
            char_is_converted_queue,
            change_char_queue,
            live2d_text_queue,
            is_display_text_value,
            motion_complete_value,
            desktop_w,
            desktop_h,
            log_queue,
        ),
    )
    client = Live2DClient(
        live2d_text_queue=live2d_text_queue,
        audio_file_path_queue=audio_file_path_queue,
        emotion_queue=emotion_queue,
        motion_complete_value=motion_complete_value,
        change_char_queue=change_char_queue,
        thinking_queue=thinking_queue,
        text_display_value=is_display_text_value,
        char_is_converted_queue=char_is_converted_queue,
    )
    return Live2DProcessHandle(client=client, process=process)
