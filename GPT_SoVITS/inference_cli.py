from __future__ import annotations

import contextlib
import os
import queue
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, Optional

import soundfile as sf
import torch

os.chdir(os.path.dirname(__file__))

from character import CharacterAttributes
from process_ckpt import get_sovits_version_from_path_fast, load_sovits_new
from qconfig import d_sakiko_config

if TYPE_CHECKING:
    import numpy as np
    from TTS_infer_pack.TTS import TTS, TTS_Config

gsv_sam_rate = d_sakiko_config.sovits_inference_sampling_steps.value
SILENCE_WAV_PATH = '../reference_audio/silent_audio/silence.wav'

if d_sakiko_config.cuda_enabled.value:
    DEFAULT_DEVICE_POLICY = "cuda"
elif d_sakiko_config.mps_enabled.value:
    DEFAULT_DEVICE_POLICY = "mps"
else:
    DEFAULT_DEVICE_POLICY = 'cpu'

PayloadDict = dict[str, object]
CommandDict = dict[str, object]
ResultDict = dict[str, object]


def build_empty_prompt_cache_snapshot() -> dict[str, object]:
    """构造角色会话使用的空 prompt 缓存快照。"""
    return {
        "ref_audio_path": None,
        "prompt_semantic": None,
        "refer_spec": [],
        "prompt_text": None,
        "prompt_lang": None,
        "phones": None,
        "bert_features": None,
        "norm_text": None,
        "aux_ref_audio_paths": [],
        "raw_audio": None,
        "raw_sr": None,
    }


@dataclass(frozen=True)
class ModelArchitectureSignature:
    """描述角色模型能否复用同一共享推理壳的结构签名。"""

    model_version: str
    device: str
    is_half: bool
    bert_base_path: str
    cnhuhbert_base_path: str
    use_vocoder: bool
    needs_sv: bool
    vits_class_name: str
    t2s_config_shape_key: tuple[object, ...]
    vits_config_shape_key: tuple[object, ...]
    t2s_weights_kind: str
    vits_weights_kind: str


@dataclass
class WeightsCacheEntry:
    """表示一条已加载到 CPU 内存中的权重缓存。"""

    path: str
    kind: str
    checkpoint: dict[str, object]
    model_version: str
    loaded_at: float
    last_used_at: float
    is_lora: bool = False


@dataclass
class SharedTTSBundle:
    """表示一套按结构签名复用的共享推理壳。"""

    signature: ModelArchitectureSignature
    tts: "TTS"
    current_t2s_weights_path: str | None = None
    current_vits_weights_path: str | None = None
    current_character_name: str | None = None
    last_used_at: float | None = None


class ModelWeightsCache:
    """统一管理角色权重的 CPU 侧缓存。"""

    def __init__(self, max_entries: int = 8) -> None:
        self.max_entries: int = max_entries
        self.entries_by_path: OrderedDict[str, WeightsCacheEntry] = OrderedDict()

    def get_t2s_checkpoint(self, weights_path: str) -> WeightsCacheEntry:
        """获取一份 T2S 权重缓存。"""
        entry = self.entries_by_path.get(weights_path)
        if entry is None:
            checkpoint = cast(
                dict[str, object],
                torch.load(weights_path, map_location="cpu", weights_only=False),
            )
            entry = WeightsCacheEntry(
                path=weights_path,
                kind="t2s",
                checkpoint=checkpoint,
                model_version=self._infer_t2s_model_version(checkpoint),
                loaded_at=time.time(),
                last_used_at=time.time(),
            )
            self.entries_by_path[weights_path] = entry
        else:
            self.mark_used(entry)
        self.evict_if_needed()
        return entry

    def get_vits_checkpoint(self, weights_path: str) -> WeightsCacheEntry:
        """获取一份 SoVITS 权重缓存。"""
        entry = self.entries_by_path.get(weights_path)
        if entry is None:
            _, model_version, if_lora_v3 = get_sovits_version_from_path_fast(weights_path)
            checkpoint = cast(dict[str, object], load_sovits_new(weights_path))
            entry = WeightsCacheEntry(
                path=weights_path,
                kind="vits",
                checkpoint=checkpoint,
                model_version=model_version,
                loaded_at=time.time(),
                last_used_at=time.time(),
                is_lora=if_lora_v3,
            )
            self.entries_by_path[weights_path] = entry
        else:
            self.mark_used(entry)
        self.evict_if_needed()
        return entry

    def mark_used(self, entry: WeightsCacheEntry) -> None:
        """将某条权重缓存标记为最近使用。"""
        entry.last_used_at = time.time()
        if entry.path in self.entries_by_path:
            self.entries_by_path.move_to_end(entry.path)

    def evict_if_needed(self) -> None:
        """当缓存超过上限时按 LRU 淘汰。"""
        while len(self.entries_by_path) > self.max_entries:
            _, entry = self.entries_by_path.popitem(last=False)
            del entry

    def unload_path(self, weights_path: str) -> None:
        """删除指定路径对应的权重缓存。"""
        entry = self.entries_by_path.pop(weights_path, None)
        if entry is not None:
            del entry

    def unload_all(self) -> None:
        """清空全部权重缓存。"""
        self.entries_by_path.clear()

    @staticmethod
    def _infer_t2s_model_version(checkpoint: dict[str, object]) -> str:
        """从 T2S checkpoint 中推断一个粗粒度版本字符串。"""
        config_object = checkpoint.get("config")
        if not isinstance(config_object, dict):
            return "unknown"
        model_object = config_object.get("model")
        if not isinstance(model_object, dict):
            return "unknown"
        version_object = model_object.get("version")
        if isinstance(version_object, str):
            return version_object
        return "unknown"


