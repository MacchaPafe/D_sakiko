from __future__ import annotations

import argparse
import contextlib
import gc
import os
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil
import torch

SCRIPT_DIR = Path(__file__).resolve().parent
GPT_SOVITS_DIR = SCRIPT_DIR.parent
REPO_ROOT = GPT_SOVITS_DIR.parent

sys.path.insert(0, str(GPT_SOVITS_DIR))

ANON_GPT_PATH = REPO_ROOT / "reference_audio/anon/GPT-SoVITS_models/anon_v2pp.ckpt"
ANON_SOVITS_PATH = REPO_ROOT / "reference_audio/anon/GPT-SoVITS_models/anon_v2pp.pth"
TOMORI_GPT_PATH = REPO_ROOT / "reference_audio/tomori/GPT-SoVITS_models/tomori.ckpt"
TOMORI_SOVITS_PATH = REPO_ROOT / "reference_audio/tomori/GPT-SoVITS_models/tomori.pth"
SAKIKO_V4_GPT_PATH = REPO_ROOT / "reference_audio/sakiko/GPT-SoVITS_models/sakiko_v4-e10.ckpt"
SAKIKO_V4_SOVITS_PATH = REPO_ROOT / "reference_audio/sakiko/GPT-SoVITS_models/sakiko_v4_e6_s390_l64.pth"

import inference_cli
from character import CharacterAttributes
from TTS_infer_pack.TTS import TTS, TTS_Config


@dataclass(frozen=True)
class BenchmarkModel:
    """表示一次加载测试使用的角色模型配置。"""

    name: str
    gpt_path: Path
    sovits_path: Path
    model_version: str


@dataclass(frozen=True)
class BenchmarkSample:
    """表示单次模型加载耗时。"""

    scenario_name: str
    iteration: int
    model_name: str
    duration_seconds: float


@dataclass(frozen=True)
class BenchmarkResult:
    """表示某个测试场景的完整结果。"""

    scenario_name: str
    samples: list[BenchmarkSample]
    active_bundle_count: int | None = None


@dataclass(frozen=True)
class MemorySection:
    """表示内存诊断输出中的一个可计数组件。"""

    name: str
    bytes_count: int


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="对比 GPT-SoVITS 多角色模型加载策略的耗时。",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=10,
        help="每个场景中两个模型交替加载的总次数。",
    )
    parser.add_argument(
        "--device-policy",
        type=str,
        default=inference_cli.DEFAULT_DEVICE_POLICY,
        choices=["cpu", "cuda", "mps", "auto"],
        help="加载模型时使用的设备策略。",
    )
    parser.add_argument(
        "--include-legacy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否执行旧式每次新建 TTS 实例的基线测试。",
    )
    parser.add_argument(
        "--model-pair",
        type=str,
        default="v2pp_pair",
        choices=["v2pp_pair", "v2pp_v4"],
        help=(
            "选择测试用模型组。"
            "v2pp_pair 表示 anon v2ProPlus 与 tomori v2ProPlus；"
            "v2pp_v4 表示 anon v2ProPlus 与 sakiko v4。"
        ),
    )
    parser.add_argument(
        "--print-memory-breakdown",
        action="store_true",
        help="在共享 bundle 测试完成且卸载前打印 PyTorch 模型与权重缓存的内存拆分。",
    )
    return parser.parse_args()


def build_benchmark_models(model_pair: str) -> list[BenchmarkModel]:
    """根据命令行参数构造测试模型组。"""
    if model_pair == "v2pp_v4":
        return [
            BenchmarkModel("anon_v2pp", ANON_GPT_PATH, ANON_SOVITS_PATH, "v2ProPlus"),
            BenchmarkModel("sakiko_v4", SAKIKO_V4_GPT_PATH, SAKIKO_V4_SOVITS_PATH, "v4"),
        ]
    return [
        BenchmarkModel("anon_v2pp", ANON_GPT_PATH, ANON_SOVITS_PATH, "v2ProPlus"),
        BenchmarkModel("tomori_v2pp", TOMORI_GPT_PATH, TOMORI_SOVITS_PATH, "v2ProPlus"),
    ]


