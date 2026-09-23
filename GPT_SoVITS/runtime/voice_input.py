"""单一录音/识别会话；识别绑定录音开始时的对话和草稿位置。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal

from runtime.drafts import DraftStore

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray
    from sounddevice import InputStream
    from runtime.asr_client import ASRProcessClient

RecognitionTarget = tuple[str, int, int]
RecognitionResult = tuple[RecognitionTarget, str, str | None]

# 语音输入独立计时，不随文字对话、语音合成或桌宠形态切换重置。
ASR_IDLE_TIMEOUT_MS = 120_000


class VoiceInputService(QObject):
    """共享录音会话，并在两分钟空闲后回收识别进程。"""

    stateChanged = pyqtSignal(str)
    error = pyqtSignal(str)
    recognized = pyqtSignal(str)
    _result = pyqtSignal(object)
    _loaded = pyqtSignal(object)
    _released = pyqtSignal(object)

    def __init__(
        self,
        drafts: DraftStore,
        parent: QObject | None = None,
        *,
        model_loader: Callable[[], ASRProcessClient] | None = None,
        stream_factory: Callable[..., InputStream] | None = None,
    ) -> None:
        """建立独立空闲计时器，保留可注入的模型和录音设备。"""
        super().__init__(parent)
        self.drafts = drafts
        self.model: ASRProcessClient | None = None
        self.loading_model: ASRProcessClient | None = None
        self.state = "dormant"
        self.stream: InputStream | None = None
        self.frames: list[NDArray[np.float32]] = []
        self.target: RecognitionTarget | None = None
        self._pending_target: RecognitionTarget | None = None
        self._loading = False
        self._releasing = False
        self.closed = False
        self.stream_factory = stream_factory
        self._result.connect(self._accept_result)
        self._loaded.connect(self._accept_model)
        self._released.connect(self._accept_release)
        self.model_loader = model_loader
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.setTimerType(Qt.PreciseTimer)
        self.idle_timer.setInterval(ASR_IDLE_TIMEOUT_MS)
        self.idle_timer.timeout.connect(self._release_idle_model)

    def _state(self, state: str) -> None:
        """仅在识别服务重新空闲时开始计时，工作期间停用计时器。"""
        self.idle_timer.stop()
        self.state = state
        if state == "idle" and not self.closed:
            self.idle_timer.start()
        self.stateChanged.emit(state)

    def load(self) -> None:
        """异步加载模型，并阻止重复加载或与旧进程回收重叠。"""
        if self.closed or self._loading or self._releasing or self.model is not None:
            return
        self._loading = True
        self._state("preparing" if self._pending_target else "loading")

        def run() -> None:
            """在后台初始化识别进程，退出期间立即回收迟到的模型。"""
            try:
                if self.model_loader is not None:
                    model = self.model_loader()
                else:
                    from runtime.asr_client import ASRProcessClient

                    model = ASRProcessClient(
                        "./pretrained_models/faster_whisper_small",
                        on_started=self._asr_started,
                    )
                if self.closed:
                    close = getattr(model, "close", None)
                    if close:
                        close()
                else:
                    self._loaded.emit(model)
            except Exception as exc:
                if not self.closed:
                    self._loaded.emit(exc)

        threading.Thread(target=run, name="DesktopASRLoader", daemon=True).start()

    def _asr_started(self, model: ASRProcessClient) -> None:
        """发布尚在加载的进程句柄，使退出可以中断模型初始化。"""
        # 先发布进程句柄；模型尚未加载完时退出也可以终止它。
        self.loading_model = model
        if self.closed:
            model.close()

    def _accept_model(self, result: ASRProcessClient | Exception) -> None:
        """接收加载结果，并继续用户已发起且未取消的录音。"""
        self._loading = False
        self.loading_model = None
        if self.closed:
            close = getattr(result, "close", None)
            if close:
                close()
            return
        if isinstance(result, Exception):
            self._pending_target = None
            self._state("unavailable")
            self.error.emit(f"语音模型加载失败：{result}")
        else:
            self.model = result
            target, self._pending_target = self._pending_target, None
            self._state("idle")
            if target is not None:
                self._start_target(target)

    def _release_idle_model(self) -> None:
        """只回收空闲模型，进程等待与资源关闭均放在后台线程。"""
        if self.closed or self.state != "idle" or self.model is None:
            return
        model, self.model = self.model, None
        self._releasing = True
        self._state("unloading")

        def run() -> None:
            """关闭旧进程后通知主线程，保证重新加载不会与释放重叠。"""
            error: Exception | None = None
            try:
                model.close()
            except Exception as exc:
                error = exc
            if not self.closed:
                self._released.emit(error)

        threading.Thread(target=run, name="DesktopASRRelease", daemon=True).start()

    def _accept_release(self, error: Exception | None) -> None:
        """回收结束后进入正常待机，或继续回收期间收到的录音请求。"""
        self._releasing = False
        if self.closed:
            return
        if error is not None:
            self.error.emit(f"释放语音输入模型失败：{error}")
        self._state("dormant")
        if self._pending_target is not None:
            self.load()

    def toggle(self, chat_id: str, cursor: int) -> None:
        """一次点击即可唤醒并录音，再次点击可取消尚未开始的录音。"""
        if self.closed:
            return
        if self.state == "recording":
            self.finish()
        elif self.state == "idle":
            self.start(chat_id, cursor)
        elif self.state == "preparing":
            self._pending_target = None
            self._state("unloading" if self._releasing else "loading")
        elif self.state in {"dormant", "unavailable", "unloading"}:
            self._pending_target = (
                chat_id,
                self.drafts.get(chat_id).text_revision,
                cursor,
            )
            if self._releasing:
                self._state("preparing")
            self.load()

    def start(self, chat_id: str, cursor: int) -> None:
        """保存点击时的对话与草稿位置，再启动录音。"""
        self._start_target((chat_id, self.drafts.get(chat_id).text_revision, cursor))

    def _start_target(self, target: RecognitionTarget) -> None:
        """以既定草稿归属启动设备，加载期间切换对话不会改变目标。"""
        if self.closed or self.state != "idle":
            return
        self.target = target
        self.frames = []
        try:
            import sounddevice as sd

            factory = self.stream_factory or sd.InputStream
            self.stream = factory(
                samplerate=16000, channels=1, dtype="float32", callback=self._audio
            )
            self._state("recording")
            self.stream.start()
        except Exception as exc:
            try:
                self._close_stream()
            except Exception:
                pass
            self._state("idle")
            self.error.emit(f"无法开始录音：{exc}")

    def _audio(
        self, indata: NDArray[np.float32], frames: int, time: object, status: object
    ) -> None:
        """保存录音设备送来的独立音频帧。"""
        if self.state == "recording":
            self.frames.append(indata.copy())

    def _close_stream(self) -> None:
        """即使停止录音失败，也关闭设备句柄。"""
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def finish(self) -> None:
        """结束录音并后台识别，识别期间禁止空闲回收。"""
        if self.state != "recording":
            return
        self._state("transcribing")
        try:
            self._close_stream()
        except Exception as error:
            self._state("idle")
            self.error.emit(f"结束录音失败：{error}")
            return
        target = self.target
        model = self.model
        frames, self.frames = self.frames, []

        def run() -> None:
            """将音频识别结果送回原始草稿，失败同样结束本次会话。"""
            try:
                import numpy as np

                if not frames:
                    raise ValueError("没有捕获到音频。")
                data = np.concatenate(frames).flatten().astype(np.float32)
                if len(data) < 1600:
                    raise ValueError("录音过短，请重试。")
                if model is None:
                    raise RuntimeError("语音识别模型不可用。")
                segments, _ = model.transcribe(data, beam_size=5, language="zh")
                from opencc import OpenCC

                text = OpenCC("tw2s.json").convert("".join(s.text for s in segments))
                self._result.emit((target, text, None))
            except Exception as exc:
                self._result.emit((target, "", str(exc)))

        threading.Thread(target=run, name="DesktopASR", daemon=True).start()

    def _accept_result(self, result: RecognitionResult) -> None:
        """写回识别内容，完成或可重试失败后重新计算两分钟空闲期。"""
        if self.closed:
            return
        target, text, error = result
        if error:
            self.error.emit(f"语音识别失败：{error}")
            process = getattr(self.model, "process", None)
            if getattr(self.model, "closed", False) or (
                process is not None and process.poll() is not None
            ):
                self.model.close()
                self.model = None
                self._state("unavailable")
                return
        elif text:
            self.drafts.insert_recognition(*target, text)
            self.recognized.emit(target[0])
        self._state("idle")

    def close(self) -> None:
        """永久关闭服务并取消待开始录音，迟到结果不再更新界面。"""
        if self.closed:
            return
        self.closed = True
        self.idle_timer.stop()
        self._pending_target = None
        try:
            self._close_stream()
        except Exception as error:
            self.error.emit(f"释放录音设备失败：{error}")
        finally:
            self.frames.clear()
            close = getattr(self.model, "close", None)
            if close:
                close()
            close_loading = getattr(self.loading_model, "close", None)
            if close_loading:
                close_loading()
            self._state("closed")
