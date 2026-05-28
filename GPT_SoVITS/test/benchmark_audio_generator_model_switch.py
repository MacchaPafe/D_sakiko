from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from multiprocessing import Queue
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
GPT_SOVITS_DIR = SCRIPT_DIR.parent
REPO_ROOT = GPT_SOVITS_DIR.parent

sys.path.insert(0, str(GPT_SOVITS_DIR))

REF_AUDIO_LANGUAGE_LIST = [
    "中文",
    "英文",
    "日文",
    "粤语",
    "韩文",
    "中英混合",
    "日英混合",
    "粤英混合",
    "韩英混合",
    "多语种混合",
    "多语种混合(粤语)",
]


@dataclass
class BenchmarkCharacter:
    character_name: str
    GPT_model_path: str
    sovits_model_path: str
    gptsovits_ref_audio: str
    gptsovits_ref_audio_text: str
    gptsovits_ref_audio_lan: str


@dataclass(frozen=True)
class SwitchSample:
    iteration: int
    model_name: str
    duration_seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark AudioGenerate model switching between anon and tomori "
            "V2 Pro Plus GPT-SoVITS models."
        ),
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=10,
        help="Total model switch operations to measure.",
    )
    return parser.parse_args()


def read_reference_language(character_dir: Path) -> str:
    language_file = character_dir / "reference_audio_language.txt"
    try:
        for raw_line in language_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            return REF_AUDIO_LANGUAGE_LIST[int(line) - 1]
    except (OSError, ValueError, IndexError):
        pass
    return "日文"


def build_character(
    name: str,
    character_dir: Path,
    gpt_filename: str,
    sovits_filename: str,
    reference_audio_filename: str,
) -> BenchmarkCharacter:
    models_dir = character_dir / "GPT-SoVITS_models"
    return BenchmarkCharacter(
        character_name=name,
        GPT_model_path=str(models_dir / gpt_filename),
        sovits_model_path=str(models_dir / sovits_filename),
        gptsovits_ref_audio=str(character_dir / reference_audio_filename),
        gptsovits_ref_audio_text=str(character_dir / "reference_text.txt"),
        gptsovits_ref_audio_lan=read_reference_language(character_dir),
    )


def build_characters() -> list[BenchmarkCharacter]:
    return [
        build_character(
            name="anon_v2pp",
            character_dir=REPO_ROOT / "reference_audio" / "anon",
            gpt_filename="anon_v2pp.ckpt",
            sovits_filename="anon_v2pp.pth",
            reference_audio_filename="anon_X.wav_0008742720_0008876160.wav",
        ),
        build_character(
            name="sakiko_v4",
            character_dir=REPO_ROOT / "reference_audio" / "sakiko",
            gpt_filename="sakiko_v4-e10.ckpt",
            sovits_filename="sakiko_v4_e6_s390_l64.pth",
            reference_audio_filename="black_sakiko.wav",
        ),
    ]


def validate_character_files(characters: list[BenchmarkCharacter]) -> None:
    missing_paths = [
        path
        for character in characters
        for path in [
            character.GPT_model_path,
            character.sovits_model_path,
            character.gptsovits_ref_audio,
            character.gptsovits_ref_audio_text,
        ]
        if not Path(path).exists()
    ]
    if missing_paths:
        missing_text = "\n".join(missing_paths)
        raise FileNotFoundError(f"Missing benchmark files:\n{missing_text}")


def format_seconds(seconds: float) -> str:
    return f"{seconds:8.3f}s"


def print_result(samples: Sequence[SwitchSample], initial_load_seconds: float) -> None:
    print(f"initial_load = {format_seconds(initial_load_seconds)}")

    print("\n[model_switch]")
    for sample in samples:
        print(
            f"{sample.iteration:02d}. "
            f"{sample.model_name:<12} "
            f"{format_seconds(sample.duration_seconds)}"
        )

    durations = [sample.duration_seconds for sample in samples]
    print(
        "summary: "
        f"total={format_seconds(sum(durations))}, "
        f"mean={format_seconds(statistics.mean(durations))}, "
        f"median={format_seconds(statistics.median(durations))}, "
        f"min={format_seconds(min(durations))}, "
        f"max={format_seconds(max(durations))}"
    )


def shutdown_audio_generator(audio_generator: object) -> None:
    process = audio_generator.gptsovits_process
    if not process.is_alive():
        return

    audio_generator.to_gptsovits_com_queue.put("bye")
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)


def run_benchmark(iterations: int) -> tuple[list[SwitchSample], float]:
    if iterations < 1:
        raise ValueError("--iterations must be greater than 0")

    from audio_generator import AudioGenerate

    characters = build_characters()
    validate_character_files(characters)

    audio_generator = AudioGenerate()
    message_queue = Queue()
    samples: list[SwitchSample] = []

    try:
        print("AudioGenerate model switch benchmark")
        print(f"iterations = {iterations}")
        print(
            "models = "
            + ", ".join(character.character_name for character in characters)
        )
        print("Initializing AudioGenerate with anon_v2pp...")
        start_time = time.perf_counter()
        audio_generator.initialize(characters, message_queue)
        initial_load_seconds = time.perf_counter() - start_time

        for iteration in range(1, iterations + 1):
            current_character = characters[(iteration - 1) % len(characters)]
            start_time = time.perf_counter()
            audio_generator.generate_audio_for_character_sync(
                "テスト",
                current_character,
                True,
                "日英混合",
            )
            duration_seconds = time.perf_counter() - start_time
            samples.append(
                SwitchSample(
                    iteration=iteration,
                    model_name=current_character.character_name,
                    duration_seconds=duration_seconds,
                )
            )
            print(
                f"{iteration:02d}. switched to "
                f"{current_character.character_name}: {format_seconds(duration_seconds)}"
            )

        return samples, initial_load_seconds
    finally:
        shutdown_audio_generator(audio_generator)


def main() -> None:
    args = parse_args()
    samples, initial_load_seconds = run_benchmark(iterations=args.iterations)
    print_result(samples, initial_load_seconds)


if __name__ == "__main__":
    main()
