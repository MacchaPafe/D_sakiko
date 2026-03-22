"""场景切分与候选角色生成。"""

from __future__ import annotations

from pathlib import Path

from .characters import build_character_catalog, default_episode_prior_candidates, find_alias_hits
from .schemas import CandidateCharacter, SceneChunk, ScreenTextUnit, UtteranceUnit
from .subtitle_loader import extract_episode_number


DEFAULT_SCENE_GAP_MS = 12_000
DEFAULT_SCREEN_ATTACH_MARGIN_MS = 1_000
DEFAULT_CANDIDATE_LIMIT = 12


def _collect_scene_screen_texts(
    screen_texts: list[ScreenTextUnit],
    start_ms: int,
    end_ms: int,
    margin_ms: int = DEFAULT_SCREEN_ATTACH_MARGIN_MS,
) -> list[ScreenTextUnit]:
    attached: list[ScreenTextUnit] = []
    for item in screen_texts:
        if item.end_ms < start_ms - margin_ms:
            continue
        if item.start_ms > end_ms + margin_ms:
            continue
        attached.append(item)
    return attached


def _build_scene_summary_hint(screen_texts: list[ScreenTextUnit]) -> str | None:
    if not screen_texts:
        return None
    leading_texts = [item.text for item in screen_texts[:2] if item.text]
    if not leading_texts:
        return None
    return " / ".join(leading_texts)


def score_candidate_characters(
    utterances: list[UtteranceUnit],
    screen_texts: list[ScreenTextUnit],
    previous_scene: SceneChunk | None = None,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> list[CandidateCharacter]:
    """根据场景文本为候选角色打分。"""

    catalog = build_character_catalog()
    prior_display_names = default_episode_prior_candidates()

    utterance_texts = [text for utterance in utterances for text in (utterance.zh_text, utterance.jp_text) if text]
    screen_text_values = [item.text for item in screen_texts if item.text]
    previous_scene_names = {candidate.display_name for candidate in previous_scene.candidate_characters} if previous_scene else set()

    scored_candidates: list[CandidateCharacter] = []
    for candidate in catalog:
        score = 0
        notes: list[str] = []

        if candidate.display_name in prior_display_names:
            score += 2
            notes.append("命中剧集先验")

        utterance_hits = find_alias_hits(utterance_texts, candidate.aliases)
        if utterance_hits:
            score += 3
            notes.append(f"台词别名命中: {', '.join(utterance_hits[:3])}")

        screen_hits = find_alias_hits(screen_text_values, candidate.aliases)
        if screen_hits:
            score += 2
            notes.append(f"屏幕字命中: {', '.join(screen_hits[:3])}")

        if candidate.display_name in previous_scene_names:
            score += 1
            notes.append("继承上一场景候选")

        if score <= 0:
            continue

        scored_candidates.append(
            candidate.model_copy(update={"score": score, "notes": "；".join(notes) if notes else candidate.notes})
        )

    if not scored_candidates:
        fallback_names = sorted(prior_display_names)
        fallback = [candidate for candidate in catalog if candidate.display_name in fallback_names]
        return fallback[:limit]

    return sorted(
        scored_candidates,
        key=lambda item: (-item.score, item.display_name),
    )[:limit]


def segment_scenes(
    utterances: list[UtteranceUnit],
    screen_texts: list[ScreenTextUnit],
    anime_title: str,
    series_id: str,
    season_id: int,
    gap_ms: int = DEFAULT_SCENE_GAP_MS,
) -> list[SceneChunk]:
    """根据对白时间间隔切分场景，并附上候选角色。"""

    if not utterances:
        return []

    scenes: list[SceneChunk] = []
    episode = utterances[0].episode

    current_group: list[UtteranceUnit] = [utterances[0]]
    scene_index = 1

    def flush_group(previous_scene: SceneChunk | None = None) -> SceneChunk:
        nonlocal scene_index
        start_ms = current_group[0].start_ms
        end_ms = current_group[-1].end_ms
        attached_screen_texts = _collect_scene_screen_texts(screen_texts, start_ms, end_ms)
        candidate_characters = score_candidate_characters(current_group, attached_screen_texts, previous_scene=previous_scene)
        scene = SceneChunk(
            anime_title=anime_title,
            series_id=series_id,
            season_id=season_id,
            scene_id=f"ep{episode:02d}_s{scene_index:03d}",
            episode=episode,
            start_ms=start_ms,
            end_ms=end_ms,
            utterances=list(current_group),
            screen_texts=attached_screen_texts,
            candidate_characters=candidate_characters,
            scene_summary_hint=_build_scene_summary_hint(attached_screen_texts),
        )
        scene_index += 1
        return scene

    previous_utterance = utterances[0]
    for utterance in utterances[1:]:
        if utterance.start_ms - previous_utterance.end_ms > gap_ms:
            previous_scene = scenes[-1] if scenes else None
            scenes.append(flush_group(previous_scene=previous_scene))
            current_group = [utterance]
        else:
            current_group.append(utterance)
        previous_utterance = utterance

    previous_scene = scenes[-1] if scenes else None
    scenes.append(flush_group(previous_scene=previous_scene))
    return scenes


def segment_scenes_from_subtitle_path(
    subtitle_path: str | Path,
    utterances: list[UtteranceUnit],
    screen_texts: list[ScreenTextUnit],
    anime_title: str,
    series_id: str,
    season_id: int,
    gap_ms: int = DEFAULT_SCENE_GAP_MS,
) -> list[SceneChunk]:
    """基于字幕路径和已提取结果切分场景。"""

    _ = extract_episode_number(subtitle_path)
    return segment_scenes(
        utterances=utterances,
        screen_texts=screen_texts,
        anime_title=anime_title,
        series_id=series_id,
        season_id=season_id,
        gap_ms=gap_ms,
    )
