"""测试字幕 OCR 文本关联和事件聚合。"""

from __future__ import annotations

from pathlib import Path
import unittest

from rag.pipeline.subtitle_ocr.aggregation import (
    aggregate_observations,
    levenshtein_distance,
    texts_are_similar,
)
from rag.pipeline.subtitle_ocr.models import (
    FrameObservation,
    OCRCandidate,
    OCRObservationsArtifact,
    OCRRunStatistics,
    VideoIdentity,
)
from rag.pipeline.subtitle_ocr.profiles import load_profile


def make_observation(timestamp_ms: int, text: str | None, confidence: float = 0.99) -> FrameObservation:
    """构造不依赖 OCR 模型的测试观测。"""

    if text is None:
        return FrameObservation(timestamp_ms=timestamp_ms, ocr_seconds=0.01)
    return FrameObservation(
        timestamp_ms=timestamp_ms,
        candidates=[
            OCRCandidate(
                source="primary",
                text=text,
                normalized_text=text,
                confidence=confidence,
            )
        ],
        selected_candidate_index=0,
        ocr_seconds=0.01,
    )


def make_artifact(frames: list[FrameObservation]) -> OCRObservationsArtifact:
    """构造完整的 observations 测试产物。"""

    return OCRObservationsArtifact(
        series_id="yume_mita",
        episode=1,
        video=VideoIdentity(
            path="/tmp/fake.mp4",
            size_bytes=1,
            modified_ns=1,
            duration_ms=10_000,
            width=1920,
            height=1080,
            fps=30.0,
        ),
        profile=load_profile(),
        scan_complete=True,
        completed_until_ms=9000,
        frames=frames,
        statistics=OCRRunStatistics(
            processed_frames=len(frames),
            primary_frames=sum(frame.selected_candidate() is not None for frame in frames),
            fallback_frames=0,
            empty_frames=sum(frame.selected_candidate() is None for frame in frames),
            decode_seconds=0.0,
            primary_ocr_seconds=0.0,
            fallback_ocr_seconds=0.0,
        ),
    )


class SubtitleOCRAggregationTest(unittest.TestCase):
    """验证聚合器不会依赖真实视频或 OCR 模型。"""

    def test_short_text_uses_edit_distance(self) -> None:
        """短文本允许一个异常字符差异。"""

        self.assertEqual(levenshtein_distance("不记得了", "不记得了い"), 1)
        self.assertTrue(texts_are_similar("不记得了", "不记得了い"))

    def test_empty_observation_is_bridged(self) -> None:
        """一个空采样点不会拆分前后相同字幕。"""

        artifact = make_artifact(
            [
                make_observation(1000, "合同上写得很清楚"),
                make_observation(1500, None),
                make_observation(2000, "合同上写得很清楚"),
            ]
        )
        review = aggregate_observations(artifact, Path("/tmp/observations.json"))
        self.assertEqual(len(review.events), 1)
        self.assertEqual(review.events[0].text, "合同上写得很清楚")

    def test_different_text_creates_new_event(self) -> None:
        """连续不同正文被聚合为两个事件。"""

        artifact = make_artifact(
            [
                make_observation(1000, "那个我不太擅长组合"),
                make_observation(1500, "那个我不太擅长组合"),
                make_observation(2000, "哎呀放心不是组合"),
                make_observation(2500, "哎呀放心不是组合"),
            ]
        )
        review = aggregate_observations(artifact, Path("/tmp/observations.json"))
        self.assertEqual([event.text for event in review.events], [
            "那个我不太擅长组合",
            "哎呀放心不是组合",
        ])
        self.assertTrue(all(event.status == "auto_accepted" for event in review.events))

    def test_multiline_order_variation_stays_in_one_event(self) -> None:
        """同一组多行文字仅顺序波动时不会拆成多条字幕。"""

        first = "-我没听说过\n-是啊\n我不是只负责插画相关吗"
        reordered = "-我没听说过\n我不是只负责插画相关吗\n-是啊"
        artifact = make_artifact(
            [
                make_observation(1000, first),
                make_observation(1500, reordered),
                make_observation(2000, first),
            ]
        )
        review = aggregate_observations(artifact, Path("/tmp/observations.json"))
        self.assertEqual(len(review.events), 1)
        self.assertEqual(review.events[0].text, first)
        self.assertIn("unstable_line_order", review.events[0].reasons)

    def test_single_fallback_sample_requires_review(self) -> None:
        """单帧固定区域结果保留但必须进入待复核状态。"""

        observation = FrameObservation(
            timestamp_ms=1000,
            candidates=[
                OCRCandidate(
                    source="fallback_single",
                    text="不记得了",
                    normalized_text="不记得了",
                    confidence=0.98,
                )
            ],
            selected_candidate_index=0,
            ocr_seconds=0.01,
        )
        review = aggregate_observations(
            make_artifact([observation]),
            Path("/tmp/observations.json"),
        )
        self.assertEqual(review.events[0].status, "pending")
        self.assertIn("single_sample", review.events[0].reasons)
        self.assertIn("fallback_only", review.events[0].reasons)

    def test_fixed_region_results_are_primary_quality_evidence(self) -> None:
        """高置信度固定区域快速结果不会被标记为 fallback_only。"""

        frames = []
        for timestamp_ms in (1000, 1500):
            frames.append(
                FrameObservation(
                    timestamp_ms=timestamp_ms,
                    candidates=[
                        OCRCandidate(
                            source="fixed_single",
                            text="从现在开始加油吧",
                            normalized_text="从现在开始加油吧",
                            confidence=0.99,
                        )
                    ],
                    selected_candidate_index=0,
                    ocr_seconds=0.01,
                )
            )

        review = aggregate_observations(
            make_artifact(frames),
            Path("/tmp/observations.json"),
        )

        self.assertEqual(review.events[0].status, "auto_accepted")
        self.assertNotIn("fallback_only", review.events[0].reasons)


if __name__ == "__main__":
    unittest.main()
