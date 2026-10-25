"""验证语音进程的资源位置与启动参数。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from speech.contracts import VoiceProfile
from speech.scheduler import RequestError
from speech.server import request_from_json
from speech.worker import ProcessWorker, worker_main


class ResourcePathsTest(unittest.TestCase):
    """模型和参考音频只能从已授权的资源库读取。"""

    def test_library_paths_and_cache_rejection(self) -> None:
        """接受资源库中的权重与音频，拒绝缓存中的同名文件。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "library"
            models = library / "models"
            audio = library / "audio"
            cache = root / "cache"
            for directory in (models, audio, cache):
                directory.mkdir(parents=True, exist_ok=True)
            for file in (models / "gpt", models / "sovits", audio / "reference", cache / "reference"):
                file.write_bytes(b"test")
            voice: Dict[str, object] = {
                "gpt_weights_path": str(models / "gpt"),
                "sovits_weights_path": str(models / "sovits"),
                "reference_audio_path": str(audio / "reference"),
                "reference_text": "参考",
                "reference_language": "zh",
            }
            body: Dict[str, object] = {"text": "你好", "language": "zh", "priority": 1, "voice": voice}
            request = request_from_json(body, library)
            self.assertEqual(Path(request.voice.reference_audio_path), (audio / "reference").resolve())
            voice["reference_audio_path"] = str(cache / "reference")
            with self.assertRaises(RequestError):
                request_from_json(body, library)

    def test_worker_startup_arguments(self) -> None:
        """将资源路径、临时目录、精度和模型结构传入独立进程。"""
        voice = VoiceProfile("/library/gpt", "/library/sovits", "/library/reference", "参考", "zh")
        resources = MagicMock()
        with tempfile.TemporaryDirectory() as temporary, patch("speech.worker.multiprocessing.get_context") as get_context:
            directory = str(Path(temporary) / "worker")
            context = get_context.return_value
            parent = MagicMock()
            child = MagicMock()
            context.Pipe.return_value = (parent, child)
            ProcessWorker(voice, resources, "cpu", directory, "float32", "v2")
            context.Process.assert_called_once_with(
                target=worker_main,
                args=(child, voice, resources, "cpu", directory, "float32", "v2"),
                daemon=True,
            )
            context.Process.return_value.start.assert_called_once()
            child.close.assert_called_once()

    def test_worker_start_failure_cleans_only_new_directory(self) -> None:
        """spawn 失败回收新目录和管道，已有目录必须原样保留。"""
        voice = VoiceProfile("g", "s", "r", "参考", "zh")
        with tempfile.TemporaryDirectory() as temporary, patch("speech.worker.multiprocessing.get_context") as get_context:
            directory = Path(temporary) / "worker"
            context = get_context.return_value
            parent, child = MagicMock(), MagicMock()
            context.Pipe.return_value = (parent, child)
            context.Process.return_value.start.side_effect = OSError("spawn")
            with self.assertRaisesRegex(OSError, "spawn"):
                ProcessWorker(voice, MagicMock(), "cpu", str(directory))
            self.assertFalse(directory.exists())
            parent.close.assert_called_once()
            child.close.assert_called_once()
            directory.mkdir()
            (directory / "kept").write_bytes(b"owned elsewhere")
            with self.assertRaises(FileExistsError):
                ProcessWorker(voice, MagicMock(), "cpu", str(directory))
            self.assertEqual((directory / "kept").read_bytes(), b"owned elsewhere")
