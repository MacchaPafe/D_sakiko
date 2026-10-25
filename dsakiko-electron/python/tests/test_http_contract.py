"""验证正式 Node 接线使用的语音报文与可信资源边界。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from speech.server import request_from_json
from speech.scheduler import RequestError


class HttpContractTests(unittest.TestCase):
    """无需加载推理模型即可验证受理前的材料检查。"""

    def test_managed_assets_and_sampling(self) -> None:
        """允许宿主明确授权的数据目录，并固定采样参数。"""
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "resource"
            assets = parent / "assets"
            root.mkdir()
            assets.mkdir()
            resource = assets / "reference.wav"
            resource.write_bytes(b"audio")
            body: Dict[str, object] = {
                "text": "你好", "language": "zh", "priority": 1,
                "voice": {"gpt_weights_path": str(resource), "sovits_weights_path": str(resource),
                          "reference_audio_path": str(resource), "reference_text": "你好", "reference_language": "zh"},
                "sampling": {"top_k": 15, "top_p": 0.8, "temperature": 0.9, "seed": -1, "sample_steps": 16},
            }
            request = request_from_json(body, assets)
            self.assertIsNotNone(request.sampling)
            assert request.sampling is not None
            self.assertEqual(request.sampling.top_p, 0.8)
            with self.assertRaises(RequestError):
                request_from_json(body, root)
            body["sampling"] = {"top_k": True, "top_p": 1, "temperature": 1, "seed": -1, "sample_steps": 16}
            with self.assertRaises(RequestError):
                request_from_json(body, assets)

    def test_symlink_cannot_escape_authorized_root(self) -> None:
        """解析符号链接后仍必须处于受控目录。"""
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "resource"
            root.mkdir()
            outside = parent / "private.wav"
            outside.write_bytes(b"private")
            link = root / "link.wav"
            link.symlink_to(outside)
            body: Dict[str, object] = {"text": "test", "language": "en", "priority": 0,
                "voice": {"gpt_weights_path": str(link), "sovits_weights_path": str(link),
                          "reference_audio_path": str(link), "reference_text": "test", "reference_language": "en"}}
            with self.assertRaises(RequestError):
                request_from_json(body, root)
