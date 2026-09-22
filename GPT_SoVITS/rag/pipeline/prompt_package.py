"""为可暂停的 LLM 标注流程提供 Prompt Package 深模块。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, Field


PromptStageKind = Literal[
    "stage1_speaker",
    "stage2_document",
    "stage2b_thought",
    "stage3_relation",
    "stage3_thought_link",
    "stage3_thought_aggregation",
]


@dataclass(frozen=True, slots=True)
class PreparedPrompt:
    """表示尚未写入磁盘的一条静态 Prompt 任务。"""

    task_id: str
    prompt: str
    context: dict[str, str]


class PromptSourceFingerprint(BaseModel):
    """记录 Prompt Package 输入文件的稳定指纹。"""

    path: str
    sha256: str


class PromptPackageTask(BaseModel):
    """记录一条 Prompt 与预期 response 文件。"""

    task_id: str
    prompt_file: str
    response_file: str
    prompt_sha256: str
    context: dict[str, str] = Field(default_factory=dict)


class PromptPackageManifest(BaseModel):
    """描述一批可由 API 或工作区 Codex 完成的 Prompt 任务。"""

    format_version: int = 1
    stage_kind: PromptStageKind
    source_files: list[PromptSourceFingerprint] = Field(default_factory=list)
    template_files: list[PromptSourceFingerprint] = Field(default_factory=list)
    parameters: dict[str, str] = Field(default_factory=dict)
    tasks: list[PromptPackageTask] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PromptResponseBundle:
    """保存从 Prompt Package 读取的原始 response 与完整性问题。"""

    responses: dict[str, str]
    errors: tuple[str, ...]


class PromptPackageError(ValueError):
    """表示 Prompt Package 已过期、缺失或结构不完整。"""


def create_prompt_package(
    output_dir: str | Path,
    stage_kind: PromptStageKind,
    prompts: list[PreparedPrompt],
    source_paths: list[str | Path],
    template_paths: list[str | Path],
    parameters: dict[str, str] | None = None,
) -> PromptPackageManifest:
    """写入静态 Prompt、response 目录和带哈希的 manifest。"""

    root = Path(output_dir).resolve()
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        raise PromptPackageError(
            f"输出目录已包含 Prompt Package: {manifest_path}。请使用新目录，避免旧 response 被误用。"
        )
    prompts_dir = root / "prompts"
    responses_dir = root / "responses"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)

    seen_task_ids: set[str] = set()
    tasks: list[PromptPackageTask] = []
    for prompt_index, prepared in enumerate(prompts, start=1):
        if prepared.task_id in seen_task_ids:
            raise PromptPackageError(f"发现重复 task_id: {prepared.task_id}")
        seen_task_ids.add(prepared.task_id)
        file_stem = _safe_task_file_stem(prepared.task_id, prompt_index)
        prompt_path = prompts_dir / f"{file_stem}.txt"
        response_path = responses_dir / f"{file_stem}.json"
        prompt_path.write_text(prepared.prompt, encoding="utf-8")
        tasks.append(
            PromptPackageTask(
                task_id=prepared.task_id,
                prompt_file=str(prompt_path.relative_to(root)),
                response_file=str(response_path.relative_to(root)),
                prompt_sha256=_sha256_text(prepared.prompt),
                context=prepared.context,
            )
        )

    manifest = PromptPackageManifest(
        stage_kind=stage_kind,
        source_files=[_fingerprint_file(path) for path in source_paths],
        template_files=[_fingerprint_file(path) for path in template_paths],
        parameters=dict(parameters or {}),
        tasks=tasks,
    )
    save_prompt_package_manifest(manifest, manifest_path)
    return manifest


def save_prompt_package_manifest(
    manifest: PromptPackageManifest,
    output_path: str | Path,
) -> None:
    """保存 Prompt Package manifest。"""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_prompt_package_manifest(input_path: str | Path) -> PromptPackageManifest:
    """读取并校验 Prompt Package manifest。"""

    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return PromptPackageManifest.model_validate(payload)


def read_prompt_package_responses(
    manifest_path: str | Path,
    allow_partial: bool = False,
    allow_stale: bool = False,
) -> PromptResponseBundle:
    """校验 Package 指纹并读取每条任务的原始 response。"""

    resolved_manifest_path = Path(manifest_path).resolve()
    root = resolved_manifest_path.parent
    manifest = load_prompt_package_manifest(resolved_manifest_path)
    errors = _validate_package_fingerprints(manifest, root)
    responses: dict[str, str] = {}
    expected_response_paths: set[Path] = set()
    for task in manifest.tasks:
        response_path = _resolve_package_member(root, task.response_file)
        expected_response_paths.add(response_path)
        if not response_path.exists():
            errors.append(f"缺少 response: {task.task_id} -> {response_path}")
            continue
        content = response_path.read_text(encoding="utf-8").strip()
        if not content:
            errors.append(f"response 为空: {task.task_id} -> {response_path}")
            continue
        responses[task.task_id] = content

    responses_dir = root / "responses"
    if responses_dir.exists():
        for response_path in responses_dir.glob("*.json"):
            if response_path.resolve() not in expected_response_paths:
                errors.append(f"发现 manifest 未声明的 response: {response_path}")

    stale_errors = [error for error in errors if error.startswith("文件指纹变化")]
    blocking_errors = [error for error in errors if error not in stale_errors]
    if stale_errors and not allow_stale:
        raise PromptPackageError("\n".join(stale_errors))
    if blocking_errors and not allow_partial:
        raise PromptPackageError("\n".join(blocking_errors))
    return PromptResponseBundle(responses=responses, errors=tuple(errors))


def validate_prompt_package(
    manifest_path: str | Path,
    allow_stale: bool = False,
) -> PromptPackageManifest:
    """在请求模型前校验 manifest、源文件、模板与 Prompt 指纹。"""

    resolved_manifest_path = Path(manifest_path).resolve()
    manifest = load_prompt_package_manifest(resolved_manifest_path)
    errors = _validate_package_fingerprints(manifest, resolved_manifest_path.parent)
    if errors and not allow_stale:
        raise PromptPackageError("\n".join(errors))
    return manifest


def parse_json_response(content: str) -> object:
    """解析严格 JSON，并兼容移除单层 Markdown 代码围栏。"""

    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return json.loads(stripped)


def task_prompt_path(manifest_path: str | Path, task: PromptPackageTask) -> Path:
    """返回某条任务在 Package 中的绝对 Prompt 路径。"""

    return _resolve_package_member(Path(manifest_path).resolve().parent, task.prompt_file)


def task_response_path(manifest_path: str | Path, task: PromptPackageTask) -> Path:
    """返回某条任务在 Package 中的绝对 response 路径。"""

    return _resolve_package_member(Path(manifest_path).resolve().parent, task.response_file)


def _validate_package_fingerprints(
    manifest: PromptPackageManifest,
    root: Path,
) -> list[str]:
    """检查源文件、模板和已写入 Prompt 是否发生变化。"""

    errors: list[str] = []
    for fingerprint in [*manifest.source_files, *manifest.template_files]:
        path = Path(fingerprint.path)
        if not path.exists():
            errors.append(f"文件指纹变化：文件已不存在 {path}")
            continue
        current_hash = _sha256_bytes(path.read_bytes())
        if current_hash != fingerprint.sha256:
            errors.append(f"文件指纹变化：{path}")
    for task in manifest.tasks:
        prompt_path = _resolve_package_member(root, task.prompt_file)
        if not prompt_path.exists():
            errors.append(f"文件指纹变化：Prompt 已不存在 {prompt_path}")
            continue
        if _sha256_text(prompt_path.read_text(encoding="utf-8")) != task.prompt_sha256:
            errors.append(f"文件指纹变化：Prompt 已修改 {prompt_path}")
    return errors


def _fingerprint_file(path_value: str | Path) -> PromptSourceFingerprint:
    """计算一个现有文件的绝对路径和 SHA-256。"""

    path = Path(path_value).resolve()
    if not path.is_file():
        raise PromptPackageError(f"无法为不存在的文件创建指纹: {path}")
    return PromptSourceFingerprint(path=str(path), sha256=_sha256_bytes(path.read_bytes()))


def _safe_task_file_stem(task_id: str, task_index: int) -> str:
    """生成兼顾可读性和跨平台安全性的任务文件名。"""

    readable = re.sub(r"[^0-9A-Za-z_.-]+", "_", task_id).strip("._-")
    if not readable:
        readable = f"task_{task_index:04d}"
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:8]
    return f"{readable[:80]}__{digest}"


def _resolve_package_member(root: Path, relative_path: str) -> Path:
    """解析 Package 成员路径，并阻止绝对路径或目录穿越。"""

    if Path(relative_path).is_absolute():
        raise PromptPackageError(f"Prompt Package 成员必须使用相对路径: {relative_path}")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PromptPackageError(f"Prompt Package 成员越出目录: {relative_path}") from exc
    return candidate


def _sha256_text(value: str) -> str:
    """计算 UTF-8 文本的 SHA-256。"""

    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    """计算字节内容的 SHA-256。"""

    return hashlib.sha256(value).hexdigest()