class SharedTTSManager:
    """统一管理角色会话、共享 bundle 与权重缓存。"""

    def __init__(
        self,
        max_active_bundles: int = d_sakiko_config.max_loaded_voice_models.value,
        max_cached_weight_entries: int = 8,
    ) -> None:
        self.max_active_bundles: int = max_active_bundles
        self.weight_cache: ModelWeightsCache = ModelWeightsCache(max_entries=max_cached_weight_entries)
        self.bundles_by_signature: OrderedDict[ModelArchitectureSignature, SharedTTSBundle] = OrderedDict()
        self.runtime_index: dict[str, CharacterVoiceRuntime] = {}

    def get_tts_for_runtime(self, runtime: CharacterVoiceRuntime) -> "TTS":
        """获取某个角色当前应绑定的共享推理壳。"""
        self._register_runtime(runtime)
        signature = self._build_signature(runtime)
        bundle = self._get_or_create_bundle(signature, runtime)
        self._ensure_role_weights_bound(runtime, bundle)
        self._activate_runtime_prompt_cache(runtime, bundle)
        runtime.last_bound_signature = signature
        runtime.last_bound_bundle_key = signature
        runtime.touch()
        return bundle.tts

    def ensure_loaded(self, runtime: CharacterVoiceRuntime) -> None:
        """确保某个角色已经被预热到可用状态。"""
        self.get_tts_for_runtime(runtime)

    def save_runtime_prompt_cache(self, runtime: CharacterVoiceRuntime) -> None:
        """将当前 bundle 中的 prompt 缓存写回角色会话。"""
        if runtime.last_bound_bundle_key is None:
            return
        bundle = self.bundles_by_signature.get(runtime.last_bound_bundle_key)
        if bundle is None:
            return
        self._save_runtime_prompt_cache(runtime, bundle)

    def unload_runtime(self, runtime: CharacterVoiceRuntime) -> bool:
        """卸载某个角色的运行时状态。"""
        self.runtime_index.pop(runtime.character.character_name, None)
        had_state = runtime.last_bound_bundle_key is not None or runtime.prompt_cache_snapshot != build_empty_prompt_cache_snapshot()
        runtime.last_bound_signature = None
        runtime.last_bound_bundle_key = None
        runtime.clear_prompt_cache_snapshot()
        return had_state

    def unload_character(self, character_name: str) -> bool:
        """按角色名称卸载运行时状态。"""
        runtime = self.runtime_index.get(character_name)
        if runtime is None:
            return False
        return self.unload_runtime(runtime)

    def unload_all(self) -> None:
        """释放全部 bundle 与权重缓存。"""
        for _, bundle in list(self.bundles_by_signature.items()):
            self._dispose_bundle(bundle)
        self.bundles_by_signature.clear()
        self.weight_cache.unload_all()
        for runtime in self.runtime_index.values():
            runtime.last_bound_signature = None
            runtime.last_bound_bundle_key = None
            runtime.clear_prompt_cache_snapshot()
        self.runtime_index.clear()

    def set_device(self, runtime: CharacterVoiceRuntime, device: str) -> None:
        """更新角色目标设备策略。"""
        runtime.device_policy = device
        runtime.last_bound_signature = None
        runtime.last_bound_bundle_key = None
        self._register_runtime(runtime)

    def get_loaded_device(self, character_name: str) -> str | None:
        """获取某个角色当前绑定 bundle 的设备。"""
        runtime = self.runtime_index.get(character_name)
        if runtime is None or runtime.last_bound_signature is None:
            return None
        return runtime.last_bound_signature.device

    def get_status(self, runtime: CharacterVoiceRuntime) -> dict[str, object]:
        """获取某个角色当前在共享层中的状态。"""
        self._register_runtime(runtime)
        bundle = None
        if runtime.last_bound_bundle_key is not None:
            bundle = self.bundles_by_signature.get(runtime.last_bound_bundle_key)
        return {
            "is_loaded": bundle is not None,
            "device_policy": runtime.device_policy,
            "loaded_device": runtime.last_bound_signature.device if runtime.last_bound_signature is not None else None,
            "bundle_signature": str(runtime.last_bound_bundle_key) if runtime.last_bound_bundle_key is not None else None,
            "bundle_current_character": bundle.current_character_name if bundle is not None else None,
            "gpt_model_path": runtime.character.GPT_model_path,
            "sovits_model_path": runtime.character.sovits_model_path,
            "last_error": runtime.last_error,
        }

    def _register_runtime(self, runtime: CharacterVoiceRuntime) -> None:
        """登记一个角色运行时对象。"""
        self.runtime_index[runtime.character.character_name] = runtime

    def _build_signature(self, runtime: CharacterVoiceRuntime) -> ModelArchitectureSignature:
        """为角色构造共享判定所需的结构签名。"""
        from TTS_infer_pack.TTS import TTS_Config

        t2s_path, vits_path = runtime.get_model_paths()
        t2s_entry = self.weight_cache.get_t2s_checkpoint(t2s_path)
        vits_entry = self.weight_cache.get_vits_checkpoint(vits_path)
        target_device = resolve_device(runtime.device_policy)
        default_config = cast(dict[str, object], TTS_Config.default_configs[vits_entry.model_version])
        use_vocoder = vits_entry.model_version in {"v3", "v4"}
        needs_sv = vits_entry.model_version in {"v2Pro", "v2ProPlus"}
        vits_class_name = "SynthesizerTrnV3" if use_vocoder else "SynthesizerTrn"
        return ModelArchitectureSignature(
            model_version=vits_entry.model_version,
            device=str(target_device),
            is_half=cast(bool, default_config["is_half"]),
            bert_base_path=cast(str, default_config["bert_base_path"]),
            cnhuhbert_base_path=cast(str, default_config["cnhuhbert_base_path"]),
            use_vocoder=use_vocoder,
            needs_sv=needs_sv,
            vits_class_name=vits_class_name,
            t2s_config_shape_key=self._build_shape_key_from_checkpoint(t2s_entry.checkpoint),
            vits_config_shape_key=self._build_shape_key_from_checkpoint(vits_entry.checkpoint),
            t2s_weights_kind="full",
            vits_weights_kind="lora" if vits_entry.is_lora else "full",
        )

    def _get_or_create_bundle(
        self,
        signature: ModelArchitectureSignature,
        runtime: CharacterVoiceRuntime,
    ) -> SharedTTSBundle:
        """查找或创建一个共享 bundle。"""
        bundle = self.bundles_by_signature.get(signature)
        if bundle is None:
            bundle = self._create_bundle(signature, runtime)
            self.bundles_by_signature[signature] = bundle
            self._evict_bundle_if_needed()
        else:
            self.bundles_by_signature.move_to_end(signature)
        bundle.last_used_at = time.time()
        return bundle

    def _create_bundle(
        self,
        signature: ModelArchitectureSignature,
        runtime: CharacterVoiceRuntime,
    ) -> SharedTTSBundle:
        """创建一套新的共享推理壳。"""
        from TTS_infer_pack.TTS import TTS, TTS_Config

        config = TTS_Config(
            {
                "custom": {
                    "device": signature.device,
                    "is_half": signature.is_half,
                    "version": signature.model_version,
                    "t2s_weights_path": runtime.character.GPT_model_path or "",
                    "vits_weights_path": runtime.character.sovits_model_path or "",
                    "bert_base_path": signature.bert_base_path,
                    "cnhuhbert_base_path": signature.cnhuhbert_base_path,
                }
            }
        )
        config.device = resolve_device(runtime.device_policy)
        config.is_half = signature.is_half
        config.update_version(signature.model_version)
        config.bert_base_path = signature.bert_base_path
        config.cnhuhbert_base_path = signature.cnhuhbert_base_path
        tts = TTS(config, init_mode="shared_shell")
        return SharedTTSBundle(signature=signature, tts=tts, last_used_at=time.time())

    def _ensure_role_weights_bound(
        self,
        runtime: CharacterVoiceRuntime,
        bundle: SharedTTSBundle,
    ) -> None:
        """确保目标角色的声学权重已经装载到 bundle 中。"""
        t2s_path, vits_path = runtime.get_model_paths()
        if bundle.current_t2s_weights_path != t2s_path:
            t2s_entry = self.weight_cache.get_t2s_checkpoint(t2s_path)
            bundle.tts.init_t2s_weights(
                t2s_path,
                save=False,
                checkpoint=t2s_entry.checkpoint,
            )
            bundle.current_t2s_weights_path = t2s_path
        if bundle.current_vits_weights_path != vits_path:
            vits_entry = self.weight_cache.get_vits_checkpoint(vits_path)
            bundle.tts.init_vits_weights(
                vits_path,
                save=False,
                checkpoint=vits_entry.checkpoint,
            )
            bundle.current_vits_weights_path = vits_path
        bundle.current_character_name = runtime.character.character_name
        bundle.last_used_at = time.time()

    def _activate_runtime_prompt_cache(
        self,
        runtime: CharacterVoiceRuntime,
        bundle: SharedTTSBundle,
    ) -> None:
        """将角色自己的 prompt 缓存挂到共享壳上。"""
        bundle.tts.import_prompt_cache(runtime.load_prompt_cache_snapshot())

    def _save_runtime_prompt_cache(
        self,
        runtime: CharacterVoiceRuntime,
        bundle: SharedTTSBundle,
    ) -> None:
        """把共享壳上的 prompt 缓存写回角色会话。"""
        runtime.save_prompt_cache_snapshot(bundle.tts.export_prompt_cache())

    def _dispose_bundle(self, bundle: SharedTTSBundle) -> None:
        """释放一个共享 bundle。"""
        bundle.tts.unload()

    def _evict_bundle_if_needed(self) -> None:
        """当 bundle 超过上限时按 LRU 淘汰。"""
        while len(self.bundles_by_signature) > self.max_active_bundles:
            _, oldest_bundle = self.bundles_by_signature.popitem(last=False)
            self._dispose_bundle(oldest_bundle)

    @staticmethod
    def _build_shape_key_from_checkpoint(checkpoint: dict[str, object]) -> tuple[object, ...]:
        """从 checkpoint 中提取一组稳定的形状摘要。"""
        weights_object = checkpoint.get("weight")
        if not isinstance(weights_object, dict):
            return tuple()
        shape_items: list[tuple[str, tuple[int, ...]]] = []
        for name in sorted(weights_object.keys()):
            tensor_object = weights_object[name]
            if isinstance(tensor_object, torch.Tensor):
                shape_items.append(
                    (
                        str(name),
                        tuple(int(dimension) for dimension in tensor_object.shape),
                    )
                )
            if len(shape_items) >= 12:
                break
        return tuple(shape_items)


