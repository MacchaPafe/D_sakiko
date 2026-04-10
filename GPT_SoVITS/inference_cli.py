from __future__ import annotations

import contextlib
import os
import queue
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, cast, Optional

import soundfile as sf
import torch

os.chdir(os.path.dirname(__file__))

from character import CharacterAttributes
from qconfig import d_sakiko_config

if TYPE_CHECKING:
    import numpy as np
    from TTS_infer_pack.TTS import TTS, TTS_Config

gsv_sam_rate = d_sakiko_config.sovits_inference_sampling_steps.value
SILENCE_WAV_PATH = '../reference_audio/silent_audio/silence.wav'
DEFAULT_DEVICE_POLICY = 'cpu'

PayloadDict = dict[str, object]
CommandDict = dict[str, object]
ResultDict = dict[str, object]


class CharacterVoiceRuntime:
    """保存 worker 侧单个角色的运行时元数据。"""

    def __init__(self, character: CharacterAttributes, device_policy: str = DEFAULT_DEVICE_POLICY) -> None:
        self.character: CharacterAttributes = character
        self.device_policy: str = device_policy
        self.last_used_at: float | None = None
        self.last_error: str | None = None

    def update_character(self, character: CharacterAttributes) -> None:
        """使用最新的角色信息刷新当前运行时对象。"""
        self.character = character

    def apply_payload(self, payload: PayloadDict) -> None:
        """用命令 payload 中的语音字段更新角色信息。"""
        self.character.GPT_model_path = cast(str, payload["gpt_model_path"])
        self.character.sovits_model_path = cast(
            str,
            payload["sovits_model_path"],
        )
        self.character.gptsovits_ref_audio = cast(
            str,
            payload["ref_audio_path"],
        )
        self.character.gptsovits_ref_audio_text = cast(
            str,
            payload["ref_text_path"],
        )
        self.character.gptsovits_ref_audio_lan = cast(
            str,
            payload["ref_language"],
        )

    def touch(self) -> None:
        """更新最近一次使用时间。"""
        self.last_used_at = time.time()

    def load_now(self, tts_pool: TTSPool) -> None:
        """显式加载当前角色模型。"""
        tts_pool.ensure_loaded(self)

    def unload_now(self, tts_pool: TTSPool) -> None:
        """显式卸载当前角色模型。"""
        tts_pool.unload(self.character.character_name)

    def set_device_policy(self, device: str, tts_pool: TTSPool | None = None, apply_now: bool = True) -> None:
        """更新当前角色的目标设备策略。"""
        self.device_policy = device
        if tts_pool is not None and apply_now:
            tts_pool.set_device(self, device)

    def synthesize(
        self,
        payload: PayloadDict,
        tts_pool: TTSPool,
        dict_language_v2: dict[str, str],
        i18n_translator,
    ) -> tuple[int, np.ndarray]:
        """执行一次语音合成并返回采样率与音频数据。"""
        self.apply_payload(payload)
        tts = tts_pool.get_tts(self)

        ref_text_path = cast(str, payload["ref_text_path"])
        with open(ref_text_path, 'r', encoding='utf-8') as file:
            ref_text = file.read()

        input_diction = {
            "text": cast(str, payload["text"]),
            "text_lang": dict_language_v2[i18n_translator(cast(str, payload["text_language"]))],
            "ref_audio_path": cast(str, payload["ref_audio_path"]),
            "prompt_text": ref_text,
            "prompt_lang": dict_language_v2[i18n_translator(cast(str, payload["ref_language"]))],
            "top_p": 1,
            "temperature": 1,
            "text_split_method": cast(str, payload.get("text_split_method", "cut0")),
            "speed_factor": cast(float, payload.get("speed_factor", 1.0)),
            "sample_steps": gsv_sam_rate,
            "fragment_interval": cast(float, payload.get("fragment_interval", 0.5)),
        }
        with open(os.devnull, "w") as null:
            with contextlib.redirect_stdout(null):
                return next(tts.run(input_diction))


