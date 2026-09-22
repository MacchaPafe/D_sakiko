"""测试字幕 OCR 复核编辑器的证据缓存。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from rag.pipeline.subtitle_ocr.editor import OCRSubtitleReviewEditor
from rag.pipeline.subtitle_ocr.models import (
    OCRReviewArtifact,
    SubtitleReviewEvent,
    VideoIdentity,
)
from rag.pipeline.subtitle_ocr.profiles import load_profile
from rag.pipeline.subtitle_ocr.storage import atomic_write_model


def make_review() -> OCRReviewArtifact:
    """构造包含一条字幕的审核文件。"""

    return OCRReviewArtifact(
        series_id="yume_mita",
        episode=1,
        video=VideoIdentity(
            path="/tmp/fake.mp4",
            size_bytes=1,
            modified_ns=1,
            duration_ms=5000,
            width=200,
            height=100,
            fps=30.0,
        ),
        observations_path="/tmp/observations.json",
        profile=load_profile(),
        events=[
            SubtitleReviewEvent(
                event_id="event-1",
                start_ms=1000,
                end_ms=2000,
                text="第一条",
                status="pending",
                confidence=0.8,
                representative_timestamp_ms=1500,
            )
        ],
    )


class SubtitleOCREditorTest(unittest.TestCase):
    """验证编辑器不会为同一时间点重复解码视频。"""

    def test_current_frame_and_crop_share_one_decode(self) -> None:
        """完整帧和字幕裁剪应由同一次视频读取生成。"""

        with tempfile.TemporaryDirectory() as directory:
            review_path = Path(directory) / "yume_mita[01].review.json"
            atomic_write_model(make_review(), review_path)
            editor = OCRSubtitleReviewEditor(review_path)
            frame = np.zeros((100, 200, 3), dtype=np.uint8)
            with mock.patch.object(
                editor,
                "_read_video_frame",
                return_value=frame,
            ) as read_frame:
                full_path, crop_path = editor._cache_current_evidence(1500)
                cached_full_path, cached_crop_path = editor._cache_current_evidence(1500)

            self.assertEqual(read_frame.call_count, 1)
            self.assertEqual(cached_full_path, full_path)
            self.assertEqual(cached_crop_path, crop_path)
            self.assertIsNotNone(full_path)
            self.assertIsNotNone(crop_path)
            assert full_path is not None
            assert crop_path is not None
            self.assertTrue(full_path.exists())
            self.assertTrue(crop_path.exists())


if __name__ == "__main__":
    unittest.main()
