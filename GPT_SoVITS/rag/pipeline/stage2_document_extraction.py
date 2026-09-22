"""第二阶段：从整合后的 scene 输入中抽取三张表候选。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Callable

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError

from .llm_client import LiteLLMConfig, LiteLLMJsonClient
from .prompt_package import (
    PreparedPrompt,
    PromptPackageError,
    PromptPackageManifest,
    create_prompt_package,
    load_prompt_package_manifest,
    parse_json_response,
    read_prompt_package_responses,
    task_prompt_path,
)
from .schemas import (
    SceneAnnotationPass2,
    Stage2AnnotationArtifact,
    Stage2InputArtifact,
    Stage2SceneAnnotationResult,
    Stage2SceneInput,
)
from .stage2_input_builder import load_stage2_input_artifact


DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "extraction_pass.jinja"


_DATACLASS_KWARGS = {"slots": True} if sys.version_info >= (3, 10) else {}


@dataclass(**_DATACLASS_KWARGS)
class _Stage2SceneRequest:
    scene: Stage2SceneInput
    scene_index: int
    total_scenes: int
    prompt: str
    prompt_path: str | None


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

    template_path = Path(template_path)
    prompts_dir_path = Path(prompts_dir) if prompts_dir is not None else None
    if prompts_dir_path is not None:
        prompts_dir_path.mkdir(parents=True, exist_ok=True)

    selected_scenes = artifact.scenes
    if scene_ids:
        selected_scenes = [scene for scene in selected_scenes if scene.scene_id in scene_ids]
    if max_scenes is not None:
        selected_scenes = selected_scenes[:max_scenes]

    total_scenes = len(selected_scenes)
    scene_requests: list[_Stage2SceneRequest] = []
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

        scene_requests.append(
            _Stage2SceneRequest(
                scene=scene,
                scene_index=scene_index,
                total_scenes=total_scenes,
                prompt=prompt,
                prompt_path=prompt_path,
            )
        )

    if stream and len(scene_requests) > 1:
        _emit_status(
            status_callback,
            "检测到开启了流式输出；为避免多个场景的增量文本互相交错，当前保持串行请求。",
        )

    results_by_index: dict[int, Stage2SceneAnnotationResult] = {}
    if stream or len(scene_requests) <= 1:
        for request in scene_requests:
            results_by_index[request.scene_index] = _annotate_stage2_scene_request(
                request=request,
                llm_config=llm_config,
                stream=stream,
                status_callback=status_callback,
                stream_callback=stream_callback,
            )
    else:
        _emit_status(
            status_callback,
            "已完成 {} 个场景的第二阶段 prompt 准备，开始并行请求 LLM。".format(total_scenes),
        )
        with ThreadPoolExecutor(max_workers=total_scenes) as executor:
            future_to_request = {
                executor.submit(
                    _annotate_stage2_scene_request,
                    request=request,
                    llm_config=llm_config,
                    stream=False,
                    status_callback=status_callback,
                    stream_callback=stream_callback,
                ): request
                for request in scene_requests
            }
            for future in as_completed(future_to_request):
                request = future_to_request[future]
                results_by_index[request.scene_index] = future.result()

    results = [results_by_index[index] for index in range(1, total_scenes + 1)]

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


def prepare_stage2_prompt_package(
    artifact: Stage2InputArtifact,
    input_path: str | Path,
    output_dir: str | Path,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    scene_ids: set[str] | None = None,
    max_scenes: int | None = None,
) -> PromptPackageManifest:
    """为 Stage 2A 文档抽取生成可离线完成的静态任务包。"""

    resolved_template_path = Path(template_path).resolve()
    selected_scenes = artifact.scenes
    if scene_ids:
        selected_scenes = [scene for scene in selected_scenes if scene.scene_id in scene_ids]
    if max_scenes is not None:
        selected_scenes = selected_scenes[:max_scenes]
    prompts = [
        PreparedPrompt(
            task_id=scene.scene_id,
            prompt=render_stage2_prompt(scene, resolved_template_path),
            context={"scene_id": scene.scene_id, "episode": str(scene.episode)},
        )
        for scene in selected_scenes
    ]
    return create_prompt_package(
        output_dir=output_dir,
        stage_kind="stage2_document",
        prompts=prompts,
        source_paths=[input_path],
        template_paths=[resolved_template_path],
        parameters={
            "input_path": str(Path(input_path).resolve()),
            "template_path": str(resolved_template_path),
        },
    )


def assemble_stage2_prompt_package(
    manifest_path: str | Path,
    model_label: str = "codex-workspace",
    allow_partial: bool = False,
    allow_stale: bool = False,
) -> Stage2AnnotationArtifact:
    """校验离线 response 并组装 Stage 2A 标注产物。"""

    manifest = load_prompt_package_manifest(manifest_path)
    if manifest.stage_kind != "stage2_document":
        raise PromptPackageError("manifest 不是 Stage 2A Document 任务包。")
    input_path = manifest.parameters.get("input_path")
    if not input_path:
        raise PromptPackageError("Stage 2A manifest 缺少 input_path。")
    artifact = load_stage2_input_artifact(input_path)
    scene_ids = {scene.scene_id for scene in artifact.scenes}
    response_bundle = read_prompt_package_responses(
        manifest_path,
        allow_partial=allow_partial,
        allow_stale=allow_stale,
    )
    results: list[Stage2SceneAnnotationResult] = []
    for task in manifest.tasks:
        scene_id = task.context.get("scene_id", task.task_id)
        prompt_path = str(task_prompt_path(manifest_path, task))
        raw_response = response_bundle.responses.get(task.task_id)
        if scene_id not in scene_ids:
            results.append(
                Stage2SceneAnnotationResult(
                    scene_id=scene_id,
                    prompt_path=prompt_path,
                    error="manifest 引用了 Stage 2 Input 中不存在的场景。",
                )
            )
            continue
        if raw_response is None:
            results.append(
                Stage2SceneAnnotationResult(
                    scene_id=scene_id,
                    prompt_path=prompt_path,
                    error="缺少 response。",
                )
            )
            continue
        try:
            annotation = SceneAnnotationPass2.model_validate(parse_json_response(raw_response))
            if annotation.scene_id != scene_id:
                raise ValueError(
                    f"Stage 2 scene_id 不匹配: expected={scene_id}, actual={annotation.scene_id}"
                )
            results.append(
                Stage2SceneAnnotationResult(
                    scene_id=scene_id,
                    prompt_path=prompt_path,
                    raw_response_text=raw_response,
                    annotation=annotation,
                )
            )
        except Exception as exc:
            results.append(
                Stage2SceneAnnotationResult(
                    scene_id=scene_id,
                    prompt_path=prompt_path,
                    raw_response_text=raw_response,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return Stage2AnnotationArtifact(
        metadata=artifact.metadata,
        model=model_label,
        template_path=manifest.parameters.get("template_path", ""),
        results=results,
    )


def _annotate_stage2_scene_request(
    request: _Stage2SceneRequest,
    llm_config: LiteLLMConfig,
    stream: bool,
    status_callback: Callable[[str], None] | None,
    stream_callback: Callable[[str], None] | None,
) -> Stage2SceneAnnotationResult:
    scene = request.scene
    scene_index = request.scene_index
    total_scenes = request.total_scenes
    client = LiteLLMJsonClient(llm_config)

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
            request.prompt,
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
        _emit_status(
            status_callback,
            "[{}/{}] {}: 第二阶段抽取成功".format(scene_index, total_scenes, scene.scene_id),
        )
        return Stage2SceneAnnotationResult(
            scene_id=scene.scene_id,
            prompt_path=request.prompt_path,
            raw_response_text=response.raw_content,
            annotation=annotation,
        )
    except Exception as exc:  # pragma: no cover - 真实 API 调用路径
        _emit_status(
            status_callback,
            "[{}/{}] {}: 第二阶段抽取失败：{}: {}".format(
                scene_index, total_scenes, scene.scene_id, type(exc).__name__, exc
            ),
        )
        return Stage2SceneAnnotationResult(
            scene_id=scene.scene_id,
            prompt_path=request.prompt_path,
            error=f"{type(exc).__name__}: {exc}",
        )


def _emit_status(status_callback: Callable[[str], None] | None, message: str) -> None:
    if status_callback is not None:
        status_callback(message)
