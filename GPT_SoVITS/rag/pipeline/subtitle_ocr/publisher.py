"""校验字幕审核产物并发布唯一的正式 ASS。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import time

import pysubs2

from ..subtitle_loader import extract_episode_number, load_relevant_subtitle_lines
from .models import OCRReviewArtifact, PublicationRecord, default_artifact_stem, utc_now_text
from .storage import atomic_write_model, load_review


def validate_publishable_review(review: OCRReviewArtifact) -> list[str]:
    """返回阻止正式发布的全部字幕审核问题。"""

    issues: list[str] = []
    pending = [event.event_id for event in review.events if event.status == "pending"]
    if pending:
        issues.append(f"仍有 {len(pending)} 条 pending 字幕")
    active = sorted(
        (event for event in review.events if event.status != "deleted"),
        key=lambda event: (event.start_ms, event.end_ms, event.event_id),
    )
    if not active:
        issues.append("没有可发布字幕")
    previous = None
    for event in active:
        if not event.text.strip():
            issues.append(f"{event.event_id} 正文为空")
        if event.end_ms <= event.start_ms:
            issues.append(f"{event.event_id} 时间范围无效")
        if previous is not None and event.start_ms < previous.end_ms:
            issues.append(f"{previous.event_id} 与 {event.event_id} 时间重叠")
        previous = event
    return issues


def default_ass_path(review_path: str | Path, review: OCRReviewArtifact) -> Path:
    """返回审核文件并排的正式 ASS 默认路径。"""

    return Path(review_path).resolve().parent / f"{default_artifact_stem(review.series_id, review.episode)}.ass"


def build_ass_document(review: OCRReviewArtifact) -> pysubs2.SSAFile:
    """把审核完成且未删除的事件转换为干净的 ASS 文档。"""

    issues = validate_publishable_review(review)
    if issues:
        raise ValueError("无法发布 ASS：" + "；".join(issues))
    subtitles = pysubs2.SSAFile()
    subtitles.info["Title"] = f"{review.series_id} episode {review.episode} reviewed subtitles"
    subtitles.styles["Dial_CH"] = pysubs2.SSAStyle(
        fontname="Arial",
        fontsize=48,
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
        marginv=45,
    )
    for event in sorted(review.events, key=lambda item: (item.start_ms, item.end_ms)):
        if event.status == "deleted":
            continue
        subtitles.events.append(
            pysubs2.SSAEvent(
                start=event.start_ms,
                end=event.end_ms,
                text=event.text.replace("\n", "\\N"),
                style="Dial_CH",
            )
        )
    return subtitles


def _backup_ass(path: Path) -> Path:
    """为将被替换的正式 ASS 创建带时间戳备份。"""

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.{timestamp}.bak")
    shutil.copy2(path, backup)
    return backup


def publish_review_ass(
    review_path: str | Path,
    output_path: str | Path | None = None,
    *,
    backup_existing: bool = True,
) -> Path:
    """校验 review、回读临时 ASS，并原子发布正式字幕文件。"""

    review_target = Path(review_path).resolve()
    review = load_review(review_target)
    ass_target = Path(output_path).resolve() if output_path else default_ass_path(review_target, review)
    if extract_episode_number(ass_target) != review.episode:
        raise ValueError("正式 ASS 文件名中的集数与 review.episode 不一致")
    subtitles = build_ass_document(review)
    ass_target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{ass_target.name}.",
        suffix=".ass",
        dir=ass_target.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        subtitles.save(str(temporary_path), encoding="utf-8")
        parsed = load_relevant_subtitle_lines(temporary_path)
        expected_count = sum(event.status != "deleted" for event in review.events)
        if len(parsed) != expected_count:
            raise ValueError(
                f"ASS 回读条目数不一致：期望 {expected_count}，实际 {len(parsed)}"
            )
        if ass_target.exists() and backup_existing:
            _backup_ass(ass_target)
        os.replace(temporary_path, ass_target)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    review.publication = PublicationRecord(
        ass_path=str(ass_target),
        published_at=utc_now_text(),
        published_revision=review.revision,
    )
    review.updated_at = utc_now_text()
    atomic_write_model(review, review_target)
    return ass_target