class TTSPool:
    """统一管理 worker 侧已加载的 TTS 实例。"""

    def __init__(self, max_loaded_models: int = 1) -> None:
        self.max_loaded_models: int = max_loaded_models
        self.loaded_tts_by_character_name: OrderedDict[str, TTS] = OrderedDict()

    def get_tts(self, runtime: CharacterVoiceRuntime) -> TTS:
        """获取指定角色对应的 TTS，必要时按 LRU 规则加载。"""
        character_name = runtime.character.character_name
        # 先尝试从已经加载的模型中寻找模型
        if character_name in self.loaded_tts_by_character_name:
            tts = self.loaded_tts_by_character_name[character_name]
            self.loaded_tts_by_character_name.move_to_end(character_name)
            self._sync_tts(runtime, tts)
            runtime.touch()
            return tts

        # 如果当前模型数量达到上限，删除访问时间最早的模型来节约空间（LRU）
        if len(self.loaded_tts_by_character_name) >= self.max_loaded_models:
            oldest_character_name, oldest_tts = self.loaded_tts_by_character_name.popitem(last=False)
            self._dispose_tts(oldest_tts)
            del oldest_character_name

        tts = self._create_tts(runtime)
        self.loaded_tts_by_character_name[character_name] = tts
        runtime.touch()

        return tts

    def ensure_loaded(self, runtime: CharacterVoiceRuntime) -> None:
        """确保指定角色已经拥有可用的 TTS 实例。"""
        self.get_tts(runtime)

    def unload(self, character_name: str) -> bool:
        """
        卸载指定角色当前已加载的 TTS 实例。
        :param character_name: 需要卸载模型的角色名称
        :returns bool: 该角色的模型是否真的被加载了。True：是，且该模型已经被卸载；False：该角色模型从来没有被加载过
        """
        if character_name not in self.loaded_tts_by_character_name:
            return False
        tts = self.loaded_tts_by_character_name.pop(character_name)
        self._dispose_tts(tts)
        return True

    def unload_all(self) -> None:
        """卸载池中所有已加载的 TTS 实例。"""
        for character_name in list(self.loaded_tts_by_character_name.keys()):
            self.unload(character_name)

    def set_device(self, runtime: CharacterVoiceRuntime, device: str) -> None:
        """更新指定角色的设备策略并迁移已加载模型。"""
        runtime.device_policy = device
        character_name = runtime.character.character_name
        if character_name not in self.loaded_tts_by_character_name:
            return
        target_device = resolve_device(device)
        tts = self.loaded_tts_by_character_name[character_name]
        tts.set_device(target_device, save=False)

    def get_loaded_device(self, character_name: str) -> str | None:
        """
        获取指定角色当前已加载模型所在的设备。

        :returns: 如果该角色的模型已经被加载了，返回其被加载的位置（"cuda"/"mps"/"cpu"）；否则，返回 None。
        """
        if character_name not in self.loaded_tts_by_character_name:
            return None
        tts = self.loaded_tts_by_character_name[character_name]
        return str(tts.configs.device)

    @staticmethod
    def _create_tts(runtime: CharacterVoiceRuntime) -> TTS:
        """根据运行时对象创建新的 TTS 实例。"""
        from TTS_infer_pack.TTS import TTS, TTS_Config

        with open(os.devnull, "w") as null:
            with contextlib.redirect_stdout(null):
                tts_config = TTS_Config()
                tts_config.device = resolve_device(runtime.device_policy)
                tts_config.t2s_weights_path = runtime.character.GPT_model_path or ''
                tts_config.vits_weights_path = runtime.character.sovits_model_path or ''
                return TTS(tts_config)

    @staticmethod
    def _sync_tts(runtime: CharacterVoiceRuntime, tts: TTS) -> None:
        """确保已加载的 TTS 与当前角色配置保持一致。"""
        target_device = resolve_device(runtime.device_policy)
        if str(tts.configs.device) != str(target_device):
            tts.set_device(target_device, save=False)

        with open(os.devnull, "w") as null:
            with contextlib.redirect_stdout(null):
                if tts.configs.t2s_weights_path != runtime.character.GPT_model_path:
                    tts.init_t2s_weights(runtime.character.GPT_model_path or '', save=False)
                if tts.configs.vits_weights_path != runtime.character.sovits_model_path:
                    tts.init_vits_weights(runtime.character.sovits_model_path or '', save=False)

    @staticmethod
    def _dispose_tts(tts: TTS) -> None:
        """释放一个 TTS 实例占用的资源。"""
        tts.unload()


