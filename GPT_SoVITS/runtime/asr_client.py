"""有界等待和退出的 ASR 子进程客户端；主窗口不加载识别原生库。"""

import base64
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace


class ASRProcessClient:
    def __init__(self, model_path, *, on_started=None):
        path = Path(model_path).resolve()
        if not path.is_dir():
            raise FileNotFoundError(f"语音识别模型不存在：{path}")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                str(Path(__file__).with_name("asr_worker.py")),
                str(path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.replies = queue.Queue()
        self.lock = threading.Lock()
        self.closed = False

        def read():
            try:
                for line in self.process.stdout:
                    if line.startswith("DS_ASR_JSON:"):
                        self.replies.put(json.loads(line[len("DS_ASR_JSON:") :]))
            except (OSError, ValueError):
                pass
            finally:
                self.replies.put({"error": "语音识别进程已退出。"})

        threading.Thread(target=read, name="ASRReplies", daemon=True).start()
        try:
            if on_started is not None:
                on_started(self)
            result = self._receive()
            if not result.get("ready"):
                raise RuntimeError("语音识别进程未就绪。")
        except Exception:
            self.close()
            raise

    def _receive(self):
        try:
            result = self.replies.get(timeout=90)
        except queue.Empty:
            self.close()
            raise RuntimeError("语音识别超时，请重试。")
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result

    def transcribe(self, audio, **kwargs):
        with self.lock:
            if self.closed or self.process.poll() is not None:
                raise RuntimeError("语音识别进程不可用。")
            data = audio.astype("<f4", copy=False).tobytes()
            self.process.stdin.write(
                json.dumps(
                    {
                        "type": "transcribe",
                        "audio": base64.b64encode(data).decode("ascii"),
                    }
                )
                + "\n"
            )
            self.process.stdin.flush()
            result = self._receive()
            return [SimpleNamespace(text=result.get("text", ""))], None

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
