"""把逐帧 OCR 观测聚合为可审核字幕事件。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

from .models import (
    EventCandidateSummary,
    FrameObservation,
    OCRCandidate,
    OCRObservationsArtifact,
    OCRReviewArtifact,
    SubtitleReviewEvent,
)


IGNORED_COMPARISON_RE = re.compile(r"[\s，。！？、…,.!?：:；;·・—-]+")
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
TRAILING_NOISE_RE = re.compile(r"[ぁ-んァ-ンA-Za-z]+$")


@dataclass(slots=True)
class ObservationCluster:
    """保存暂未转换为正式事件的一组连续相似观测。"""

    observations: list[FrameObservation]

    def selected_candidates(self) -> list[OCRCandidate]:
        """返回聚类中全部已选文本候选。"""

        return [
            candidate
            for observation in self.observations
            if (candidate := observation.selected_candidate()) is not None
        ]


def normalize_comparison_text(text: str) -> str:
    """生成用于关联字幕事件的保守规范化文本。"""

    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\\N", "\n").replace("\\n", "\n")
    return IGNORED_COMPARISON_RE.sub("", normalized)


def levenshtein_distance(left: str, right: str) -> int:
    """计算两个字符串的 Levenshtein 编辑距离。"""

    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous: list[int] = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current: list[int] = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            substitution = previous[right_index - 1] + (left_char != right_char)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


def text_similarity(left: str, right: str) -> float:
    """返回归一化编辑相似度。"""

    normalized_left = normalize_comparison_text(left)
    normalized_right = normalize_comparison_text(right)
    maximum_length = max(len(normalized_left), len(normalized_right))
    if maximum_length == 0:
        return 1.0
    distance = levenshtein_distance(normalized_left, normalized_right)
    return max(0.0, 1.0 - distance / maximum_length)


def texts_are_similar(left: str, right: str) -> bool:
    """按文本长度采用不同门槛判断两个 OCR 结果是否属于同一字幕。"""

    normalized_left = normalize_comparison_text(left)
    normalized_right = normalize_comparison_text(right)
    left_lines = tuple(
        sorted(
            normalize_comparison_text(line)
            for line in left.replace("\\N", "\n").splitlines()
            if normalize_comparison_text(line)
        )
    )
    right_lines = tuple(
        sorted(
            normalize_comparison_text(line)
            for line in right.replace("\\N", "\n").splitlines()
            if normalize_comparison_text(line)
        )
    )
    if len(left_lines) > 1 and left_lines == right_lines:
        return True
    maximum_length = max(len(normalized_left), len(normalized_right))
    if normalized_left == normalized_right:
        return True
    distance = levenshtein_distance(normalized_left, normalized_right)
    if maximum_length <= 6:
        return distance <= 1
    threshold = 0.85 if maximum_length <= 15 else 0.90
    return text_similarity(normalized_left, normalized_right) >= threshold


def _cluster_observations(artifact: OCRObservationsArtifact) -> list[ObservationCluster]:
    """按时间、空白容忍和文本相似度形成连续字幕观测组。"""

    observations = sorted(
        (item for item in artifact.frames if item.phase == "coarse"),
        key=lambda item: item.timestamp_ms,
    )
    clusters: list[ObservationCluster] = []
    current: list[FrameObservation] = []
    last_nonempty_ms: int | None = None
    current_text = ""

    for observation in observations:
        candidate = observation.selected_candidate()
        if candidate is None or not candidate.text.strip():
            if (
                current
                and last_nonempty_ms is not None
                and observation.timestamp_ms - last_nonempty_ms > artifact.profile.empty_tolerance_ms
            ):
                clusters.append(ObservationCluster(observations=current))
                current = []
                current_text = ""
                last_nonempty_ms = None
            continue

        if not current:
            current = [observation]
            current_text = candidate.text
            last_nonempty_ms = observation.timestamp_ms
            continue

        gap_ms = observation.timestamp_ms - (last_nonempty_ms or observation.timestamp_ms)
        if gap_ms <= artifact.profile.empty_tolerance_ms and texts_are_similar(
            current_text, candidate.text
        ):
            current.append(observation)
            last_nonempty_ms = observation.timestamp_ms
            continue

        clusters.append(ObservationCluster(observations=current))
        current = [observation]
        current_text = candidate.text
        last_nonempty_ms = observation.timestamp_ms

    if current:
        clusters.append(ObservationCluster(observations=current))
    return clusters


def _candidate_summaries(cluster: ObservationCluster) -> list[EventCandidateSummary]:
    """计算事件内部每个实际文本候选的加权共识得分。"""

    candidates = cluster.selected_candidates()
    grouped: dict[str, list[OCRCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.text].append(candidate)

    summaries: list[EventCandidateSummary] = []
    total_count = max(1, len(candidates))
    for text, matches in grouped.items():
        mean_confidence = sum(item.confidence for item in matches) / len(matches)
        mean_similarity = sum(text_similarity(text, item.text) for item in candidates) / total_count
        occurrence_ratio = len(matches) / total_count
        stable_ratio = 1.0 if len(matches) >= 2 else 0.0
        consensus_score = (
            mean_similarity * 0.40
            + occurrence_ratio * 0.30
            + mean_confidence * 0.20
            + stable_ratio * 0.10
        )
        summaries.append(
            EventCandidateSummary(
                text=text,
                normalized_text=normalize_comparison_text(text),
                occurrences=len(matches),
                mean_confidence=round(mean_confidence, 6),
                consensus_score=round(consensus_score, 6),
                sources=sorted({item.source for item in matches}),
            )
        )
    return sorted(
        summaries,
        key=lambda item: (-item.consensus_score, -item.occurrences, item.text),
    )


def _event_reasons(
    cluster: ObservationCluster,
    summaries: list[EventCandidateSummary],
    selected_text: str,
) -> list[str]:
    """根据证据质量生成结构化复核原因。"""

    candidates = cluster.selected_candidates()
    reasons: list[str] = []
    if len(cluster.observations) == 1:
        reasons.append("single_sample")
    if candidates and min(item.confidence for item in candidates) < 0.95:
        reasons.append("low_confidence")
    if candidates and all(item.source.startswith("fallback_") for item in candidates):
        reasons.append("fallback_only")
    if len(summaries) >= 2 and summaries[0].consensus_score - summaries[1].consensus_score < 0.08:
        reasons.append("weak_vote_margin")
    if any("\n" in item.text for item in candidates):
        line_orders = {tuple(item.text.splitlines()) for item in candidates}
        normalized_line_sets = {tuple(sorted(lines)) for lines in line_orders}
        if len(line_orders) > 1 and len(normalized_line_sets) == 1:
            reasons.append("unstable_line_order")
    if TRAILING_NOISE_RE.search(selected_text) and CHINESE_RE.search(selected_text):
        reasons.append("suspicious_script_suffix")
    return reasons


def _cluster_to_event(
    cluster: ObservationCluster,
    event_index: int,
    episode: int,
    minimum_duration_ms: int,
    default_tail_ms: int,
    next_start_ms: int | None,
) -> SubtitleReviewEvent:
    """把一个观测聚类转换为可审核字幕事件。"""

    summaries = _candidate_summaries(cluster)
    if not summaries:
        raise ValueError("非空观测聚类缺少文本候选")
    winner = summaries[0]
    start_ms = cluster.observations[0].timestamp_ms
    proposed_end = cluster.observations[-1].timestamp_ms + default_tail_ms
    if next_start_ms is not None:
        proposed_end = min(proposed_end, next_start_ms)
    end_ms = max(start_ms + minimum_duration_ms, proposed_end)
    reasons = _event_reasons(cluster, summaries, winner.text)
    status = "pending" if reasons else "auto_accepted"
    representative = cluster.observations[len(cluster.observations) // 2].timestamp_ms
    return SubtitleReviewEvent(
        event_id=f"ocr_event:ep{episode:02d}:{event_index:04d}",
        start_ms=start_ms,
        end_ms=end_ms,
        text=winner.text,
        status=status,
        reasons=reasons,
        confidence=winner.mean_confidence,
        representative_timestamp_ms=representative,
        observation_timestamps_ms=[item.timestamp_ms for item in cluster.observations],
        candidates=summaries,
    )


def aggregate_observations(
    artifact: OCRObservationsArtifact,
    observations_path: str | Path,
) -> OCRReviewArtifact:
    """把完整 observations 产物聚合为初始 review 产物。"""

    clusters = _cluster_observations(artifact)
    events: list[SubtitleReviewEvent] = []
    for index, cluster in enumerate(clusters, start=1):
        next_start_ms = (
            clusters[index].observations[0].timestamp_ms if index < len(clusters) else None
        )
        events.append(
            _cluster_to_event(
                cluster=cluster,
                event_index=index,
                episode=artifact.episode,
                minimum_duration_ms=artifact.profile.minimum_event_duration_ms,
                default_tail_ms=artifact.profile.coarse_interval_ms,
                next_start_ms=next_start_ms,
            )
        )
    return OCRReviewArtifact(
        series_id=artifact.series_id,
        episode=artifact.episode,
        video=artifact.video,
        observations_path=str(Path(observations_path).resolve()),
        profile=artifact.profile,
        events=events,
    )
