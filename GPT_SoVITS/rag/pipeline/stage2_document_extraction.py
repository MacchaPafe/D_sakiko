"""第二阶段：从整合后的 scene 输入中抽取三张表候选。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError

from .llm_client import LiteLLMConfig, LiteLLMJsonClient
from .schemas import (
    SceneAnnotationPass2,
    Stage2AnnotationArtifact,
    Stage2InputArtifact,
    Stage2SceneAnnotationResult,
    Stage2SceneInput,
)


DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "extraction_pass.jinja"


def _scene_to_prompt_context(scene: Stage2SceneInput) -> dict[str, Any]:
    return scene.model_dump(mode="json")


def render_stage2_prompt(
    scene: Stage2SceneInput,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
) -> str:
    """将第二阶段 scene 输入渲染为 prompt。"""

    template_path = Path(template_path)
    environment = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    template = environment.get_template(template_path.name)
    return template.render(**_scene_to_prompt_context(scene))


def annotate_scene_stage2_with_llm(
    scene: Stage2SceneInput,
    llm_config: LiteLLMConfig,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    stream: bool = False,
    status_callback: Callable[[str], None] | None = None,
    stream_callback: Callable[[str], None] | None = None,
) -> SceneAnnotationPass2:
    """对单个场景执行第二阶段 LLM 抽取。"""

    prompt = render_stage2_prompt(scene, template_path=template_path)
    client = LiteLLMJsonClient(llm_config)
    payload = client.complete_json(
        prompt,
        stream=stream,
        status_callback=status_callback,
        stream_callback=stream_callback,
    )
    try:
        return SceneAnnotationPass2.model_validate(payload.parsed_payload)
    except ValidationError as exc:
        raise ValueError(f"LLM 返回的第二阶段结果不符合 schema: {exc}") from exc


def annotate_stage2_input_artifact(
    artifact: Stage2InputArtifact,
    llm_config: LiteLLMConfig,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    scene_ids: set[str] | None = None,
    max_scenes: int | None = None,
    prompts_dir: str | Path | None = None,
    stream: bool = False,
    status_callback: Callable[[str], None] | None = None,
    stream_callback: Callable[[str], None] | None = None,
) -> Stage2AnnotationArtifact:
    """对第二阶段输入批量执行 LLM 抽取。"""

    client = LiteLLMJsonClient(llm_config)
    template_path = Path(template_path)
    prompts_dir_path = Path(prompts_dir) if prompts_dir is not None else None
    if prompts_dir_path is not None:
        prompts_dir_path.mkdir(parents=True, exist_ok=True)

    selected_scenes = artifact.scenes
    if scene_ids:
        selected_scenes = [scene for scene in selected_scenes if scene.scene_id in scene_ids]
    if max_scenes is not None:
        selected_scenes = selected_scenes[:max_scenes]

    results: list[Stage2SceneAnnotationResult] = []
    total_scenes = len(selected_scenes)
    for scene_index, scene in enumerate(selected_scenes, start=1):
        _emit_status(
            status_callback,
            "[{}/{}] 正在处理第 {} 话的 {} 场景（第二阶段）".format(
                scene_index, total_scenes, scene.episode, scene.scene_id
            ),
        )
        _emit_status(
            status_callback,
            "[{}/{}] {}: 正在渲染第二阶段 prompt".format(
                scene_index, total_scenes, scene.scene_id
            ),
        )
        prompt = render_stage2_prompt(scene, template_path=template_path)
        _emit_status(
            status_callback,
            "[{}/{}] {}: 第二阶段 prompt 渲染完成（{} 字符）".format(
                scene_index, total_scenes, scene.scene_id, len(prompt)
            ),
        )
        prompt_path = None
        if prompts_dir_path is not None:
            scene_prompt_path = prompts_dir_path / f"{scene.scene_id}.txt"
            scene_prompt_path.write_text(prompt, encoding="utf-8")
            prompt_path = str(scene_prompt_path)
            _emit_status(
                status_callback,
                "[{}/{}] {}: 已写入第二阶段 prompt 文件 {}".format(
                    scene_index, total_scenes, scene.scene_id, scene_prompt_path
                ),
            )

        try:
            _emit_status(
                status_callback,
                "[{}/{}] {}: 开始请求第二阶段 LLM".format(scene_index, total_scenes, scene.scene_id),
            )
            if stream:
                _emit_status(
                    status_callback,
                    "[{}/{}] {}: 以下开始第二阶段流式输出".format(
                        scene_index, total_scenes, scene.scene_id
                    ),
                )
            response = client.complete_json(
                prompt,
                stream=stream,
                status_callback=(
                    None
                    if status_callback is None
                    else lambda message, scene_id=scene.scene_id, scene_index=scene_index, total_scenes=total_scenes: _emit_status(
                        status_callback,
                        "[{}/{}] {}: {}".format(scene_index, total_scenes, scene_id, message),
                    )
                ),
                stream_callback=stream_callback,
            )
            if stream:
                _emit_status(status_callback, "")
                _emit_status(
                    status_callback,
                    "[{}/{}] {}: 第二阶段流式输出结束".format(
                        scene_index, total_scenes, scene.scene_id
                    ),
                )
            _emit_status(
                status_callback,
                "[{}/{}] {}: 正在校验第二阶段返回结果".format(
                    scene_index, total_scenes, scene.scene_id
                ),
            )
            annotation = SceneAnnotationPass2.model_validate(response.parsed_payload)
            results.append(
                Stage2SceneAnnotationResult(
                    scene_id=scene.scene_id,
                    prompt_path=prompt_path,
                    raw_response_text=response.raw_content,
                    annotation=annotation,
                )
            )
            _emit_status(
                status_callback,
                "[{}/{}] {}: 第二阶段抽取成功".format(scene_index, total_scenes, scene.scene_id),
            )
        except Exception as exc:  # pragma: no cover - 真实 API 调用路径
            results.append(
                Stage2SceneAnnotationResult(
                    scene_id=scene.scene_id,
                    prompt_path=prompt_path,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            _emit_status(
                status_callback,
                "[{}/{}] {}: 第二阶段抽取失败：{}: {}".format(
                    scene_index, total_scenes, scene.scene_id, type(exc).__name__, exc
                ),
            )

    return Stage2AnnotationArtifact(
        metadata=artifact.metadata,
        model=llm_config.model,
        template_path=str(template_path),
        results=results,
    )


def save_stage2_annotation_artifact(artifact: Stage2AnnotationArtifact, output_path: str | Path) -> None:
    """保存第二阶段原始抽取结果。"""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_stage2_annotation_artifact(input_path: str | Path) -> Stage2AnnotationArtifact:
    """读取第二阶段原始抽取结果。"""

    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return Stage2AnnotationArtifact.model_validate(payload)


def _emit_status(status_callback: Callable[[str], None] | None, message: str) -> None:
    if status_callback is not None:
        status_callback(message)