def validate_model_files(models: list[BenchmarkModel]) -> None:
    """检查基准测试需要的模型文件是否存在。"""
    missing_paths = [
        path
        for model in models
        for path in [model.gpt_path, model.sovits_path]
        if not path.exists()
    ]
    if missing_paths:
        missing_text = "\n".join(str(path) for path in missing_paths)
        raise FileNotFoundError(f"以下模型文件不存在，无法运行 benchmark：\n{missing_text}")


def create_character(model: BenchmarkModel) -> CharacterAttributes:
    """根据模型配置创建一个最小角色对象。"""
    character = CharacterAttributes()
    character.character_name = model.name
    character.GPT_model_path = str(model.gpt_path)
    character.sovits_model_path = str(model.sovits_path)
    return character


def create_runtime(
    model: BenchmarkModel,
    device_policy: str,
) -> inference_cli.CharacterVoiceRuntime:
    """创建用于共享 manager 测试的角色运行时对象。"""
    return inference_cli.CharacterVoiceRuntime(
        create_character(model),
        device_policy=device_policy,
    )


def get_model_sequence(models: list[BenchmarkModel], iterations: int) -> list[BenchmarkModel]:
    """生成两个模型交替加载的测试序列。"""
    return [models[index % len(models)] for index in range(iterations)]


def clear_torch_cache() -> None:
    """主动触发 Python 与 PyTorch 的缓存清理。"""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    if torch.mps.is_available():
        torch.mps.empty_cache()


def measure_duration_seconds(callback: Callable[[], None]) -> float:
    """测量一次回调执行耗时。"""
    start_time = time.perf_counter()
    callback()
    return time.perf_counter() - start_time


def format_bytes(bytes_count: int) -> str:
    """将字节数格式化成更容易阅读的二进制单位。"""
    gib = 1024 ** 3
    mib = 1024 ** 2
    kib = 1024
    if abs(bytes_count) >= gib:
        return f"{bytes_count / gib:8.3f} GiB"
    if abs(bytes_count) >= mib:
        return f"{bytes_count / mib:8.3f} MiB"
    if abs(bytes_count) >= kib:
        return f"{bytes_count / kib:8.3f} KiB"
    return f"{bytes_count:8d} B"


def get_process_rss_bytes() -> int:
    """读取当前进程的 RSS 内存占用。"""
    process = psutil.Process(os.getpid())
    return int(process.memory_info().rss)


def count_tensor_storage_bytes(
    tensor: torch.Tensor,
    seen_storages: set[tuple[str, int, int]],
) -> int:
    """按底层 storage 去重统计一个 tensor 的容量字节数。"""
    try:
        storage = tensor.untyped_storage()
        storage_bytes = int(storage.nbytes())
        storage_key = (str(tensor.device), int(storage.data_ptr()), storage_bytes)
    except (AttributeError, RuntimeError):
        storage_bytes = int(tensor.nelement() * tensor.element_size())
        storage_key = (str(tensor.device), int(tensor.data_ptr()), storage_bytes)
    if storage_key in seen_storages:
        return 0
    seen_storages.add(storage_key)
    return storage_bytes


def count_module_tensor_bytes(
    module: torch.nn.Module,
    seen_storages: set[tuple[str, int, int]],
) -> int:
    """统计一个 PyTorch Module 的参数与 buffer 容量字节数。"""
    total_bytes = 0
    for parameter in module.parameters(recurse=True):
        total_bytes += count_tensor_storage_bytes(parameter, seen_storages)
    for buffer in module.buffers(recurse=True):
        total_bytes += count_tensor_storage_bytes(buffer, seen_storages)
    return total_bytes


