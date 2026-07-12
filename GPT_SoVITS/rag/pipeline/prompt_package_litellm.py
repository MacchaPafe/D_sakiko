"""使用 LiteLLM 完成 Prompt Package 的模型请求阶段。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .llm_client import LiteLLMConfig, LiteLLMJsonClient
from .prompt_package import (
    task_prompt_path,
    task_response_path,
    validate_prompt_package,
)


@dataclass(frozen=True, slots=True)
class PromptPackageCompletionReport:
    """记录 Prompt Package 请求模型后的完成、跳过与失败数量。"""

    completed_task_ids: tuple[str, ...]
    skipped_task_ids: tuple[str, ...]
    errors: tuple[str, ...]


def complete_prompt_package_with_litellm(
    manifest_path: str | Path,
    llm_config: LiteLLMConfig,
    overwrite: bool = False,
    allow_stale: bool = False,
    stream: bool = False,
    status_callback: Callable[[str], None] | None = None,
    stream_callback: Callable[[str], None] | None = None,
) -> PromptPackageCompletionReport:
    """依次请求 Package 中尚未完成的任务并写入原始 response。"""

    manifest = validate_prompt_package(manifest_path, allow_stale=allow_stale)
    client = LiteLLMJsonClient(llm_config)
    completed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    total_tasks = len(manifest.tasks)
    for task_index, task in enumerate(manifest.tasks, start=1):
        response_path = task_response_path(manifest_path, task)
        if response_path.exists() and response_path.read_text(encoding="utf-8").strip() and not overwrite:
            skipped.append(task.task_id)
            _emit_status(
                status_callback,
                f"[{task_index}/{total_tasks}] {task.task_id}: response 已存在，跳过",
            )
            continue
        prompt = task_prompt_path(manifest_path, task).read_text(encoding="utf-8")
        _emit_status(
            status_callback,
            f"[{task_index}/{total_tasks}] {task.task_id}: 请求模型",
        )
        try:
            raw_content = client.complete_text(
                prompt,
                stream=stream,
                status_callback=status_callback,
                stream_callback=stream_callback,
            )
            response_path.parent.mkdir(parents=True, exist_ok=True)
            response_path.write_text(raw_content, encoding="utf-8")
            completed.append(task.task_id)
        except Exception as exc:
            errors.append(f"{task.task_id}: {type(exc).__name__}: {exc}")
    return PromptPackageCompletionReport(
        completed_task_ids=tuple(completed),
        skipped_task_ids=tuple(skipped),
        errors=tuple(errors),
    )


def _emit_status(callback: Callable[[str], None] | None, message: str) -> None:
    """在调用方提供回调时输出 Package 请求进度。"""

    if callback is not None:
        callback(message)
