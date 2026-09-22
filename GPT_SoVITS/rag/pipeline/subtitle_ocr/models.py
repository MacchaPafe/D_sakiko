"""定义视频字幕 OCR 的可版本化中间产物。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = 2
ReviewStatus = Literal["auto_accepted", "accepted", "pending", "deleted"]
ObservationSource = Literal[
    "primary",
    "fixed_single",
    "fixed_double",
    "fallback_single",
    "fallback_double",
]
ObservationPhase = Literal["coarse", "boundary"]


def utc_now_text() -> str:
    """返回适合写入 JSON 的 UTC 时间文本。"""

    return datetime.now(timezone.utc).isoformat()


class RelativeRegion(BaseModel):
    """描述以画面宽高比例表示的矩形区域。"""

    model_config = ConfigDict(extra="forbid")

    left: float = Field(ge=0.0, le=1.0)
    top: float = Field(ge=0.0, le=1.0)
    right: float = Field(ge=0.0, le=1.0)
    bottom: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_order(self) -> RelativeRegion:
        """验证矩形左右和上下边界顺序。"""

        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("字幕区域必须满足 left < right 且 top < bottom")
        return self


class SubtitleOCRProfile(BaseModel):
    """描述一种视频字幕布局及聚合默认参数。"""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    subtitle_band: RelativeRegion
    single_line: RelativeRegion
    double_top: RelativeRegion
    double_bottom: RelativeRegion
    minimum_box_height_ratio: float = Field(gt=0.0, le=1.0)
    minimum_chinese_characters: int = Field(ge=1)
    fixed_region_minimum_confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    coarse_interval_ms: int = Field(ge=50)
    boundary_step_ms: int = Field(ge=20)
    boundary_window_ms: int = Field(ge=0)
    empty_tolerance_ms: int = Field(ge=0)
    minimum_event_duration_ms: int = Field(ge=100)
    boundary_seek_threshold_ms: int = Field(default=2000, ge=0)
    checkpoint_interval_ms: int = Field(ge=1000)


class VideoIdentity(BaseModel):
    """保存用于发现输入视频明显变化的轻量身份信息。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    size_bytes: int = Field(ge=0)
    modified_ns: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    fps: float = Field(ge=0.0)


class OCRBox(BaseModel):
    """描述 OCR 字框在字幕裁剪区域内的像素边界。"""

    model_config = ConfigDict(extra="forbid")

    left: float
    top: float
    right: float
    bottom: float

    @property
    def height(self) -> float:
        """返回字框高度。"""

        return self.bottom - self.top

    @property
    def vertical_center(self) -> float:
        """返回字框纵向中心。"""

        return (self.top + self.bottom) / 2.0


class OCRCandidate(BaseModel):
    """保存单个采样点的一种字幕文本假设。"""

    model_config = ConfigDict(extra="forbid")

    source: ObservationSource
    text: str
    normalized_text: str
    confidence: float = Field(ge=0.0, le=1.0)
    boxes: list[OCRBox] = Field(default_factory=list)
    accepted: bool = True
    rejection_reason: str | None = None


class FrameObservation(BaseModel):
    """保存一个视频时间点的主检测和回退 OCR 证据。"""

    model_config = ConfigDict(extra="forbid")

    timestamp_ms: int = Field(ge=0)
    phase: ObservationPhase = "coarse"
    candidates: list[OCRCandidate] = Field(default_factory=list)
    selected_candidate_index: int | None = Field(default=None, ge=0)
    ocr_seconds: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_selected_index(self) -> FrameObservation:
        """验证已选候选索引没有越界。"""

        if self.selected_candidate_index is not None and self.selected_candidate_index >= len(
            self.candidates
        ):
            raise ValueError("selected_candidate_index 超出 candidates 范围")
        return self

    def selected_candidate(self) -> OCRCandidate | None:
        """返回当前采样点用于聚合的候选。"""

        if self.selected_candidate_index is None:
            return None
        return self.candidates[self.selected_candidate_index]