def count_nested_tensor_bytes(
    value: object,
    seen_storages: set[tuple[str, int, int]],
    seen_objects: set[int],
) -> int:
    """递归统计 checkpoint 等嵌套容器里的 tensor 容量字节数。"""
    object_id = id(value)
    if object_id in seen_objects:
        return 0
    seen_objects.add(object_id)

    if isinstance(value, torch.Tensor):
        return count_tensor_storage_bytes(value, seen_storages)
    if isinstance(value, torch.nn.Module):
        return count_module_tensor_bytes(value, seen_storages)
    if isinstance(value, dict):
        return sum(
            count_nested_tensor_bytes(item, seen_storages, seen_objects)
            for item in value.values()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return sum(
            count_nested_tensor_bytes(item, seen_storages, seen_objects)
            for item in value
        )
    return 0


def build_module_memory_section(
    name: str,
    module: object,
    seen_storages: set[tuple[str, int, int]] | None = None,
) -> MemorySection | None:
    """尝试把一个对象作为 PyTorch Module 统计成内存分区。"""
    if not isinstance(module, torch.nn.Module):
        return None
    storage_set = seen_storages if seen_storages is not None else set()
    return MemorySection(
        name=name,
        bytes_count=count_module_tensor_bytes(module, storage_set),
    )


def collect_tts_memory_sections(
    tts: object,
    seen_storages: set[tuple[str, int, int]] | None = None,
) -> list[MemorySection]:
    """收集一个 TTS 实例中主要模型组件的内存拆分。"""
    sections: list[MemorySection] = []
    component_names = [
        "bert_model",
        "cnhuhbert_model",
        "t2s_model",
        "vits_model",
        "vocoder",
        "sr_model",
    ]
    for component_name in component_names:
        section = build_module_memory_section(
            component_name,
            getattr(tts, component_name, None),
            seen_storages,
        )
        if section is not None:
            sections.append(section)

    sv_model = getattr(tts, "sv_model", None)
    sv_section = build_module_memory_section("sv_model", sv_model, seen_storages)
    if sv_section is not None:
        sections.append(sv_section)
    else:
        embedding_model = getattr(sv_model, "embedding_model", None)
        embedding_section = build_module_memory_section(
            "sv_model.embedding_model",
            embedding_model,
            seen_storages,
        )
        if embedding_section is not None:
            sections.append(embedding_section)

    return sections


def collect_weight_cache_memory_sections(
    manager: inference_cli.SharedTTSManager,
    seen_storages: set[tuple[str, int, int]] | None = None,
) -> list[MemorySection]:
    """收集 CPU 侧 checkpoint 权重缓存的内存拆分。"""
    sections: list[MemorySection] = []
    storage_set = seen_storages if seen_storages is not None else set()
    for entry in manager.weight_cache.entries_by_path.values():
        bytes_count = count_nested_tensor_bytes(entry.checkpoint, storage_set, set())
        path_name = Path(entry.path).name
        sections.append(
            MemorySection(
                name=f"{entry.kind}:{path_name}",
                bytes_count=bytes_count,
            )
        )
    return sections


def print_memory_sections(sections: list[MemorySection], indent: str = "  ") -> int:
    """打印一组内存分区并返回它们的总字节数。"""
    total_bytes = 0
    for section in sections:
        total_bytes += section.bytes_count
        print(f"{indent}{section.name:<32} {format_bytes(section.bytes_count)}")
    return total_bytes


def print_shared_manager_memory_breakdown(
    manager: inference_cli.SharedTTSManager,
    scenario_name: str,
) -> None:
    """打印共享 TTS manager 当前驻留对象的内存诊断信息。"""
    rss_bytes = get_process_rss_bytes()
    print(f"\n[memory_breakdown:{scenario_name}]")
    print(f"process_rss                    {format_bytes(rss_bytes)}")
    print("note: tensor_capacity 统计参数/buffer/checkpoint 的 storage 容量；RSS 是进程常驻页，二者不是同一口径。")
    print(f"active_bundle_count            {len(manager.bundles_by_signature)}")
    print(f"shared_frontend_count          {len(manager.frontend_cache.entries_by_key)}")

    bundle_total_bytes = 0
    seen_storages: set[tuple[str, int, int]] = set()
    for index, bundle in enumerate(manager.bundles_by_signature.values(), start=1):
        signature = bundle.signature
        character_name = bundle.current_character_name or "<none>"
        print(
            f"bundle[{index}] "
            f"version={signature.model_version} "
            f"device={signature.device} "
            f"character={character_name}"
        )
        sections = collect_tts_memory_sections(bundle.tts)
        local_bundle_bytes = print_memory_sections(sections, indent="  ")
        unique_sections = collect_tts_memory_sections(bundle.tts, seen_storages)
        unique_bundle_bytes = sum(section.bytes_count for section in unique_sections)
        bundle_total_bytes += unique_bundle_bytes
        print(f"  {'bundle_tensor_capacity':<32} {format_bytes(local_bundle_bytes)}")
        print(f"  {'bundle_unique_tensor_capacity':<32} {format_bytes(unique_bundle_bytes)}")

    cache_sections = collect_weight_cache_memory_sections(manager, seen_storages)
    print("weight_cache")
    cache_total_bytes = print_memory_sections(cache_sections, indent="  ")
    print(f"  {'weight_cache_tensor_capacity':<32} {format_bytes(cache_total_bytes)}")

    tensor_capacity_bytes = bundle_total_bytes + cache_total_bytes
    print(f"tensor_capacity_total          {format_bytes(tensor_capacity_bytes)}")
    print(f"rss_minus_tensor_capacity      {format_bytes(rss_bytes - tensor_capacity_bytes)}")


def run_shared_manager_benchmark(
    models: list[BenchmarkModel],
    iterations: int,
    max_active_bundles: int,
    device_policy: str,
    print_memory_breakdown: bool,
) -> BenchmarkResult:
    """测试共享 bundle manager 的交替加载耗时。"""
    manager = inference_cli.SharedTTSManager(
        max_active_bundles=max_active_bundles,
        max_cached_weight_entries=len(models) * 2,
    )
    runtimes = {
        model.name: create_runtime(model, device_policy)
        for model in models
    }
    sequence = get_model_sequence(models, iterations)
    samples: list[BenchmarkSample] = []
    scenario_name = f"shared_bundle_max_{max_active_bundles}"

    try:
        with open(os.devnull, "w") as null_output:
            for index, model in enumerate(sequence, start=1):
                runtime = runtimes[model.name]
                with contextlib.redirect_stdout(null_output):
                    duration = measure_duration_seconds(
                        lambda runtime=runtime: manager.ensure_loaded(runtime)
                    )
                samples.append(
                    BenchmarkSample(
                        scenario_name=scenario_name,
                        iteration=index,
                        model_name=model.name,
                        duration_seconds=duration,
                    )
                )
                clear_torch_cache()
        if print_memory_breakdown:
            print_shared_manager_memory_breakdown(manager, scenario_name)
        return BenchmarkResult(
            scenario_name=scenario_name,
            samples=samples,
            active_bundle_count=len(manager.bundles_by_signature),
        )
    finally:
        manager.unload_all()
        clear_torch_cache()


def create_tts_config(model: BenchmarkModel, device_policy: str) -> TTS_Config:
    """创建旧式完整加载基线使用的 TTS 配置。"""
    device = inference_cli.resolve_device(device_policy)
    config = TTS_Config(
        {
            "custom": {
                "device": str(device),
                "is_half": False,
                "version": model.model_version,
                "t2s_weights_path": str(model.gpt_path),
                "vits_weights_path": str(model.sovits_path),
                "bert_base_path": "pretrained_models/chinese-roberta-wwm-ext-large",
                "cnhuhbert_base_path": "pretrained_models/chinese-hubert-base",
            }
        }
    )
    config.device = device
    config.is_half = False
    config.update_version(model.model_version)
    config.t2s_weights_path = str(model.gpt_path)
    config.vits_weights_path = str(model.sovits_path)
    return config


def load_fresh_tts(model: BenchmarkModel, device_policy: str) -> None:
    """按旧式策略新建一个完整 TTS 实例并加载目标角色权重。"""
    config = create_tts_config(model, device_policy)
    tts = TTS(config, init_mode="shared_shell")
    try:
        tts.init_t2s_weights(str(model.gpt_path), save=False)
        tts.init_vits_weights(str(model.sovits_path), save=False)
    finally:
        tts.unload()
        del tts


def run_legacy_fresh_tts_benchmark(
    models: list[BenchmarkModel],
    iterations: int,
    device_policy: str,
) -> BenchmarkResult:
    """测试旧式每次新建完整 TTS 实例的交替加载耗时。"""
    sequence = get_model_sequence(models, iterations)
    samples: list[BenchmarkSample] = []
    scenario_name = "legacy_fresh_tts"

    with open(os.devnull, "w") as null_output:
        for index, model in enumerate(sequence, start=1):
            with contextlib.redirect_stdout(null_output):
                duration = measure_duration_seconds(
                    lambda model=model: load_fresh_tts(model, device_policy)
                )
            samples.append(
                BenchmarkSample(
                    scenario_name=scenario_name,
                    iteration=index,
                    model_name=model.name,
                    duration_seconds=duration,
                )
            )
            clear_torch_cache()
    return BenchmarkResult(scenario_name=scenario_name, samples=samples)


def format_seconds(seconds: float) -> str:
    """格式化秒数。"""
    return f"{seconds:8.3f}s"


def print_result(result: BenchmarkResult) -> None:
    """输出某个测试场景的详细结果与汇总。"""
    durations = [sample.duration_seconds for sample in result.samples]
    print(f"\n[{result.scenario_name}]")
    if result.active_bundle_count is not None:
        print(f"active_bundle_count = {result.active_bundle_count}")
    for sample in result.samples:
        print(
            f"{sample.iteration:02d}. "
            f"{sample.model_name:<8} "
            f"{format_seconds(sample.duration_seconds)}"
        )
    print(
        "summary: "
        f"total={format_seconds(sum(durations))}, "
        f"mean={format_seconds(statistics.mean(durations))}, "
        f"median={format_seconds(statistics.median(durations))}, "
        f"min={format_seconds(min(durations))}, "
        f"max={format_seconds(max(durations))}"
    )


def main() -> None:
    """运行 GPT-SoVITS 模型加载基准测试。"""
    args = parse_args()
    models = build_benchmark_models(args.model_pair)
    validate_model_files(models)

    print("GPT-SoVITS 模型加载 benchmark")
    print(f"iterations = {args.iterations}")
    print(f"device_policy = {args.device_policy}")
    print(f"model_pair = {args.model_pair}")
    print(
        "models = "
        + ", ".join(f"{model.name}:{model.model_version}" for model in models)
    )

    shared_one_result = run_shared_manager_benchmark(
        models=models,
        iterations=args.iterations,
        max_active_bundles=1,
        device_policy=args.device_policy,
        print_memory_breakdown=args.print_memory_breakdown,
    )
    print_result(shared_one_result)

    shared_two_result = run_shared_manager_benchmark(
        models=models,
        iterations=args.iterations,
        max_active_bundles=2,
        device_policy=args.device_policy,
        print_memory_breakdown=args.print_memory_breakdown,
    )
    print_result(shared_two_result)

    if args.include_legacy:
        legacy_result = run_legacy_fresh_tts_benchmark(
            models=models,
            iterations=args.iterations,
            device_policy=args.device_policy,
        )
        print_result(legacy_result)


if __name__ == "__main__":
    main()
