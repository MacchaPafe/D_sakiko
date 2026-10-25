"""可选的真实 GPT-SoVITS 冒烟测试；模型来源由本机测试清单指定。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from speech.contracts import SpeechRequest, VoiceProfile
from speech.gpt_sovits_resources import GptSovitsResources
from speech.worker import ProcessWorker


@unittest.skipUnless(os.environ.get("DSAKIKO_TEST_SPEECH_CONFIG"), "未指定真实模型测试清单")
class RealInferenceTests(unittest.IsolatedAsyncioTestCase):
    """通过真实 worker 合成音频，全部输入改名并分散存放。"""

    async def test_renamed_scattered_resources_generate_audio(self) -> None:
        """测试夹具使用硬链接避免复制大模型，不属于产品导入功能。"""
        config_path = Path(os.environ["DSAKIKO_TEST_SPEECH_CONFIG"])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="dsakiko-path-smoke-") as temporary:
            root = Path(temporary)
            counter = 0

            def scatter(value: object) -> object:
                """将清单中的每个文件独立改名，禁止依赖相邻文件和扩展名。"""
                nonlocal counter
                if isinstance(value, dict):
                    return {key: scatter(item) for key, item in value.items()}
                if not isinstance(value, str):
                    raise TypeError("测试清单只能包含文件路径")
                counter += 1
                target = root / f"位置 {counter}" / "任意名称"
                target.parent.mkdir()
                os.link(value, target)
                return str(target)

            startup = config["gptSovits"]
            resources = GptSovitsResources.parse(scatter(startup["resources"]), startup["architecture"])
            voice_data = config["voice"]
            voice = VoiceProfile(
                str(scatter(voice_data["gpt_weights_path"])),
                str(scatter(voice_data["sovits_weights_path"])),
                str(scatter(voice_data["reference_audio_path"])),
                voice_data["reference_text"], voice_data["reference_language"],
            )
            worker = ProcessWorker(voice, resources, "cpu", str(root / "runtime"), "float32", startup["architecture"])
            try:
                for index, (text, language) in enumerate((("你好。", "zh"), ("こんにちは。", "ja"))):
                    output = root / f"output-{index}.wav"
                    request = SpeechRequest(text, language, voice, 0)
                    duration = await asyncio.wait_for(worker.synthesize(request, output), timeout=180)
                    self.assertGreater(duration, 0)
                    with wave.open(str(output), "rb") as audio:
                        self.assertGreater(audio.getnframes(), 0)
                        self.assertEqual(audio.getnchannels(), 1)
                        self.assertEqual(audio.getframerate(), 48000 if startup["architecture"] == "v4" else 32000)
                        self.assertTrue(any(audio.readframes(audio.getnframes())), "不能把错误回退的静音当作合成成功")
                    self.assertFalse(Path(str(output) + ".tmp.wav").exists())
            finally:
                await worker.close()


if __name__ == "__main__":
    unittest.main()
