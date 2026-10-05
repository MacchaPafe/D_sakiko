"""每个进程独占一套推理实例，避免共享权重和参考缓存串话。"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import sys
import traceback
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Dict, Tuple, cast

from .contracts import SpeechRequest, VoiceProfile


def worker_main(pipe: Connection, voice: VoiceProfile, pretrained: str, device: str, directory: str) -> None:
    """在隔离进程加载复制的推理代码，仅从外部位置读取权重。"""
    try:
        vendor = Path(__file__).resolve().parents[1] / "vendor" / "gpt_sovits"
        runtime = Path(directory)
        runtime.mkdir(parents=True, exist_ok=True)
        os.chdir(runtime)
        sys.path.insert(0, str(vendor))
        os.environ["DSAKIKO_PRETRAINED_ROOT"] = pretrained
        os.environ["DSAKIKO_G2PW_ROOT"] = str(Path(pretrained).parent / "text" / "G2PWModel")
        os.environ["MPLCONFIGDIR"] = str(runtime / "matplotlib")
        os.environ["XDG_CACHE_HOME"] = str(runtime / "cache")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["DSAKIKO_TEXT_CACHE_DIR"] = str(runtime)
        os.environ["bert_path"] = str(Path(pretrained) / "chinese-roberta-wwm-ext-large")
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        import torch
        import soundfile
        import pyopenjtalk
        if not Path(os.fsdecode(pyopenjtalk.OPEN_JTALK_DICT_DIR)).is_dir():
            raise RuntimeError("Python 环境缺少 Open JTalk 字典，请先准备日语词典再启动语音")
        from TTS_infer_pack.TTS import TTS, TTS_Config
        from process_ckpt import get_sovits_version_from_path_fast

        torch.set_num_threads(2)
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("配置了 CUDA，但设备不可用")
        if device == "mps" and not torch.backends.mps.is_available():
            raise RuntimeError("配置了 MPS，但设备不可用")
        version = get_sovits_version_from_path_fast(voice.sovits_weights_path)[1]
        config = TTS_Config({"custom": {
            "device": device, "is_half": device == "cuda", "version": version,
            "t2s_weights_path": voice.gpt_weights_path, "vits_weights_path": voice.sovits_weights_path,
            "bert_base_path": str(Path(pretrained) / "chinese-roberta-wwm-ext-large"),
            "cnhuhbert_base_path": str(Path(pretrained) / "chinese-hubert-base"),
        }})
        pipeline = TTS(config)
        while True:
            item = pipe.recv()
            if item is None:
                break
            request, output = cast(Tuple[SpeechRequest, str], item)
            temporary = output + ".tmp.wav"
            inputs: Dict[str, object] = {
                "text": request.text, "text_lang": request.language,
                "ref_audio_path": request.voice.reference_audio_path,
                "prompt_text": request.voice.reference_text, "prompt_lang": request.voice.reference_language,
                "text_split_method": "cut0", "speed_factor": request.speed or 1.0,
                "fragment_interval": (request.sentence_pause_ms if request.sentence_pause_ms is not None else 300) / 1000,
                "top_k": 15, "top_p": 1.0, "temperature": 1.0, "seed": -1,
                "parallel_infer": False, "use_cuda_graph": False, "sample_steps": 16,
            }
            generator = pipeline.run(inputs)
            try:
                sample_rate, samples = next(generator)
                soundfile.write(temporary, samples, sample_rate, format="WAV")
                os.replace(temporary, output)
                pipe.send({"duration_ms": len(samples) * 1000 / sample_rate})
            finally:
                generator.close()
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        traceback.print_exc()
        try:
            pipe.send({"error": "推理进程失败"})
        except (OSError, BrokenPipeError):
            pass
    finally:
        pipe.close()


class ProcessWorker:
    """主进程通过轮询管道等待结果，加载模型期间控制接口仍可响应。"""

    def __init__(self, voice: VoiceProfile, pretrained: str, device: str, directory: str) -> None:
        """创建受预算约束的单模型进程。"""
        context = multiprocessing.get_context("spawn")
        self.pipe, child = context.Pipe()
        self.process = context.Process(target=worker_main, args=(child, voice, pretrained, device, directory), daemon=True)
        self.process.start()
        child.close()
        self.closing = False

    async def synthesize(self, request: SpeechRequest, output: Path) -> float:
        """等待完整结果；进程退出时结束等待。"""
        self.pipe.send((request, str(output)))
        while not self.closing:
            if self.pipe.poll():
                result = cast(Dict[str, object], self.pipe.recv())
                duration = result.get("duration_ms")
                if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                    return float(duration)
                raise RuntimeError("推理进程报告失败")
            if not self.process.is_alive():
                raise RuntimeError("推理进程意外退出")
            await asyncio.sleep(0.05)
        raise RuntimeError("推理进程已关闭")

    async def close(self) -> None:
        """强制结束只用于退出或空闲回收，普通任务取消不调用此方法。"""
        if self.closing:
            return
        self.closing = True
        if self.process.is_alive():
            self.process.terminate()
        await asyncio.to_thread(self.process.join, 5)
        if self.process.is_alive():
            self.process.kill()
            await asyncio.to_thread(self.process.join, 2)
        self.pipe.close()