def resolve_device(device_policy: str) -> torch.device:
    """
    根据设备策略解析出实际使用的 torch 设备。

    1.解析内容为 cuda/mps：优先使用电脑上可用的 cuda/mps。如果这两种设备不存在，则改为使用 CPU
    2.解析内容为 CPU：只会使用 CPU
    3.解析内容为 auto：优先使用电脑上可用的 cuda/mps。如果这两种设备不存在，则改为使用 CPU
    """
    normalized_policy = device_policy.lower()
    if normalized_policy == 'cuda' and torch.cuda.is_available():
        return torch.device('cuda')
    if normalized_policy == 'mps' and torch.mps.is_available():
        return torch.device('mps')
    if normalized_policy == 'cpu':
        return torch.device('cpu')
    if torch.cuda.is_available():
        return torch.device('cuda')
    if torch.mps.is_available():
        return torch.device('mps')
    return torch.device('cpu')


def build_language_mapping(i18n_translator) -> dict[str, str]:
    """构造 GPT-SoVITS v2 语音语言映射表。"""
    return {
        i18n_translator("中文"): "all_zh",
        i18n_translator("英文"): "en",
        i18n_translator("日文"): "all_ja",
        i18n_translator("粤语"): "all_yue",
        i18n_translator("韩文"): "all_ko",
        i18n_translator("中英混合"): "zh",
        i18n_translator("日英混合"): "ja",
        i18n_translator("粤英混合"): "yue",
        i18n_translator("韩英混合"): "ko",
        i18n_translator("多语种混合"): "auto",
        i18n_translator("多语种混合(粤语)"): "auto_yue",
    }


def allocate_output_wav_path(output_dir: str) -> str:
    """为本次生成分配一个新的输出音频路径。"""
    count_file = '../reference_audio/audio_generate_count.txt'
    with open(count_file, 'r', encoding='utf-8') as file:
        generate_count = int(file.read())
    output_wav_path = os.path.join(output_dir, f"output{generate_count}.wav")
    generate_count += 1
    with open(count_file, 'w', encoding='utf-8') as file:
        file.write(str(generate_count))
    return output_wav_path


def put_ack(result_queue, command: str, character_name: str, ok: bool, request_id: str = '') -> None:
    """向主进程返回一条命令确认结果。"""
    result_queue.put(
        {
            "type": "ack",
            "command": command,
            "request_id": request_id,
            "character_name": character_name,
            "ok": ok,
        }
    )


def put_error(result_queue, command: str, character_name: str, message: str, request_id: str = '') -> None:
    """向主进程返回一条错误结果。"""
    result_queue.put(
        {
            "type": "error",
            "command": command,
            "request_id": request_id,
            "character_name": character_name,
            "message": message,
        }
    )


def get_or_create_runtime(
    runtime_registry: dict[str, CharacterVoiceRuntime],
    character_name: str,
    character: CharacterAttributes | None,
    payload: PayloadDict | None = None,
) -> CharacterVoiceRuntime:
    """从注册表中获取或创建指定角色的运行时对象。"""
    runtime = runtime_registry.get(character_name)
    if runtime is None:
        if character is not None:
            runtime_character = character
            runtime_character.character_name = character_name
            runtime = CharacterVoiceRuntime(runtime_character)
            runtime_registry[character_name] = runtime
        else:
            raise ValueError("无法创建运行时对象：既没有找到已存在的运行时，也没有提供角色信息")
    elif character is not None:
        runtime.update_character(character)
    if payload is not None:
        runtime.apply_payload(payload)
    return runtime


def build_status_result(runtime: CharacterVoiceRuntime, tts_pool: TTSPool, request_id: str = '') -> ResultDict:
    """构造当前角色的运行时状态结果。"""
    character_name = runtime.character.character_name
    return {
        "type": "status",
        "request_id": request_id,
        "character_name": character_name,
        "is_loaded": character_name in tts_pool.loaded_tts_by_character_name,
        "device_policy": runtime.device_policy,
        "loaded_device": tts_pool.get_loaded_device(character_name),
        "gpt_model_path": runtime.character.GPT_model_path,
        "sovits_model_path": runtime.character.sovits_model_path,
        "last_error": runtime.last_error,
    }


def send_progress(progress_queue, message: str, request_id: str = '') -> None:
    """向主进程发送一条进度消息。"""
    progress_queue.put(
        {
            "type": "progress",
            "request_id": request_id,
            "message": message,
        }
    )


