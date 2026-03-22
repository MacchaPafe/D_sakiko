"""第一阶段：字幕到 speaker 标注的编排逻辑。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError

from rag.models import CanonBranch, SeasonId, SeriesId

from .llm_client import LiteLLMConfig, LiteLLMJsonClient
from .scene_segmenter import segment_scenes_from_subtitle_path
from .schemas import (
    SceneAnnotationPass1,
    SceneChunk,
    Stage1AnnotationArtifact,
    Stage1Metadata,
    Stage1PreparedArtifact,
    Stage1SceneAnnotationResult,
)
from .subtitle_loader import (
    build_screen_text_units,
    build_utterance_units,
    extract_episode_number,
    load_relevant_subtitle_lines,
    ms_to_timestamp,
)


DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "speaker_pass.jinja"
DEFAULT_SCENE_GAP_MS = 12_000


def prepare_stage1_scenes(
    subtitle_path: str | Path,
    anime_title: str = "It's MyGO!!!!!",
    series_id: SeriesId = SeriesId.ITS_MYGO,
    season_id: SeasonId = SeasonId.THREE,
    canon_branch: CanonBranch = CanonBranch.MAIN,
    scene_gap_ms: int = DEFAULT_SCENE_GAP_MS,
) -> list[SceneChunk]:
    """从字幕文件准备第一阶段输入场景。"""

    _ = canon_branch  # 第一阶段当前不直接写入 prompt，但保留参数接口。
    episode = extract_episode_number(subtitle_path)
    lines = load_relevant_subtitle_lines(subtitle_path)
    utterances = build_utterance_units(lines, episode=episode)
    screen_texts = build_screen_text_units(lines, episode=episode)
    return segment_scenes_from_subtitle_path(
        subtitle_path=subtitle_path,
        utterances=utterances,
        screen_texts=screen_texts,
        anime_title=anime_title,
        series_id=series_id.value,
        season_id=season_id.value,
        gap_ms=scene_gap_ms,
    )


def prepare_stage1_artifact(
    subtitle_path: str | Path,
    anime_title: str = "It's MyGO!!!!!",
    series_id: SeriesId = SeriesId.ITS_MYGO,
    season_id: SeasonId = SeasonId.THREE,
    canon_branch: CanonBranch = CanonBranch.MAIN,
    scene_gap_ms: int = DEFAULT_SCENE_GAP_MS,
) -> Stage1PreparedArtifact:
    """构建第一阶段预处理产物。"""

    subtitle_path = Path(subtitle_path)
    scenes = prepare_stage1_scenes(
        subtitle_path=subtitle_path,
        anime_title=anime_title,
        series_id=series_id,
        season_id=season_id,
        canon_branch=canon_branch,
        scene_gap_ms=scene_gap_ms,
    )
    episode = extract_episode_number(subtitle_path)
    return Stage1PreparedArtifact(
        metadata=Stage1Metadata(
            subtitle_path=str(subtitle_path),
            anime_title=anime_title,
            series_id=series_id.value,
            season_id=season_id.value,
            canon_branch=canon_branch.value,
            episode=episode,
            scene_gap_ms=scene_gap_ms,
        ),
        scenes=scenes,
    )


def save_stage1_prepared_artifact(artifact: Stage1PreparedArtifact, output_path: str | Path) -> None:
    """保存第一阶段预处理产物。"""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_stage1_prepared_artifact(input_path: str | Path) -> Stage1PreparedArtifact:
    """读取第一阶段预处理产物。"""

    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return Stage1PreparedArtifact.model_validate(payload)


def _scene_to_prompt_context(scene: SceneChunk) -> dict[str, Any]:
    return {
        "anime_title": scene.anime_title,
        "series_id": scene.series_id,
        "season_id": scene.season_id,
        "episode": scene.episode,
        "scene_id": scene.scene_id,
        "scene_start_text": ms_to_timestamp(scene.start_ms),
        "scene_end_text": ms_to_timestamp(scene.end_ms),
        "scene_summary_hint": scene.scene_summary_hint,
        "candidate_characters": [candidate.model_dump() for candidate in scene.candidate_characters],
        "screen_texts": [
            {
                **item.model_dump(),
                "start_text": ms_to_timestamp(item.start_ms),
                "end_text": ms_to_timestamp(item.end_ms),
            }
            for item in scene.screen_texts
        ],
        "utterances": [
            {
                **item.model_dump(),
                "start_text": ms_to_timestamp(item.start_ms),
                "end_text": ms_to_timestamp(item.end_ms),
            }
            for item in scene.utterances
        ],
    }


def render_stage1_prompt(scene: SceneChunk, template_path: str | Path = DEFAULT_TEMPLATE_PATH) -> str:
    """将场景渲染为第一阶段 prompt。"""

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


def annotate_scene_with_llm(
    scene: SceneChunk,
    llm_config: LiteLLMConfig,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    stream: bool = False,
    status_callback: Callable[[str], None] | None = None,
    stream_callback: Callable[[str], None] | None = None,
) -> SceneAnnotationPass1:
    """对单个场景执行第一阶段 LLM 标注。"""

    prompt = render_stage1_prompt(scene, template_path=template_path)
    client = LiteLLMJsonClient(llm_config)
    payload = client.complete_json(
        prompt,
        stream=stream,
        status_callback=status_callback,
        stream_callback=stream_callback,
    )
    try:
        return SceneAnnotationPass1.model_validate(payload.parsed_payload)
    except ValidationError as exc:
        raise ValueError(f"LLM 返回的第一阶段结果不符合 schema: {exc}") from exc


def annotate_prepared_stage1_artifact(
    artifact: Stage1PreparedArtifact,
    llm_config: LiteLLMConfig,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    scene_ids: set[str] | None = None,
    max_scenes: int | None = None,
    prompts_dir: str | Path | None = None,
    stream: bool = False,
    status_callback: Callable[[str], None] | None = None,
    stream_callback: Callable[[str], None] | None = None,
) -> Stage1AnnotationArtifact:
    """对预处理后的场景批量执行第一阶段标注。"""

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

    results: list[Stage1SceneAnnotationResult] = []
    total_scenes = len(selected_scenes)
    for scene_index, scene in enumerate(selected_scenes, start=1):
        _emit_status(
            status_callback,
            "[{}/{}] 正在处理第 {} 话的 {} 场景".format(scene_index, total_scenes, scene.episode, scene.scene_id),
        )
        _emit_status(
            status_callback,
            "[{}/{}] {}: 正在渲染 prompt".format(scene_index, total_scenes, scene.scene_id),
        )
        prompt = render_stage1_prompt(scene, template_path=template_path)
        _emit_status(
            status_callback,
            "[{}/{}] {}: prompt 渲染完成（{} 字符）".format(scene_index, total_scenes, scene.scene_id, len(prompt)),
        )
        prompt_path = None
        if prompts_dir_path is not None:
            scene_prompt_path = prompts_dir_path / f"{scene.scene_id}.txt"
            scene_prompt_path.write_text(prompt, encoding="utf-8")
            prompt_path = str(scene_prompt_path)
            _emit_status(
                status_callback,
                "[{}/{}] {}: 已写入 prompt 文件 {}".format(
                    scene_index, total_scenes, scene.scene_id, scene_prompt_path
                ),
            )

        try:
            _emit_status(
                status_callback,
                "[{}/{}] {}: 开始请求 LLM".format(scene_index, total_scenes, scene.scene_id),
            )
            if stream:
                _emit_status(
                    status_callback,
                    "[{}/{}] {}: 以下开始流式输出".format(scene_index, total_scenes, scene.scene_id),
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
                    "[{}/{}] {}: 流式输出结束".format(scene_index, total_scenes, scene.scene_id),
                )
            _emit_status(
                status_callback,
                "[{}/{}] {}: 正在校验返回结果".format(scene_index, total_scenes, scene.scene_id),
            )
            annotation = SceneAnnotationPass1.model_validate(response.parsed_payload)
            results.append(
                Stage1SceneAnnotationResult(
                    scene_id=scene.scene_id,
                    prompt_path=prompt_path,
                    raw_response_text=response.raw_content,
                    annotation=annotation,
                )
            )
            _emit_status(
                status_callback,
                "[{}/{}] {}: 标注成功".format(scene_index, total_scenes, scene.scene_id),
            )
        except Exception as exc:  # pragma: no cover - 真实 API 调用路径
            results.append(
                Stage1SceneAnnotationResult(
                    scene_id=scene.scene_id,
                    prompt_path=prompt_path,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            _emit_status(
                status_callback,
                "[{}/{}] {}: 标注失败：{}: {}".format(
                    scene_index, total_scenes, scene.scene_id, type(exc).__name__, exc
                ),
            )

    return Stage1AnnotationArtifact(
        metadata=artifact.metadata,
        model=llm_config.model,
        template_path=str(template_path),
        results=results,
    )


def save_stage1_annotation_artifact(artifact: Stage1AnnotationArtifact, output_path: str | Path) -> None:
    """保存第一阶段原始标注结果。"""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_stage1_annotation_artifact(input_path: str | Path) -> Stage1AnnotationArtifact:
    """读取第一阶段原始标注结果。"""

    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return Stage1AnnotationArtifact.model_validate(payload)


def default_status_printer(message: str) -> None:
    """默认状态输出。"""

    print(message, flush=True)


def default_stream_printer(chunk_text: str) -> None:
    """默认流式文本输出。"""

    print(chunk_text, end="", flush=True)


def _emit_status(status_callback: Callable[[str], None] | None, message: str) -> None:
    if status_callback is not None:
        status_callback(message)
