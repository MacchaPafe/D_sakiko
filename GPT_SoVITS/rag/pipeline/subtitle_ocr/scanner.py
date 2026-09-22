"""扫描视频中的内嵌中文字幕并生成可审核中间产物。"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import shutil
import time
from collections.abc import Iterator
from typing import Literal, Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray
from rapidocr import RapidOCR

from .aggregation import aggregate_observations, normalize_comparison_text, texts_are_similar
from .models import (
    FrameObservation,
    OCRBox,
    OCRCandidate,
    OCRObservationsArtifact,
    OCRReviewArtifact,
    OCRRunStatistics,
    ObservationSource,
    ObservationPhase,
    RelativeRegion,
    SCHEMA_VERSION,
    SubtitleOCRProfile,
    VideoIdentity,
    artifact_paths,
    default_artifact_stem,
    utc_now_text,
)
from .profiles import DEFAULT_PROFILE_ID, load_profile, region_pixels
from .storage import atomic_write_model, load_observations, load_review


CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
FixedRegionMode = Literal["fixed", "fallback"]


class RapidOCRResult(Protocol):
    """描述扫描器使用的 RapidOCR 输出字段。"""

    boxes: NDArray[np.float32] | None
    txts: tuple[str, ...] | None
    scores: tuple[float, ...] | None


class VideoCaptureReader(Protocol):
    """描述边界顺序解码所需的 OpenCV 视频读取接口。"""

    def set(self, property_id: int, value: float) -> bool:
        """设置视频读取位置。"""

    def read(self) -> tuple[bool, NDArray[np.uint8]]:
        """读取并解码当前帧。"""

    def grab(self) -> bool:
        """抓取下一帧但暂不取回像素。"""

    def retrieve(self) -> tuple[bool, NDArray[np.uint8]]:
        """取回最近一次抓取的帧像素。"""


def _package_version(package_name: str) -> str:
    """读取包版本，无法读取时返回 unknown。"""

    try:
        return version(package_name)
    except PackageNotFoundError:
        return "unknown"


def runtime_versions() -> dict[str, str]:
    """返回影响 OCR 结果的运行时版本快照。"""

    return {
        "rapidocr": _package_version("rapidocr"),
        "opencv-python": _package_version("opencv-python"),
        "onnxruntime": _package_version("onnxruntime"),
        "numpy": _package_version("numpy"),
    }


def inspect_video(video_path: str | Path) -> VideoIdentity:
    """读取视频的轻量身份信息和媒体属性。"""

    path = Path(video_path).resolve()
    stat = path.stat()
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"无法打开视频: {path}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_ms = int(frame_count * 1000.0 / fps) if fps > 0 else 0
        return VideoIdentity(
            path=str(path),
            size_bytes=stat.st_size,
            modified_ns=stat.st_mtime_ns,
            duration_ms=duration_ms,
            width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=fps,
        )
    finally:
        capture.release()


def _video_identity_matches(left: VideoIdentity, right: VideoIdentity) -> bool:
    """判断两个轻量视频身份是否足以继续复用扫描缓存。"""

    return (
        Path(left.path).resolve() == Path(right.path).resolve()
        and left.size_bytes == right.size_bytes
        and left.modified_ns == right.modified_ns
        and left.duration_ms == right.duration_ms
        and left.width == right.width
        and left.height == right.height
    )


def _crop(frame: NDArray[np.uint8], region: RelativeRegion) -> NDArray[np.uint8]:
    """按相对区域裁剪视频帧。"""

    height, width = frame.shape[:2]
    left, top, right, bottom = region_pixels(region, width, height)
    return frame[top:bottom, left:right]


def _box_from_array(box: NDArray[np.float32]) -> OCRBox:
    """把 RapidOCR 四点字框转换为轴对齐矩形。"""

    return OCRBox(
        left=float(np.min(box[:, 0])),
        top=float(np.min(box[:, 1])),
        right=float(np.max(box[:, 0])),
        bottom=float(np.max(box[:, 1])),
    )


def _chinese_count(text: str) -> int:
    """返回文本包含的汉字数量。"""

    return len(CHINESE_RE.findall(text))


def _primary_candidates(
    result: RapidOCRResult,
    crop_height: int,
    profile: SubtitleOCRProfile,
) -> tuple[list[OCRCandidate], int | None]:
    """过滤主检测字框并构造一条按空间顺序排列的字幕候选。"""

    boxes = result.boxes if result.boxes is not None else np.empty((0, 4, 2), dtype=np.float32)
    texts = result.txts or ()
    scores = result.scores or ()
    candidates: list[OCRCandidate] = []
    accepted_lines: list[tuple[float, str, float, OCRBox]] = []
    minimum_height = crop_height * profile.minimum_box_height_ratio

    for raw_box, text, score in zip(boxes, texts, scores, strict=True):
        box = _box_from_array(raw_box)
        rejection_reason: str | None = None
        if box.height < minimum_height:
            rejection_reason = "box_too_small"
        elif _chinese_count(text) < profile.minimum_chinese_characters:
            rejection_reason = "insufficient_chinese"
        if rejection_reason is None:
            accepted_lines.append((box.vertical_center, text.strip(), float(score), box))
            continue
        candidates.append(
            OCRCandidate(
                source="primary",
                text=text.strip(),
                normalized_text=normalize_comparison_text(text),
                confidence=float(score),
                boxes=[box],
                accepted=False,
                rejection_reason=rejection_reason,
            )
        )

    if not accepted_lines:
        return candidates, None
    accepted_lines.sort(key=lambda item: item[0])
    combined_text = "\n".join(item[1] for item in accepted_lines)
    combined = OCRCandidate(
        source="primary",
        text=combined_text,
        normalized_text=normalize_comparison_text(combined_text),
        confidence=min(item[2] for item in accepted_lines),
        boxes=[item[3] for item in accepted_lines],
    )
    candidates.insert(0, combined)
    return candidates, 0


def _recognize_region(
    engine: RapidOCR,
    frame: NDArray[np.uint8],
    region: RelativeRegion,
) -> tuple[str, float]:
    """在固定区域内关闭文本检测并直接识别整行。"""

    image = _crop(frame, region)
    result = cast(
        RapidOCRResult,
        engine(image, use_det=False, use_cls=False, use_rec=True),
    )
    texts = result.txts or ()
    scores = result.scores or ()
    if not texts or not scores:
        return "", 0.0
    return texts[0].strip(), float(scores[0])


def _select_fixed_region_candidate(candidates: list[OCRCandidate]) -> int | None:
    """选择固定区域假设，并在文本近似时偏向更短的无噪声结果。"""

    if not candidates:
        return None
    if len(candidates) == 2 and texts_are_similar(candidates[0].text, candidates[1].text):
        return min(
            range(len(candidates)),
            key=lambda index: (
                len(candidates[index].normalized_text),
                -candidates[index].confidence,
            ),
        )
    return max(
        range(len(candidates)),
        key=lambda index: (
            candidates[index].confidence,
            _chinese_count(candidates[index].text),
            -len(candidates[index].text),
        ),
    )


def _fixed_region_candidates(
    engine: RapidOCR,
    frame: NDArray[np.uint8],
    profile: SubtitleOCRProfile,
    source_mode: FixedRegionMode,
) -> tuple[list[OCRCandidate], int | None]:
    """构造固定单行和双行区域的快速或回退识别假设。"""

    single_text, single_score = _recognize_region(engine, frame, profile.single_line)
    top_text, top_score = _recognize_region(engine, frame, profile.double_top)
    bottom_text, bottom_score = _recognize_region(engine, frame, profile.double_bottom)
    candidates: list[OCRCandidate] = []
    single_source: ObservationSource = (
        "fixed_single" if source_mode == "fixed" else "fallback_single"
    )
    double_source: ObservationSource = (
        "fixed_double" if source_mode == "fixed" else "fallback_double"
    )

    if _chinese_count(single_text) >= profile.minimum_chinese_characters:
        candidates.append(
            OCRCandidate(
                source=single_source,
                text=single_text,
                normalized_text=normalize_comparison_text(single_text),
                confidence=single_score,
            )
        )
    double_lines = [text for text in (top_text, bottom_text) if text]
    double_text = "\n".join(double_lines)
    if _chinese_count(double_text) >= profile.minimum_chinese_characters:
        double_scores = [
            score
            for text, score in ((top_text, top_score), (bottom_text, bottom_score))
            if text
        ]
        candidates.append(
            OCRCandidate(
                source=double_source,
                text=double_text,
                normalized_text=normalize_comparison_text(double_text),
                confidence=min(double_scores),
            )
        )
    return candidates, _select_fixed_region_candidate(candidates)


def observe_frame(
    engine: RapidOCR,
    frame: NDArray[np.uint8],
    timestamp_ms: int,
    profile: SubtitleOCRProfile,
    phase: ObservationPhase = "coarse",
) -> FrameObservation:
    """优先运行固定区域快速识别，并在必要时运行完整检测。"""

    started_at = time.perf_counter()
    fixed_candidates, fixed_selected_index = _fixed_region_candidates(
        engine,
        frame,
        profile,
        "fixed",
    )
    fixed_selected = (
        None
        if fixed_selected_index is None
        else fixed_candidates[fixed_selected_index]
    )
    if (
        fixed_selected is not None
        and fixed_selected.confidence >= profile.fixed_region_minimum_confidence
    ):
        return FrameObservation(
            timestamp_ms=timestamp_ms,
            phase=phase,
            candidates=fixed_candidates,
            selected_candidate_index=fixed_selected_index,
            ocr_seconds=time.perf_counter() - started_at,
        )

    subtitle_crop = _crop(frame, profile.subtitle_band)
    primary_result = cast(
        RapidOCRResult,
        engine(
            subtitle_crop,
            use_det=True,
            use_cls=True,
            use_rec=True,
        ),
    )
    candidates, selected_index = _primary_candidates(
        primary_result,
        subtitle_crop.shape[0],
        profile,
    )
    if selected_index is None and fixed_selected_index is not None:
        fallback_candidates = [
            candidate.model_copy(
                update={
                    "source": (
                        "fallback_single"
                        if candidate.source == "fixed_single"
                        else "fallback_double"
                    )
                }
            )
            for candidate in fixed_candidates
        ]
        offset = len(candidates)
        candidates.extend(fallback_candidates)
        selected_index = offset + fixed_selected_index
    return FrameObservation(
        timestamp_ms=timestamp_ms,
        phase=phase,
        candidates=candidates,
        selected_candidate_index=selected_index,
        ocr_seconds=time.perf_counter() - started_at,
    )


def _empty_statistics() -> OCRRunStatistics:
    """创建全零扫描统计。"""

    return OCRRunStatistics(
        processed_frames=0,
        primary_frames=0,
        fallback_frames=0,
        empty_frames=0,
        decode_seconds=0.0,
        primary_ocr_seconds=0.0,
        fallback_ocr_seconds=0.0,
        boundary_frames=0,
        boundary_ocr_seconds=0.0,
        boundary_decode_seconds=0.0,
    )


def _updated_statistics(
    previous: OCRRunStatistics,
    observation: FrameObservation,
    decode_seconds: float,
) -> OCRRunStatistics:
    """累加一条观测到扫描统计。"""

    selected = observation.selected_candidate()
    primary = selected is not None and not selected.source.startswith("fallback_")
    fallback = selected is not None and selected.source.startswith("fallback_")
    return OCRRunStatistics(
        processed_frames=previous.processed_frames + 1,
        primary_frames=previous.primary_frames + int(primary),
        fallback_frames=previous.fallback_frames + int(fallback),
        empty_frames=previous.empty_frames + int(selected is None),
        decode_seconds=previous.decode_seconds + decode_seconds,
        primary_ocr_seconds=previous.primary_ocr_seconds
        + (observation.ocr_seconds if primary or selected is None else 0.0),
        fallback_ocr_seconds=previous.fallback_ocr_seconds
        + (observation.ocr_seconds if fallback else 0.0),
        boundary_frames=previous.boundary_frames,
        boundary_ocr_seconds=previous.boundary_ocr_seconds,
        boundary_decode_seconds=previous.boundary_decode_seconds,
    )


def scan_video(
    video_path: str | Path,
    series_id: str,
    episode: int,
    profile: SubtitleOCRProfile,
    observations_path: str | Path,
    *,
    resume: bool = False,
) -> OCRObservationsArtifact:
    """顺序解码视频并按固定时间间隔保存 OCR 观测。"""

    identity = inspect_video(video_path)
    target = Path(observations_path)
    if resume and target.exists():
        artifact = load_observations(target)
        if not _video_identity_matches(artifact.video, identity):
            raise ValueError("视频身份已变化，不能继续使用现有 OCR 断点")
        if artifact.profile != profile:
            raise ValueError("布局或扫描配置已变化，不能继续使用现有 OCR 断点")
        artifact.schema_version = SCHEMA_VERSION
    else:
        artifact = OCRObservationsArtifact(
            series_id=series_id,
            episode=episode,
            video=identity,
            profile=profile,
            runtime_versions=runtime_versions(),
            statistics=_empty_statistics(),
        )
        atomic_write_model(artifact, target)

    capture = cv2.VideoCapture(identity.path)
    if not capture.isOpened():
        raise RuntimeError(f"无法打开视频: {identity.path}")
    engine = RapidOCR()
    interval_ms = profile.coarse_interval_ms
    next_sample_ms = (
        artifact.completed_until_ms + interval_ms if artifact.frames else interval_ms
    )
    next_checkpoint_ms = next_sample_ms + profile.checkpoint_interval_ms
    frame_index = int(next_sample_ms * identity.fps / 1000.0) if identity.fps > 0 else 0
    capture.set(cv2.CAP_PROP_POS_FRAMES, float(frame_index))
    pending_decode_seconds = 0.0

    try:
        while next_sample_ms < identity.duration_ms:
            decode_started = time.perf_counter()
            succeeded = capture.grab()
            pending_decode_seconds += time.perf_counter() - decode_started
            if not succeeded:
                break
            timestamp_ms = int(frame_index * 1000.0 / identity.fps) if identity.fps > 0 else 0
            frame_index += 1
            if timestamp_ms < next_sample_ms:
                continue
            retrieve_started = time.perf_counter()
            retrieved, frame = capture.retrieve()
            pending_decode_seconds += time.perf_counter() - retrieve_started
            if not retrieved:
                next_sample_ms += interval_ms
                continue
            observation = observe_frame(engine, frame, next_sample_ms, profile)
            artifact.frames.append(observation)
            artifact.completed_until_ms = next_sample_ms
            artifact.updated_at = utc_now_text()
            artifact.statistics = _updated_statistics(
                artifact.statistics,
                observation,
                pending_decode_seconds,
            )
            pending_decode_seconds = 0.0
            if next_sample_ms >= next_checkpoint_ms:
                atomic_write_model(artifact, target)
                next_checkpoint_ms += profile.checkpoint_interval_ms
            next_sample_ms += interval_ms
    finally:
        capture.release()

    artifact.scan_complete = artifact.completed_until_ms >= identity.duration_ms - interval_ms * 2
    artifact.updated_at = utc_now_text()
    atomic_write_model(artifact, target)
    return artifact


def _resize_for_evidence(image: NDArray[np.uint8], maximum_edge: int = 1280) -> NDArray[np.uint8]:
    """按最长边限制缩小证据图，不放大小图。"""

    height, width = image.shape[:2]
    scale = min(1.0, maximum_edge / max(height, width))
    if scale >= 1.0:
        return image
    return cv2.resize(
        image,
        (max(1, int(width * scale)), max(1, int(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def write_pending_evidence(
    review: OCRReviewArtifact,
    review_path: str | Path,
) -> OCRReviewArtifact:
    """为待复核事件生成受限大小的完整帧和字幕裁剪证据。"""

    target = Path(review_path)
    asset_root = target.parent / f"{target.stem.removesuffix('.review')}.review-assets" / "evidence"
    capture = cv2.VideoCapture(review.video.path)
    if not capture.isOpened():
        return review
    try:
        for event in review.events:
            if event.status != "pending":
                continue
            capture.set(cv2.CAP_PROP_POS_MSEC, float(event.representative_timestamp_ms))
            succeeded, frame = capture.read()
            if not succeeded:
                continue
            asset_root.mkdir(parents=True, exist_ok=True)
            full_path = asset_root / f"{event.event_id.replace(':', '_')}_full.jpg"
            crop_path = asset_root / f"{event.event_id.replace(':', '_')}_crop.jpg"
            cv2.imwrite(
                str(full_path),
                _resize_for_evidence(frame),
                [int(cv2.IMWRITE_JPEG_QUALITY), 80],
            )
            cv2.imwrite(
                str(crop_path),
                _resize_for_evidence(_crop(frame, review.profile.subtitle_band)),
                [int(cv2.IMWRITE_JPEG_QUALITY), 80],
            )
            event.evidence_full_frame = str(full_path.relative_to(target.parent))
            event.evidence_crop = str(crop_path.relative_to(target.parent))
    finally:
        capture.release()
    return review


def _boundary_observation(
    engine: RapidOCR,
    frame: NDArray[np.uint8],
    timestamp_ms: int,
    profile: SubtitleOCRProfile,
    multiline: bool,
) -> FrameObservation:
    """以低成本固定区域识别构造边界回查观测。"""

    started_at = time.perf_counter()
    if multiline:
        top_text, top_score = _recognize_region(engine, frame, profile.double_top)
        bottom_text, bottom_score = _recognize_region(engine, frame, profile.double_bottom)
        lines = [text for text in (top_text, bottom_text) if text]
        scores = [
            score
            for text, score in ((top_text, top_score), (bottom_text, bottom_score))
            if text
        ]
        text = "\n".join(lines)
        score = min(scores) if scores else 0.0
        source = "fallback_double"
    else:
        text, score = _recognize_region(engine, frame, profile.single_line)
        source = "fallback_single"
    candidates: list[OCRCandidate] = []
    selected_index: int | None = None
    if _chinese_count(text) >= profile.minimum_chinese_characters:
        candidates.append(
            OCRCandidate(
                source=source,
                text=text,
                normalized_text=normalize_comparison_text(text),
                confidence=score,
            )
        )
        selected_index = 0
    return FrameObservation(
        timestamp_ms=timestamp_ms,
        phase="boundary",
        candidates=candidates,
        selected_candidate_index=selected_index,
        ocr_seconds=time.perf_counter() - started_at,
    )


def _iter_boundary_frames(
    capture: VideoCaptureReader,
    timestamps_ms: list[int],
    fps: float,
    seek_threshold_ms: int,
) -> Iterator[tuple[int, NDArray[np.uint8], float]]:
    """按时间顺序读取边界帧，短间隔顺序抓帧，长间隔才重新定位。"""

    current_frame_index: int | None = None
    previous_timestamp_ms: int | None = None
    for timestamp_ms in sorted(timestamps_ms):
        target_frame_index = (
            int(round(timestamp_ms * fps / 1000.0))
            if fps > 0
            else 0
        )
        should_seek = (
            current_frame_index is None
            or previous_timestamp_ms is None
            or timestamp_ms - previous_timestamp_ms > seek_threshold_ms
            or target_frame_index <= current_frame_index
        )
        decode_started = time.perf_counter()
        if should_seek or fps <= 0:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(timestamp_ms))
            succeeded, frame = capture.read()
        else:
            succeeded = True
            for _ in range(target_frame_index - current_frame_index):
                if not capture.grab():
                    succeeded = False
                    break
            if succeeded:
                succeeded, frame = capture.retrieve()
        decode_seconds = time.perf_counter() - decode_started
        if not succeeded:
            current_frame_index = None
            previous_timestamp_ms = None
            continue

        current_frame_index = target_frame_index
        previous_timestamp_ms = timestamp_ms
        yield timestamp_ms, frame, decode_seconds


def refine_review_boundaries(
    observations: OCRObservationsArtifact,
    review: OCRReviewArtifact,
    observations_path: str | Path,
) -> tuple[OCRObservationsArtifact, OCRReviewArtifact]:
    """在粗边界附近以 100ms 固定区域识别细化事件时间。"""

    capture = cv2.VideoCapture(review.video.path)
    if not capture.isOpened():
        for event in review.events:
            if "boundary_uncertain" not in event.reasons:
                event.reasons.append("boundary_uncertain")
                event.status = "pending"
        return observations, review
    engine = RapidOCR()
    boundary_cache: dict[tuple[int, bool], FrameObservation] = {}
    event_windows: dict[str, tuple[bool, tuple[tuple[int, ...], tuple[int, ...]]]] = {}
    requested_modes: dict[int, set[bool]] = {}
    existing_timestamps = {
        (observation.timestamp_ms, observation.phase)
        for observation in observations.frames
    }

    for event in review.events:
        multiline = "\n" in event.text
        windows = (
            tuple(
                range(
                    max(0, event.start_ms - review.profile.boundary_window_ms),
                    event.start_ms + review.profile.boundary_window_ms + 1,
                    review.profile.boundary_step_ms,
                )
            ),
            tuple(
                range(
                    max(0, event.end_ms - review.profile.boundary_window_ms),
                    min(
                        review.video.duration_ms,
                        event.end_ms + review.profile.boundary_window_ms,
                    )
                    + 1,
                    review.profile.boundary_step_ms,
                )
            ),
        )
        event_windows[event.event_id] = (multiline, windows)
        for timestamp_ms in (*windows[0], *windows[1]):
            requested_modes.setdefault(timestamp_ms, set()).add(multiline)

    boundary_decode_seconds = 0.0
    try:
        for timestamp_ms, frame, decode_seconds in _iter_boundary_frames(
            capture,
            list(requested_modes),
            review.video.fps,
            review.profile.boundary_seek_threshold_ms,
        ):
            boundary_decode_seconds += decode_seconds
            for multiline in sorted(requested_modes[timestamp_ms]):
                observation = _boundary_observation(
                    engine,
                    frame,
                    timestamp_ms,
                    review.profile,
                    multiline,
                )
                boundary_cache[(timestamp_ms, multiline)] = observation
                if (timestamp_ms, "boundary") not in existing_timestamps:
                    observations.frames.append(observation)
                    existing_timestamps.add((timestamp_ms, "boundary"))

        for event in review.events:
            multiline, windows = event_windows[event.event_id]
            matched_windows: list[list[int]] = []
            for window in windows:
                matched: list[int] = []
                for timestamp_ms in window:
                    observation = boundary_cache.get((timestamp_ms, multiline))
                    if observation is None:
                        continue
                    candidate = observation.selected_candidate()
                    if candidate is not None and texts_are_similar(candidate.text, event.text):
                        matched.append(timestamp_ms)
                matched_windows.append(matched)
            start_matches, end_matches = matched_windows
            if start_matches:
                event.start_ms = min(start_matches)
            if end_matches:
                event.end_ms = max(end_matches) + review.profile.boundary_step_ms
            if not start_matches or not end_matches:
                if "boundary_uncertain" not in event.reasons:
                    event.reasons.append("boundary_uncertain")
                event.status = "pending"
            event.start_ms = max(0, event.start_ms)
            event.end_ms = min(review.video.duration_ms, event.end_ms)
    finally:
        capture.release()

    active_events = sorted(review.events, key=lambda item: (item.start_ms, item.end_ms))
    for previous, current in zip(active_events, active_events[1:], strict=False):
        if previous.end_ms <= current.start_ms:
            continue
        transition_ms = max(previous.start_ms + 1, current.start_ms)
        previous.end_ms = transition_ms
        if previous.end_ms - previous.start_ms < review.profile.minimum_event_duration_ms:
            if "boundary_uncertain" not in previous.reasons:
                previous.reasons.append("boundary_uncertain")
            previous.status = "pending"
    observations.frames.sort(key=lambda item: (item.timestamp_ms, item.phase))
    observations.statistics.boundary_frames = len(boundary_cache)
    observations.statistics.boundary_ocr_seconds = sum(
        observation.ocr_seconds for observation in boundary_cache.values()
    )
    observations.statistics.boundary_decode_seconds = boundary_decode_seconds
    observations.updated_at = utc_now_text()
    atomic_write_model(observations, observations_path)
    return observations, review


def _backup_existing(path: Path) -> Path:
    """把现有产物复制为带时间戳的可恢复备份。"""

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.{timestamp}.bak")
    shutil.copy2(path, backup)
    return backup


def _review_has_human_changes(review: OCRReviewArtifact) -> bool:
    """判断 review 是否已经包含人工编辑或审核决定。"""

    return review.revision > 0 or any(
        event.human_edited or event.status in {"accepted", "deleted"}
        for event in review.events
    )


def extract_video_subtitles(
    video_path: str | Path,
    series_id: str,
    episode: int,
    output_dir: str | Path,
    *,
    profile_source: str | Path = DEFAULT_PROFILE_ID,
    resume: bool = False,
    aggregate_only: bool = False,
    force: bool = False,
) -> tuple[Path, Path]:
    """执行视频扫描和聚合，只生成 observations 与 review JSON。"""

    profile = load_profile(profile_source)
    observations_path, review_path = artifact_paths(output_dir, series_id, episode)
    observations_path.parent.mkdir(parents=True, exist_ok=True)
    if not aggregate_only:
        if observations_path.exists() and not resume and not force:
            raise FileExistsError(
                f"observations 已存在；请使用 --resume 或 --force: {observations_path}"
            )
        if force and observations_path.exists():
            _backup_existing(observations_path)
        artifact = scan_video(
            video_path,
            series_id,
            episode,
            profile,
            observations_path,
            resume=resume,
        )
    else:
        artifact = load_observations(observations_path)

    output_review_path = review_path
    if review_path.exists():
        existing_review = load_review(review_path)
        if _review_has_human_changes(existing_review):
            output_review_path = review_path.with_name(
                f"{default_artifact_stem(series_id, episode)}.candidate-review.json"
            )
        elif force:
            _backup_existing(review_path)
        elif not aggregate_only and not resume:
            raise FileExistsError(f"review 已存在；请使用 --force: {review_path}")
    review = aggregate_observations(artifact, observations_path)
    artifact, review = refine_review_boundaries(
        artifact,
        review,
        observations_path,
    )
    review = write_pending_evidence(review, output_review_path)
    atomic_write_model(review, output_review_path)
    return observations_path, output_review_path
