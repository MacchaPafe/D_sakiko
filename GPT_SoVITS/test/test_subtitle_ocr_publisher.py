"""测试审核字幕到正式 ASS 的发布门槛。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from rag.pipeline.subtitle_loader import build_utterance_units, load_relevant_subtitle_lines
from rag.pipeline.subtitle_ocr.models import (
    OCRReviewArtifact,
    SubtitleReviewEvent,
    VideoIdentity,
)
from rag.pipeline.subtitle_ocr.profiles import load_profile
from rag.pipeline.subtitle_ocr.publisher import publish_review_ass
from rag.pipeline.subtitle_ocr.storage import atomic_write_model, load_review


def make_publishable_review(status: str = "auto_accepted") -> OCRReviewArtifact:
    """构造无需视频即可发布的审核产物。"""

    return OCRReviewArtifact(
        series_id="yume_mita",
        episode=1,
        video=VideoIdentity(
            path="/tmp/missing.mp4",
            size_bytes=1,
            modified_ns=1,
            duration_ms=5000,
            width=1920,
            height=1080,
            fps=30.0,
        ),
        observations_path="/tmp/observations.json",
        profile=load_profile(),
        events=[
            SubtitleReviewEvent(
                event_id="event-1",
                start_ms=1000,
                end_ms=2000,
                text="第一行\n第二行",
                status=status,
                confidence=0.99,
                representative_timestamp_ms=1500,
            )
        ],
    )


class SubtitleOCRPublisherTest(unittest.TestCase):
    """验证正式 ASS 只来自完成审核的 review JSON。"""

    def test_pending_blocks_publication(self) -> None:
        """存在 pending 事件时不会留下 ASS 文件。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review_path = root / "yume_mita[01].review.json"
            ass_path = root / "yume_mita[01].ass"
            atomic_write_model(make_publishable_review("pending"), review_path)
            with self.assertRaisesRegex(ValueError, "pending"):
                publish_review_ass(review_path, ass_path)
            self.assertFalse(ass_path.exists())

    def test_clean_ass_roundtrip_enters_stage1(self) -> None:
        """正式 ASS 保留多行并能转换为中文单语台词。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review_path = root / "yume_mita[01].review.json"
            ass_path = root / "yume_mita[01].ass"
            atomic_write_model(make_publishable_review(), review_path)
            published = publish_review_ass(review_path, ass_path)
            lines = load_relevant_subtitle_lines(published)
            utterances = build_utterance_units(lines, episode=1)
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0].style, "Dial_CH")
            self.assertEqual(utterances[0].zh_text, "第一行 第二行")
            self.assertNotIn("OCR_NEEDS_REVIEW", published.read_text(encoding="utf-8-sig"))
            self.assertFalse(load_review(review_path).publication_is_stale())


if __name__ == "__main__":
    unittest.main()
