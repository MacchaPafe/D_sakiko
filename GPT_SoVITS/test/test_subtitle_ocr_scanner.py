"""测试字幕 OCR 扫描器的快速路径与顺序边界解码。"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import numpy as np
from numpy.typing import NDArray

from rag.pipeline.subtitle_ocr.models import OCRCandidate
from rag.pipeline.subtitle_ocr.profiles import load_profile
from rag.pipeline.subtitle_ocr.scanner import (
    _iter_boundary_frames,
    _select_fixed_region_candidate,
    observe_frame,
)


class EmptyOCRResult:
    """提供不包含检测文字的 RapidOCR 兼容结果。"""

    boxes: NDArray[np.float32] | None = None
    txts: tuple[str, ...] | None = None
    scores: tuple[float, ...] | None = None


class FakeVideoCapture:
    """记录边界读取器采用 seek 还是顺序抓帧。"""

    def __init__(self) -> None:
        """初始化调用计数器和固定测试帧。"""

        self.seek_calls = 0
        self.read_calls = 0
        self.grab_calls = 0
        self.retrieve_calls = 0
        self.frame = np.zeros((4, 4, 3), dtype=np.uint8)

    def set(self, property_id: int, value: float) -> bool:
        """记录一次随机定位。"""

        _ = property_id, value
        self.seek_calls += 1
        return True

    def read(self) -> tuple[bool, NDArray[np.uint8]]:
        """记录一次随机定位后的完整读取。"""

        self.read_calls += 1
        return True, self.frame

    def grab(self) -> bool:
        """记录一次不取回像素的顺序抓帧。"""

        self.grab_calls += 1
        return True

    def retrieve(self) -> tuple[bool, NDArray[np.uint8]]:
        """记录一次顺序抓帧后的像素取回。"""

        self.retrieve_calls += 1
        return True, self.frame


class SubtitleOCRScannerTest(unittest.TestCase):
    """验证性能优化不会依赖真实视频或真实 OCR 模型。"""

    def test_similar_fixed_candidates_prefer_shorter_text(self) -> None:
        """单行和双行近似时选择没有可疑尾字的较短文本。"""

        candidates = [
            OCRCandidate(
                source="fixed_single",
                text="露脸就更不行了",
                normalized_text="露脸就更不行了",
                confidence=0.98,
            ),
            OCRCandidate(
                source="fixed_double",
                text="露脸就更不行了け",
                normalized_text="露脸就更不行了け",
                confidence=0.995,
            ),
        ]

        self.assertEqual(_select_fixed_region_candidate(candidates), 0)

    def test_high_confidence_fixed_result_skips_full_detection(self) -> None:
        """高置信度固定区域结果不会调用完整文字检测。"""

        candidate = OCRCandidate(
            source="fixed_single",
            text="从现在开始加油吧",
            normalized_text="从现在开始加油吧",
            confidence=0.99,
        )
        engine = Mock(return_value=EmptyOCRResult())
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        with patch(
            "rag.pipeline.subtitle_ocr.scanner._fixed_region_candidates",
            return_value=([candidate], 0),
        ):
            observation = observe_frame(engine, frame, 500, load_profile())

        engine.assert_not_called()
        self.assertEqual(observation.selected_candidate(), candidate)

    def test_low_confidence_fixed_result_runs_full_detection(self) -> None:
        """低置信度固定区域结果必须经过完整检测并降级为回退证据。"""

        candidate = OCRCandidate(
            source="fixed_single",
            text="年年年",
            normalized_text="年年年",
            confidence=0.5,
        )
        engine = Mock(return_value=EmptyOCRResult())
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        with patch(
            "rag.pipeline.subtitle_ocr.scanner._fixed_region_candidates",
            return_value=([candidate], 0),
        ):
            observation = observe_frame(engine, frame, 500, load_profile())

        engine.assert_called_once()
        selected = observation.selected_candidate()
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.source, "fallback_single")

    def test_boundary_reader_seeks_only_across_long_gaps(self) -> None:
        """相邻边界点顺序抓帧，超过阈值后才重新 seek。"""

        capture = FakeVideoCapture()
        frames = list(
            _iter_boundary_frames(
                capture,
                [1000, 1100, 1200, 5000],
                fps=10.0,
                seek_threshold_ms=2000,
            )
        )

        self.assertEqual([timestamp for timestamp, _, _ in frames], [1000, 1100, 1200, 5000])
        self.assertEqual(capture.seek_calls, 2)
        self.assertEqual(capture.read_calls, 2)
        self.assertEqual(capture.grab_calls, 2)
        self.assertEqual(capture.retrieve_calls, 2)


if __name__ == "__main__":
    unittest.main()
