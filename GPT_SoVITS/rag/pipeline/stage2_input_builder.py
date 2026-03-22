"""将第一阶段结果整合为第二阶段输入。"""

from __future__ import annotations

import json
from pathlib import Path

from .schemas import (
    SceneAnnotationPass1,
    SceneChunk,
    SpeakerAnnotation,
    Stage1AnnotationArtifact,
    Stage1PreparedArtifact,
    Stage2InputArtifact,
    Stage2InputMetadata,
    Stage2SceneInput,
    Stage2ScreenText,
    Stage2SkippedScene,
    Stage2Utterance,
)
from .subtitle_loader import ms_to_timestamp


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def build_stage2_scene_input(scene: SceneChunk, annotation: SceneAnnotationPass1) -> Stage2SceneInput:
    """将单个 scene 与第一阶段标注合并为第二阶段输入。"""

    if annotation.scene_id != scene.scene_id:
        raise ValueError(f"scene_id 不匹配: scene={scene.scene_id}, annotation={annotation.scene_id}")
    if annotation.episode != scene.episode:
        raise ValueError(f"episode 不匹配: scene={scene.episode}, annotation={annotation.episode}")

    notes = list(annotation.global_notes)
    utterance_ids = {item.u_id for item in scene.utterances}
    annotation_map: dict[str, SpeakerAnnotation] = {}
    duplicate_annotation_ids: list[str] = []

    for item in annotation.utterance_annotations:
        if item.u_id in annotation_map:
            duplicate_annotation_ids.append(item.u_id)
        annotation_map[item.u_id] = item

    if duplicate_annotation_ids:
        duplicate_ids_text = ", ".join(sorted(set(duplicate_annotation_ids)))
        notes.append(f"重复的一阶段标注 u_id: {duplicate_ids_text}")

    extra_annotation_ids = [
        item.u_id for item in annotation.utterance_annotations if item.u_id not in utterance_ids
    ]
    if extra_annotation_ids:
        notes.append("一阶段结果中存在未匹配到台词的 u_id: {}".format(", ".join(extra_annotation_ids)))

    missing_annotation_ids: list[str] = []
    merged_utterances: list[Stage2Utterance] = []
    for utterance in scene.utterances:
        annotation_item = annotation_map.get(utterance.u_id)
        if annotation_item is None:
            missing_annotation_ids.append(utterance.u_id)
        merged_utterances.append(
            Stage2Utterance(
                u_id=utterance.u_id,
                start_ms=utterance.start_ms,
                end_ms=utterance.end_ms,
                start_text=ms_to_timestamp(utterance.start_ms),
                end_text=ms_to_timestamp(utterance.end_ms),
                speaker_name=None if annotation_item is None else annotation_item.speaker_name,
                addressee_candidates=(
                    [] if annotation_item is None else _unique_preserving_order(annotation_item.addressee_candidates)
                ),
                mentioned_characters=(
                    [] if annotation_item is None else _unique_preserving_order(annotation_item.mentioned_characters)
                ),
                emotion_hint=None if annotation_item is None else annotation_item.emotion_hint,
                zh_text=utterance.zh_text,
                jp_text=utterance.jp_text,
            )
        )

    if missing_annotation_ids:
        notes.append("以下台词缺少一阶段 speaker 标注: {}".format(", ".join(missing_annotation_ids)))

    return Stage2SceneInput(
        anime_title=scene.anime_title,
        series_id=scene.series_id,
        season_id=scene.season_id,
        episode=scene.episode,
        scene_id=scene.scene_id,
        start_ms=scene.start_ms,
        end_ms=scene.end_ms,
        scene_start_text=ms_to_timestamp(scene.start_ms),
        scene_end_text=ms_to_timestamp(scene.end_ms),
        scene_summary_hint=scene.scene_summary_hint,
        present_characters=_unique_preserving_order(annotation.present_characters),
        screen_texts=[
            Stage2ScreenText(
                s_id=item.s_id,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                start_text=ms_to_timestamp(item.start_ms),
                end_text=ms_to_timestamp(item.end_ms),
                kind=item.kind,
                text=item.text,
            )
            for item in scene.screen_texts
        ],
        utterances=merged_utterances,
        global_notes=notes,
    )


def build_stage2_input_artifact(
    prepared_artifact: Stage1PreparedArtifact,
    annotation_artifact: Stage1AnnotationArtifact,
    source_stage1_output_path: str | None = None,
) -> Stage2InputArtifact:
    """将第一阶段产物转换为第二阶段输入产物。"""

    scene_map = {scene.scene_id: scene for scene in prepared_artifact.scenes}
    scenes: list[Stage2SceneInput] = []
    skipped_scenes: list[Stage2SkippedScene] = []

    for result in annotation_artifact.results:
        scene = scene_map.get(result.scene_id)
        if scene is None:
            skipped_scenes.append(
                Stage2SkippedScene(scene_id=result.scene_id, error="prepared artifact 中不存在该 scene_id")
            )
            continue
        if result.annotation is None:
            skipped_scenes.append(
                Stage2SkippedScene(
                    scene_id=result.scene_id,
                    error=result.error or "第一阶段缺少 annotation，无法转换为第二阶段输入",
                )
            )
            continue

        try:
            scenes.append(build_stage2_scene_input(scene=scene, annotation=result.annotation))
        except Exception as exc:
            skipped_scenes.append(
                Stage2SkippedScene(scene_id=result.scene_id, error=f"{type(exc).__name__}: {exc}")
            )

    return Stage2InputArtifact(
        metadata=Stage2InputMetadata(
            subtitle_path=prepared_artifact.metadata.subtitle_path,
            anime_title=prepared_artifact.metadata.anime_title,
            series_id=prepared_artifact.metadata.series_id,
            season_id=prepared_artifact.metadata.season_id,
            canon_branch=prepared_artifact.metadata.canon_branch,
            episode=prepared_artifact.metadata.episode,
            scene_gap_ms=prepared_artifact.metadata.scene_gap_ms,
            source_stage1_model=annotation_artifact.model,
            source_stage1_template_path=annotation_artifact.template_path,
            source_stage1_output_path=source_stage1_output_path,
        ),
        scenes=scenes,
        skipped_scenes=skipped_scenes,
    )


def save_stage2_input_artifact(artifact: Stage2InputArtifact, output_path: str | Path) -> None:
    """保存第二阶段输入产物。"""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_stage2_input_artifact(input_path: str | Path) -> Stage2InputArtifact:
    """读取第二阶段输入产物。"""

    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return Stage2InputArtifact.model_validate(payload)


def derive_stage2_input_output_path(stage1_output_path: str | Path) -> Path:
    """根据第一阶段输出路径推导第二阶段输入路径。"""

    stage1_output_path = Path(stage1_output_path)
    if stage1_output_path.name.endswith("_pass1_raw.json"):
        return stage1_output_path.with_name(stage1_output_path.name.replace("_pass1_raw.json", "_stage2_input.json"))
    if stage1_output_path.suffix == ".json":
        return stage1_output_path.with_name(f"{stage1_output_path.stem}_stage2_input.json")
    return stage1_output_path.with_name(f"{stage1_output_path.name}_stage2_input.json")