def synthesize(to_gptsovits_queue, from_gptsovits_queue, from_gptsovits_queue2) -> None:
    """作为独立 worker 进程处理 GPT-SoVITS 语音生成命令。"""
    from tools.i18n.i18n import I18nAuto

    i18n_translator = I18nAuto()
    dict_language_v2 = build_language_mapping(i18n_translator)
    runtime_registry: dict[str, CharacterVoiceRuntime] = {}
    tts_pool = TTSPool(max_loaded_models=2)

    while True:
        queue_shut_down = False
        command: CommandDict | None = None
        while True:
            try:
                raw_command = to_gptsovits_queue.get(block=False)
            except queue.Empty:
                time.sleep(0.2)
                continue
            except ValueError:
                queue_shut_down = True
                break
            else:
                if raw_command == 'bye':
                    command = {"type": "shutdown"}
                elif isinstance(raw_command, dict):
                    command = raw_command
                else:
                    command = None
                break

        if queue_shut_down:
            print("主程序似乎崩溃了…正在退出语音合成模块")
            tts_pool.unload_all()
            break
        if command is None:
            continue

        command_type = cast(str, command.get("type", ""))
        request_id = cast(str, command.get("request_id", ""))
        character_name = cast(str, command.get("character_name", ""))
        character = command.get("character")
        runtime_character = character if isinstance(character, CharacterAttributes) else None
        payload = cast(Optional[PayloadDict], command.get("payload"))

        if command_type == "shutdown":
            tts_pool.unload_all()
            break

        if command_type == "load_model":
            try:
                runtime = get_or_create_runtime(runtime_registry, character_name, runtime_character, payload)
                send_progress(from_gptsovits_queue2, f"正在加载 {character_name} 的 GPT-SoVITS 模型...", request_id)
                runtime.load_now(tts_pool)
                put_ack(from_gptsovits_queue, "load_model", character_name, True, request_id)
            except Exception as exc:
                print('语音模型加载错误信息：', exc)
                put_error(from_gptsovits_queue, "load_model", character_name, str(exc), request_id)
            continue

        if command_type == "unload_model":
            runtime = runtime_registry.get(character_name)
            if runtime is not None:
                runtime.unload_now(tts_pool)
            else:
                tts_pool.unload(character_name)
            put_ack(from_gptsovits_queue, "unload_model", character_name, True, request_id)
            continue

        if command_type == "set_device":
            try:
                runtime = get_or_create_runtime(runtime_registry, character_name, runtime_character, payload)
                device = cast(str, command.get("device", DEFAULT_DEVICE_POLICY))
                runtime.set_device_policy(device, tts_pool=tts_pool, apply_now=True)
                put_ack(from_gptsovits_queue, "set_device", character_name, True, request_id)
            except Exception as exc:
                print('语音设备切换错误信息：', exc)
                put_error(from_gptsovits_queue, "set_device", character_name, str(exc), request_id)
            continue

        if command_type == "get_status":
            runtime = get_or_create_runtime(runtime_registry, character_name, runtime_character, payload)
            from_gptsovits_queue.put(build_status_result(runtime, tts_pool, request_id))
            continue

        if command_type != "synthesize" or payload is None:
            continue

        try:
            runtime = get_or_create_runtime(runtime_registry, character_name, runtime_character, payload)
            send_progress(from_gptsovits_queue2, f"正在为 {character_name} 合成语音...", request_id)
            last_sampling_rate, last_audio_data = runtime.synthesize(payload, tts_pool, dict_language_v2, i18n_translator)
            output_dir = cast(str, payload.get("output_dir", "../reference_audio/generated_audios_temp"))
            output_wav_path = allocate_output_wav_path(output_dir)
            sf.write(output_wav_path, last_audio_data, last_sampling_rate)
            from_gptsovits_queue.put(
                {
                    "type": "synthesize_result",
                    "request_id": request_id,
                    "character_name": character_name,
                    "output_wav_path": output_wav_path,
                }
            )
        except StopIteration:
            print('语音合成没有输出音频')
            put_error(from_gptsovits_queue, "synthesize", character_name, "语音合成没有输出音频", request_id)
        except Exception as exc:
            print('语音合成错误信息：', exc)
            runtime = runtime_registry.get(character_name)
            if runtime is not None:
                runtime.last_error = str(exc)
            put_error(from_gptsovits_queue, "synthesize", character_name, str(exc), request_id)
