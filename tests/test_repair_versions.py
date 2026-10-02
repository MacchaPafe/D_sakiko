from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import repair
from tools.repair_versions import REPAIR_VERSIONS_PATH, load_repair_versions


class RepairVersionsTest(unittest.TestCase):
    """验证快捷版本文件和文件损坏时的交互降级。"""

    def test_direct_script_runs_outside_repository(self) -> None:
        """直接运行脚本时不被 GPT_SoVITS 中的同名 tools 包遮蔽。"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(repair.ROOT / "tools" / "repair.py"), "--help"],
                cwd=directory, capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--version", result.stdout)

    def test_file_preserves_choice_order(self) -> None:
        """发布文件包含 4.0.0，并按文件顺序展示选项。"""
        versions = load_repair_versions(repair.ROOT / REPAIR_VERSIONS_PATH)
        self.assertEqual(versions, ("4.0.0", "3.5.0", "3.2.0"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("builtins.input", return_value="1"):
            self.assertEqual(repair.choose_version(None), "4.0.0")
        self.assertIn("1. 4.0.0", output.getvalue())

    def test_invalid_files_are_rejected(self) -> None:
        """拒绝损坏 JSON、错误结构、无效及重复版本。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "versions.json"
            for contents in ("{broken", "{}", '[4]', '["4.0.0-rc1"]', '["4.0.0", "4.0.0"]'):
                with self.subTest(contents=contents):
                    path.write_text(contents, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_repair_versions(path)

    def test_missing_or_broken_list_allows_manual_and_recommended_version(self) -> None:
        """无有效列表时仍可手动输入，或接受检测版本。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / REPAIR_VERSIONS_PATH
            path.parent.mkdir()
            for contents in (None, "{broken", "[]"):
                if contents is not None:
                    path.write_text(contents, encoding="utf-8")
                for recommendation, answer, expected in ((None, "4.0.0", "4.0.0"), ("3.5.0", "", "3.5.0")):
                    with self.subTest(contents=contents, recommendation=recommendation):
                        output = io.StringIO()
                        with patch.object(repair, "ROOT", root), contextlib.redirect_stdout(output), \
                                patch("builtins.input", return_value=answer):
                            self.assertEqual(repair.choose_version(recommendation), expected)
                        self.assertEqual("无法读取手动修复的版本列表" in output.getvalue(), contents != "[]")

    def test_selection_reads_file_again_after_changes(self) -> None:
        """每次交互读取当前文件，不缓存旧的快捷选项。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / REPAIR_VERSIONS_PATH
            path.parent.mkdir()
            with patch.object(repair, "ROOT", root), contextlib.redirect_stdout(io.StringIO()), \
                    patch("builtins.input", return_value="1"):
                for version in ("4.0.0", "4.1.0"):
                    path.write_text(json.dumps([version]), encoding="utf-8")
                    self.assertEqual(repair.choose_version(None), version)

    def test_explicit_version_does_not_read_choices(self) -> None:
        """显式指定版本时不依赖快捷列表，也不进入版本选择交互。"""
        with tempfile.TemporaryDirectory() as directory:
            argv = ["repair", "--app-root", directory, "--version", "4.0.0", "--check"]
            with patch.object(sys, "argv", argv), patch.object(repair, "load_repair_versions") as load, \
                    patch.object(repair, "setup_logging", return_value=None), \
                    patch.object(repair, "get_configured_repair_base_urls", return_value=[]), \
                    patch.object(repair, "check_integrity", return_value=SimpleNamespace(candidates=())) as check, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(repair.main(), 0)
                load.assert_not_called()
                self.assertEqual(check.call_args.kwargs["version"], "4.0.0")


if __name__ == "__main__":
    unittest.main()
