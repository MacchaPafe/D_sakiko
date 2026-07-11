"""Stage 2B：按场景抽取 Event Fact 与角色观点更新。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError

from .llm_client import LiteLLMConfig, LiteLLMJsonClient
from .schemas import (
    CharacterThoughtUpdateCandidate,
    EventFactCandidate,
    SceneThoughtExtractionPass2B,
    Stage2AnnotationArtifact,
    Stage2BAnnotationArtifact,
    Stage2BSceneAnnotationResult,
    Stage2InputArtifact,
    Stage2SceneInput,
    StoryEventCandidate,
)


DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "thought_extraction_pass.jinja"


@dataclass(frozen=True, slots=True)
class _Stage2BSceneRequest:
    """保存一次 Stage 2B 场景请求所需的不可变数据。"""

    scene: Stage2SceneInput
    story_events: tuple[StoryEventCandidate, ...]
    scene_index: int
    total_scenes: int
    prompts: tuple[str, ...]
    prompt_paths: tuple[str, ...]


def render_stage2b_prompt(
    scene: Stage2SceneInput,
    story_events: list[StoryEventCandidate],
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
) -> str:
    """渲染单个场景的 Stage 2B prompt。"""

    resolved_template_path = Path(template_path)
    environment = Environment(
        loader=FileSystemLoader(str(resolved_template_path.parent)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    template = environment.get_template(resolved_template_path.name)
    return template.render(
        scene=scene.model_dump(mode="json"),
        story_events=[event.model_dump(mode="json") for event in story_events],
    )


def _build_story_event_map(
    artifact: Stage2AnnotationArtifact,
) -> dict[str, list[StoryEventCandidate]]:
    """按 scene_id 汇总 Stage 2A 成功抽取的 Story Event 候选。"""

    result: dict[str, list[StoryEventCandidate]] = {}
    for scene_result in artifact.results:
        if scene_result.annotation is None:
            result.setdefault(scene_result.scene_id, [])
            continue
        result[scene_result.scene_id] = list(scene_result.annotation.story_events)
    return result


def annotate_scene_stage2b_with_llm(
    scene: Stage2SceneInput,
    story_events: list[StoryEventCandidate],
    llm_config: LiteLLMConfig,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    stream: bool = False,
    status_callback: Callable[[str], None] | None = None,
    stream_callback: Callable[[str], None] | None = None,
) -> SceneThoughtExtractionPass2B:
    """对单个场景执行 Stage 2B LLM 抽取并校验结果。"""

    prompt = render_stage2b_prompt(scene, story_events, template_path=template_path)
    response = LiteLLMJsonClient(llm_config).complete_json(
        prompt,
        stream=stream,
        status_callback=status_callback,
        stream_callback=stream_callback,
    )
    try:
        annotation = SceneThoughtExtractionPass2B.model_validate(response.parsed_payload)
    except ValidationError as exc:
        raise ValueError(f"LLM 返回的 Stage 2B 结果不符合 schema: {exc}") from exc
    if annotation.scene_id != scene.scene_id:
        raise ValueError(f"Stage 2B scene_id 不匹配: expected={scene.scene_id}, actual={annotation.scene_id}")
    return annotation


def annotate_stage2b_artifact(
    input_artifact: Stage2InputArtifact,
    stage2a_artifact: Stage2AnnotationArtifact,
    llm_config: LiteLLMConfig,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    source_stage2a_output_path: str | None = None,
    scene_ids: set[str] | None = None,
    max_scenes: int | None = None,
    prompts_dir: str | Path | None = None,
    stream: bool = False,
    status_callback: Callable[[str], None] | None = None,
    stream_callback: Callable[[str], None] | None = None,
    max_prompt_chars: int | None = None,
    window_utterances: int = 40,
    window_overlap: int = 8,
) -> Stage2BAnnotationArtifact:
    """批量运行 Stage 2B，并保持输入 scene 的原始顺序。"""

    resolved_template_path = Path(template_path)
    if max_prompt_chars is not None and max_prompt_chars < 1:
        raise ValueError("max_prompt_chars 必须大于等于 1")
    if window_utterances < 1:
        raise ValueError("window_utterances 必须大于等于 1")
    if window_overlap < 0 or window_overlap >= window_utterances:
        raise ValueError("window_overlap 必须大于等于 0 且小于 window_utterances")
    prompts_path = Path(prompts_dir) if prompts_dir is not None else None
    if prompts_path is not None:
        prompts_path.mkdir(parents=True, exist_ok=True)

    selected_scenes = input_artifact.scenes
    if scene_ids:
        selected_scenes = [scene for scene in selected_scenes if scene.scene_id in scene_ids]
    if max_scenes is not None:
        selected_scenes = selected_scenes[:max_scenes]

    story_event_map = _build_story_event_map(stage2a_artifact)
    requests: list[_Stage2BSceneRequest] = []
    total_scenes = len(selected_scenes)
    for scene_index, scene in enumerate(selected_scenes, start=1):
        story_events = story_event_map.get(scene.scene_id, [])
        prompts = _render_stage2b_prompt_windows(
            scene,
            story_events,
            resolved_template_path,
            max_prompt_chars,
            window_utterances,
            window_overlap,
        )
        prompt_paths: list[str] = []
        if prompts_path is not None:
            for window_index, prompt in enumerate(prompts, start=1):
                suffix = "" if len(prompts) == 1 else f"_w{window_index:03d}"
                scene_prompt_path = prompts_path / f"{scene.scene_id}{suffix}.txt"
                scene_prompt_path.write_text(prompt, encoding="utf-8")
                prompt_paths.append(str(scene_prompt_path))
        _emit_status(
            status_callback,
            f"[{scene_index}/{total_scenes}] {scene.scene_id}: Stage 2B prompt 已准备"
            f"（{len(prompts)} 个窗口，共 {sum(len(prompt) for prompt in prompts)} 字符）",
        )
        requests.append(
            _Stage2BSceneRequest(
                scene=scene,
                story_events=tuple(story_events),
                scene_index=scene_index,
                total_scenes=total_scenes,
                prompts=tuple(prompts),
                prompt_paths=tuple(prompt_paths),
            )
        )

    results_by_index: dict[int, Stage2BSceneAnnotationResult] = {}
    if stream or len(requests) <= 1:
        for request in requests:
            results_by_index[request.scene_index] = _annotate_stage2b_request(
                request,
                llm_config,
                stream,
                status_callback,
                stream_callback,
            )
    else:
        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            future_map = {
                executor.submit(
                    _annotate_stage2b_request,
                    request,
                    llm_config,
                    False,
                    status_callback,
                    stream_callback,
                ): request
                for request in requests
            }
            for future in as_completed(future_map):
                request = future_map[future]
                results_by_index[request.scene_index] = future.result()

    return Stage2BAnnotationArtifact(
        metadata=input_artifact.metadata,
        source_stage2a_model=stage2a_artifact.model,
        source_stage2a_output_path=source_stage2a_output_path,
        model=llm_config.model,
        template_path=str(resolved_template_path),
        results=[results_by_index[index] for index in range(1, total_scenes + 1)],
    )


def _annotate_stage2b_request(
    request: _Stage2BSceneRequest,
    llm_config: LiteLLMConfig,
    stream: bool,
    status_callback: Callable[[str], None] | None,
    stream_callback: Callable[[str], None] | None,
) -> Stage2BSceneAnnotationResult:
    """执行一个已渲染的 Stage 2B 请求并封装错误。"""

    try:
        annotations: list[SceneThoughtExtractionPass2B] = []
        raw_contents: list[str] = []
        for prompt in request.prompts:
            response = LiteLLMJsonClient(llm_config).complete_json(
                prompt,
                stream=stream,
                status_callback=status_callback,
                stream_callback=stream_callback,
            )
            annotation = SceneThoughtExtractionPass2B.model_validate(response.parsed_payload)
            if annotation.scene_id != request.scene.scene_id:
                raise ValueError(
                    f"Stage 2B scene_id 不匹配: expected={request.scene.scene_id}, actual={annotation.scene_id}"
                )
            annotations.append(annotation)
            raw_contents.append(response.raw_content)
        annotation = _merge_window_annotations(request.scene.scene_id, annotations)
        _emit_status(
            status_callback,
            f"[{request.scene_index}/{request.total_scenes}] {request.scene.scene_id}: Stage 2B 抽取成功",
        )
        return Stage2BSceneAnnotationResult(
            scene_id=request.scene.scene_id,
            prompt_path=request.prompt_paths[0] if request.prompt_paths else None,
            prompt_paths=list(request.prompt_paths),
            raw_response_text="\n\n--- Stage 2B window ---\n\n".join(raw_contents),
            annotation=annotation,
        )
    except Exception as exc:  # pragma: no cover - 真实 API 调用路径
        _emit_status(
            status_callback,
            f"[{request.scene_index}/{request.total_scenes}] {request.scene.scene_id}: "
            f"Stage 2B 抽取失败：{type(exc).__name__}: {exc}",
        )
        return Stage2BSceneAnnotationResult(
            scene_id=request.scene.scene_id,
            prompt_path=request.prompt_paths[0] if request.prompt_paths else None,
            prompt_paths=list(request.prompt_paths),
            error=f"{type(exc).__name__}: {exc}",
        )


def _render_stage2b_prompt_windows(
    scene: Stage2SceneInput,
    story_events: list[StoryEventCandidate],
    template_path: str | Path,
    max_prompt_chars: int | None,
    window_utterances: int,
    window_overlap: int,
) -> list[str]:
    """优先渲染完整场景，超过限制时改用带重叠的台词窗口。"""

    full_prompt = render_stage2b_prompt(scene, story_events, template_path=template_path)
    if max_prompt_chars is None or len(full_prompt) <= max_prompt_chars:
        return [full_prompt]
    step = window_utterances - window_overlap
    prompts: list[str] = []
    for start in range(0, len(scene.utterances), step):
        utterances = scene.utterances[start : start + window_utterances]
        if not utterances:
            break
        window_scene = scene.model_copy(
            update={
                "utterances": utterances,
                "start_ms": utterances[0].start_ms,
                "end_ms": utterances[-1].end_ms,
                "scene_start_text": utterances[0].start_text,
                "scene_end_text": utterances[-1].end_text,
            }
        )
        prompts.append(render_stage2b_prompt(window_scene, story_events, template_path=template_path))
        if start + window_utterances >= len(scene.utterances):
            break
    return prompts or [full_prompt]


def _merge_window_annotations(
    scene_id: str,
    annotations: list[SceneThoughtExtractionPass2B],
) -> SceneThoughtExtractionPass2B:
    """合并重叠窗口结果，重新编号本地 ID 并修复观点到事实的引用。"""

    merged_facts: list[EventFactCandidate] = []
    fact_keys: dict[tuple[str, tuple[str, ...]], str] = {}
    fact_id_maps: list[dict[str, str]] = []
    for annotation in annotations:
        local_map: dict[str, str] = {}
        for fact in annotation.event_facts:
            key = (fact.fact_text.strip(), tuple(sorted(fact.evidence_u_ids)))
            merged_id = fact_keys.get(key)
            if merged_id is None:
                merged_id = f"fact_{len(merged_facts) + 1:02d}"
                fact_keys[key] = merged_id
                merged_facts.append(fact.model_copy(update={"fact_local_id": merged_id}))
            local_map[fact.fact_local_id] = merged_id
        fact_id_maps.append(local_map)

    merged_updates: list[CharacterThoughtUpdateCandidate] = []
    update_keys: set[tuple[str, str, str, tuple[str, ...]]] = set()
    for annotation, fact_id_map in zip(annotations, fact_id_maps):
        for update in annotation.character_thought_updates:
            key = (
                update.character_name,
                update.thought_text.strip(),
                update.epistemic_status,
                tuple(sorted(update.evidence_u_ids)),
            )
            if key in update_keys:
                continue
            update_keys.add(key)
            merged_updates.append(
                update.model_copy(
                    update={
                        "update_local_id": f"thought_{len(merged_updates) + 1:02d}",
                        "about_fact_local_id": fact_id_map.get(
                            update.about_fact_local_id,
                            update.about_fact_local_id,
                        ),
                    }
                )
            )
    return SceneThoughtExtractionPass2B(
        scene_id=scene_id,
        event_facts=merged_facts,
        character_thought_updates=merged_updates,
    )


def save_stage2b_annotation_artifact(
    artifact: Stage2BAnnotationArtifact,
    output_path: str | Path,
) -> None:
    """保存 Stage 2B 标注产物。"""

    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_stage2b_annotation_artifact(input_path: str | Path) -> Stage2BAnnotationArtifact:
    """读取并校验 Stage 2B 标注产物。"""

    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return Stage2BAnnotationArtifact.model_validate(payload)


def _emit_status(callback: Callable[[str], None] | None, message: str) -> None:
    """在提供回调时发送 Stage 2B 状态消息。"""

    if callback is not None:
        callback(message)