class CharacterVoiceRuntime:
    """保存 worker 侧单个角色的运行时元数据。"""

    def __init__(self, character: CharacterAttributes, device_policy: str = DEFAULT_DEVICE_POLICY) -> None:
        self.character: CharacterAttributes = character
        self.device_policy: str = device_policy
        self.last_used_at: float | None = None
        self.last_error: str | None = None
        self.last_bound_signature: ModelArchitectureSignature | None = None
        self.last_bound_bundle_key: ModelArchitectureSignature | None = None
        self.prompt_cache_snapshot: dict[str, object] = build_empty_prompt_cache_snapshot()

    def update_character(self, character: CharacterAttributes) -> None:
        """使用最新的角色信息刷新当前运行时对象。"""
        self.character = character

    def apply_payload(self, payload: PayloadDict) -> None:
        """用命令 payload 中的语音字段更新角色信息。"""
        if "gpt_model_path" in payload:
            self.character.GPT_model_path = cast(str, payload["gpt_model_path"])
        if "sovits_model_path" in payload:
            self.character.sovits_model_path = cast(
                str,
                payload["sovits_model_path"],
            )
        if "ref_audio_path" in payload:
            self.character.gptsovits_ref_audio = cast(
                str,
                payload["ref_audio_path"],
            )
        if "ref_text_path" in payload:
            self.character.gptsovits_ref_audio_text = cast(
                str,
                payload["ref_text_path"],
            )
        if "ref_language" in payload:
            self.character.gptsovits_ref_audio_lan = cast(
                str,
                payload["ref_language"],
            )

    def touch(self) -> None:
        """更新最近一次使用时间。"""
        self.last_used_at = time.time()

    def get_model_paths(self) -> tuple[str, str]:
        """返回当前角色的模型路径二元组。"""
        return self.character.GPT_model_path or "", self.character.sovits_model_path or ""

    def save_prompt_cache_snapshot(self, prompt_cache: dict[str, object]) -> None:
        """保存当前角色的 prompt 缓存快照。"""
        self.prompt_cache_snapshot = prompt_cache

    def load_prompt_cache_snapshot(self) -> dict[str, object]:
        """读取当前角色的 prompt 缓存快照。"""
        return self.prompt_cache_snapshot

    def clear_prompt_cache_snapshot(self) -> None:
        """清空当前角色的 prompt 缓存快照。"""
        self.prompt_cache_snapshot = build_empty_prompt_cache_snapshot()

    def load_now(self, tts_manager: SharedTTSManager) -> None:
        """显式加载当前角色模型。"""
        tts_manager.ensure_loaded(self)

    def unload_now(self, tts_manager: SharedTTSManager) -> None:
        """显式卸载当前角色模型。"""
        tts_manager.unload_runtime(self)

    def set_device_policy(
        self,
        device: str,
        tts_manager: SharedTTSManager | None = None,
        apply_now: bool = True,
    ) -> None:
        """更新当前角色的目标设备策略。"""
        self.device_policy = device
        if tts_manager is not None and apply_now:
            tts_manager.set_device(self, device)

    def synthesize(
        self,
        payload: PayloadDict,
        tts_manager: SharedTTSManager,
        dict_language_v2: dict[str, str],
        i18n_translator,
    ) -> tuple[int, np.ndarray]:
        """执行一次语音合成并返回采样率与音频数据。"""
        self.apply_payload(payload)
        tts = tts_manager.get_tts_for_runtime(self)

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
                generator = tts.run(input_diction)
                try:
                    return next(generator)
                finally:
                    generator.close()
                    tts_manager.save_runtime_prompt_cache(self)


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


