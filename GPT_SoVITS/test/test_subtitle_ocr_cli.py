"""测试字幕 OCR CLI 参数边界。"""

from __future__ import annotations

from pathlib import Path
import unittest

from rag.pipeline.cli import _build_parser


class SubtitleOCRCLITest(unittest.TestCase):
    """验证三个 Stage 0 子命令可以被统一解析器识别。"""

    def test_extract_arguments(self) -> None:
        """提取命令解析系列、集数和输出目录。"""

        args = _build_parser().parse_args(
            [
                "extract-video-subtitles",
                "--video",
                "episode.mp4",
                "--series-id",
                "yume_mita",
                "--episode",
                "1",
                "--output-dir",
                "output",
            ]
        )
        self.assertEqual(args.command, "extract-video-subtitles")
        self.assertEqual(args.episode, 1)
        self.assertEqual(args.output_dir, Path("output"))

    def test_review_and_publish_arguments(self) -> None:
        """复核与发布命令共享 review JSON 输入。"""

        parser = _build_parser()
        review_args = parser.parse_args(
            ["review-ocr-subtitles", "--input", "review.json"]
        )
        publish_args = parser.parse_args(
            ["publish-ocr-subtitles", "--input", "review.json"]
        )
        self.assertEqual(review_args.command, "review-ocr-subtitles")
        self.assertEqual(publish_args.command, "publish-ocr-subtitles")


if __name__ == "__main__":
    unittest.main()
