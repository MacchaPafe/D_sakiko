"""测试字幕 OCR 审核工作区的可逆编辑。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from rag.pipeline.subtitle_ocr.models import (
    OCRReviewArtifact,
    SubtitleReviewEvent,
    VideoIdentity,
)
from rag.pipeline.subtitle_ocr.profiles import load_profile
from rag.pipeline.subtitle_ocr.storage import atomic_write_model, load_review
from rag.pipeline.subtitle_ocr.workspace import OCRReviewWorkspace


def make_review() -> OCRReviewArtifact:
    """构造两条可编辑字幕事件。"""

    return OCRReviewArtifact(
        series_id="yume_mita",
        episode=1,
        video=VideoIdentity(
            path="/tmp/fake.mp4",
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
                text="第一条",
                status="auto_accepted",
                confidence=0.99,
                representative_timestamp_ms=1500,
            ),
            SubtitleReviewEvent(
                event_id="event-2",
                start_ms=2200,
                end_ms=3000,
                text="第二条",
                status="pending",
                reasons=["low_confidence"],
                confidence=0.80,
                representative_timestamp_ms=2500,
            ),
        ],
    )


class SubtitleOCRWorkspaceTest(unittest.TestCase):
    """验证人工审核操作、撤销和安全保存。"""

    def test_edit_undo_and_save(self) -> None:
        """正文修改可以撤销，并能原子保存。"""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yume_mita[01].review.json"
            atomic_write_model(make_review(), path)
            workspace = OCRReviewWorkspace.load(path)
            workspace.update_event(
                "event-2",
                text="修正后的第二条",
                start_ms=2200,
                end_ms=3100,
            )
            self.assertEqual(workspace.event("event-2").status, "accepted")
            self.assertTrue(workspace.undo())
            self.assertEqual(workspace.event("event-2").text, "第二条")
            self.assertTrue(workspace.redo())
            workspace.save()
            self.assertEqual(load_review(path).events[1].text, "修正后的第二条")

    def test_delete_restore_and_time_range(self) -> None:
        """软删除支持批量时间范围和恢复。"""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yume_mita[01].review.json"
            atomic_write_model(make_review(), path)
            workspace = OCRReviewWorkspace.load(path)
            self.assertEqual(workspace.delete_time_range(0, 2100, "OP"), 1)
            self.assertEqual(workspace.event("event-1").status, "deleted")
            self.assertEqual(workspace.restore_events(["event-1"]), 1)
            self.assertEqual(workspace.event("event-1").status, "accepted")

    def test_split_and_merge(self) -> None:
        """拆分和合并保留软删除审计记录。"""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yume_mita[01].review.json"
            atomic_write_model(make_review(), path)
            workspace = OCRReviewWorkspace.load(path)
            first_id, second_id = workspace.split_event("event-1", 1500, "拆分后")
            merged = workspace.merge_events(first_id, second_id, "重新合并")
            self.assertEqual(merged.text, "重新合并")
            self.assertEqual(workspace.event(second_id).status, "deleted")


if __name__ == "__main__":
    unittest.main()