class OCRRunStatistics(BaseModel):
    """记录一次扫描的数量和耗时摘要。"""

    model_config = ConfigDict(extra="forbid")

    processed_frames: int = Field(ge=0)
    primary_frames: int = Field(ge=0)
    fallback_frames: int = Field(ge=0)
    empty_frames: int = Field(ge=0)
    decode_seconds: float = Field(ge=0.0)
    primary_ocr_seconds: float = Field(ge=0.0)
    fallback_ocr_seconds: float = Field(ge=0.0)
    boundary_frames: int = Field(default=0, ge=0)
    boundary_ocr_seconds: float = Field(default=0.0, ge=0.0)
    boundary_decode_seconds: float = Field(default=0.0, ge=0.0)


class OCRObservationsArtifact(BaseModel):
    """保存可断点续跑的逐帧 OCR 观测。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    series_id: str
    episode: int = Field(ge=1)
    video: VideoIdentity
    profile: SubtitleOCRProfile
    runtime_versions: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_text)
    updated_at: str = Field(default_factory=utc_now_text)
    scan_complete: bool = False
    completed_until_ms: int = Field(default=0, ge=0)
    frames: list[FrameObservation] = Field(default_factory=list)
    statistics: OCRRunStatistics


class EventCandidateSummary(BaseModel):
    """记录一个字幕事件内部出现过的正文候选及其得分。"""

    model_config = ConfigDict(extra="forbid")

    text: str
    normalized_text: str
    occurrences: int = Field(ge=1)
    mean_confidence: float = Field(ge=0.0, le=1.0)
    consensus_score: float = Field(ge=0.0)
    sources: list[ObservationSource]


class SubtitleReviewEvent(BaseModel):
    """表示可在编辑器中审核和发布的一条字幕事件。"""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str
    status: ReviewStatus
    reasons: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    representative_timestamp_ms: int = Field(ge=0)
    observation_timestamps_ms: list[int] = Field(default_factory=list)
    candidates: list[EventCandidateSummary] = Field(default_factory=list)
    evidence_full_frame: str | None = None
    evidence_crop: str | None = None
    human_edited: bool = False
    deletion_reason: str | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> SubtitleReviewEvent:
        """验证事件结束时间严格晚于开始时间。"""

        if self.end_ms <= self.start_ms:
            raise ValueError("字幕事件必须满足 end_ms > start_ms")
        return self


class PublicationRecord(BaseModel):
    """记录最近一次正式 ASS 发布状态。"""

    model_config = ConfigDict(extra="forbid")

    ass_path: str
    published_at: str
    published_revision: int = Field(ge=0)


class OCRReviewArtifact(BaseModel):
    """保存聚合字幕事件、人工审核和发布状态。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    series_id: str
    episode: int = Field(ge=1)
    video: VideoIdentity
    observations_path: str
    profile: SubtitleOCRProfile
    created_at: str = Field(default_factory=utc_now_text)
    updated_at: str = Field(default_factory=utc_now_text)
    revision: int = Field(default=0, ge=0)
    events: list[SubtitleReviewEvent] = Field(default_factory=list)
    publication: PublicationRecord | None = None

    def pending_count(self) -> int:
        """返回尚未完成复核的事件数量。"""

        return sum(event.status == "pending" for event in self.events)

    def publication_is_stale(self) -> bool:
        """返回最近发布结果是否落后于当前审核修订。"""

        return self.publication is None or self.publication.published_revision != self.revision


def default_artifact_stem(series_id: str, episode: int) -> str:
    """生成满足现有字幕 loader 集数约定的文件名前缀。"""

    return f"{series_id}[{episode:02d}]"


def artifact_paths(output_dir: str | Path, series_id: str, episode: int) -> tuple[Path, Path]:
    """返回 observations 与 review JSON 的默认路径。"""

    root = Path(output_dir)
    stem = default_artifact_stem(series_id, episode)
    return root / f"{stem}.observations.json", root / f"{stem}.review.json"