def build_status_result(
    runtime: CharacterVoiceRuntime,
    tts_manager: SharedTTSManager,
    request_id: str = '',
) -> ResultDict:
    """构造当前角色的运行时状态结果。"""
    character_name = runtime.character.character_name
    return {
        "type": "status",
        "request_id": request_id,
        "character_name": character_name,
        **tts_manager.get_status(runtime),
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
    tts_manager = SharedTTSManager(
        max_active_bundles=d_sakiko_config.max_loaded_voice_models.value
    )

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
            tts_manager.unload_all()
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
            tts_manager.unload_all()
            break

        if command_type == "load_model":
            try:
                runtime = get_or_create_runtime(runtime_registry, character_name, runtime_character, payload)
                send_progress(from_gptsovits_queue2, f"正在加载 {character_name} 的 GPT-SoVITS 模型...", request_id)
                runtime.load_now(tts_manager)
                put_ack(from_gptsovits_queue, "load_model", character_name, True, request_id)
            except Exception as exc:
                print('语音模型加载错误信息：', exc)
                put_error(from_gptsovits_queue, "load_model", character_name, str(exc), request_id)
            continue

        if command_type == "unload_model":
            runtime = runtime_registry.get(character_name)
            if runtime is not None:
                runtime.unload_now(tts_manager)
            else:
                tts_manager.unload_character(character_name)
            put_ack(from_gptsovits_queue, "unload_model", character_name, True, request_id)
            continue

        if command_type == "set_device":
            try:
                runtime = get_or_create_runtime(runtime_registry, character_name, runtime_character, payload)
                device = cast(str, command.get("device", DEFAULT_DEVICE_POLICY))
                runtime.set_device_policy(device, tts_manager=tts_manager, apply_now=True)
                put_ack(from_gptsovits_queue, "set_device", character_name, True, request_id)
            except Exception as exc:
                print('语音设备切换错误信息：', exc)
                put_error(from_gptsovits_queue, "set_device", character_name, str(exc), request_id)
            continue

        if command_type == "get_status":
            runtime = get_or_create_runtime(runtime_registry, character_name, runtime_character, payload)
            from_gptsovits_queue.put(build_status_result(runtime, tts_manager, request_id))
            continue

        if command_type != "synthesize" or payload is None:
            continue

        try:
            runtime = get_or_create_runtime(runtime_registry, character_name, runtime_character, payload)
            send_progress(from_gptsovits_queue2, f"正在为 {character_name} 合成语音...", request_id)
            last_sampling_rate, last_audio_data = runtime.synthesize(payload, tts_manager, dict_language_v2, i18n_translator)
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
            import traceback
            traceback.print_exc()
            
            runtime = runtime_registry.get(character_name)
            if runtime is not None:
                runtime.last_error = str(exc)
            put_error(from_gptsovits_queue, "synthesize", character_name, str(exc), request_id)
