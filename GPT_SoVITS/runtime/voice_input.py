"""单一录音/识别会话；识别绑定录音开始时的对话和草稿位置。"""

import threading
from PyQt5.QtCore import QObject, pyqtSignal


class VoiceInputService(QObject):
    stateChanged = pyqtSignal(str)
    error = pyqtSignal(str)
    recognized = pyqtSignal(str)
    _result = pyqtSignal(object)
    _loaded = pyqtSignal(object)

    def __init__(self, drafts, parent=None, *, model_loader=None, stream_factory=None):
        super().__init__(parent)
        self.drafts = drafts
        self.model = None
        self.loading_model = None
        self.state = "loading"
        self.stream = None
        self.frames = []
        self.target = None
        self.closed = False
        self.stream_factory = stream_factory
        self._result.connect(self._accept_result)
        self._loaded.connect(self._accept_model)
        self.model_loader = model_loader

    def _state(self, state):
        self.state = state
        self.stateChanged.emit(state)

    def load(self):
        self._state("loading")

        def run():
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

    def _asr_started(self, model):
        # 先发布进程句柄；模型尚未加载完时退出也可以终止它。
        self.loading_model = model
        if self.closed:
            model.close()

    def _accept_model(self, result):
        self.loading_model = None
        if self.closed:
            close = getattr(result, "close", None)
            if close:
                close()
            return
        if isinstance(result, Exception):
            self._state("unavailable")
            self.error.emit(f"语音模型加载失败：{result}")
        else:
            self.model = result
            self._state("idle")

    def toggle(self, chat_id, cursor):
        if self.state == "recording":
            self.finish()
        elif self.state == "idle":
            self.start(chat_id, cursor)
        elif self.state == "unavailable" and not self.closed:
            self.load()

    def start(self, chat_id, cursor):
        if self.closed or self.state != "idle":
            return
        self.target = (chat_id, self.drafts.get(chat_id).text_revision, cursor)
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

    def _audio(self, indata, frames, time, status):
        if self.state == "recording":
            self.frames.append(indata.copy())

    def _close_stream(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def finish(self):
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
        frames, self.frames = self.frames, []

        def run():
            try:
                import numpy as np

                if not frames:
                    raise ValueError("没有捕获到音频。")
                data = np.concatenate(frames).flatten().astype(np.float32)
                if len(data) < 1600:
                    raise ValueError("录音过短，请重试。")
                segments, _ = self.model.transcribe(data, beam_size=5, language="zh")
                from opencc import OpenCC

                text = OpenCC("tw2s.json").convert("".join(s.text for s in segments))
                self._result.emit((target, text, None))
            except Exception as exc:
                self._result.emit((target, "", str(exc)))

        threading.Thread(target=run, name="DesktopASR", daemon=True).start()

    def _accept_result(self, result):
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

    def close(self):
        self.closed = True
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
