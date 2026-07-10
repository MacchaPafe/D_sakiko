from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.release.file_selection import FileSelectionRules, collect_selected_files, select_files


class ReleaseFileSelectionTest(unittest.TestCase):
    """验证更新与修复共用的文件规则求值语义。"""

    def test_include_overrides_exclude_but_not_hard_exclude(self) -> None:
        """普通排除可被 include 覆盖，最终排除不可被覆盖。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "kept.py").write_text("kept", encoding="utf-8")
            (root / "blocked.py").write_text("blocked", encoding="utf-8")
            rules = FileSelectionRules(
                include=("*.py",),
                exclude=("*.py",),
                hard_exclude=("blocked.py",),
            )

            result = select_files(root, {"kept.py", "blocked.py"}, rules)

        self.assertEqual(result.selected, frozenset({"kept.py"}))
        self.assertEqual(result.excluded_reasons["blocked.py"], "hard_exclude:blocked.py")

    def test_allow_limits_candidates_without_blocking_explicit_include(self) -> None:
        """allow 限制基础候选，但显式 include 仍可补充正式文件。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.py").write_text("main", encoding="utf-8")
            (root / "run.bat").write_text("run", encoding="utf-8")
            (root / "notes.md").write_text("notes", encoding="utf-8")
            rules = FileSelectionRules(allow=("*.py",), include=("*.bat",))

            result = select_files(root, {"main.py", "run.bat", "notes.md"}, rules)

        self.assertEqual(result.selected, frozenset({"main.py", "run.bat"}))
        self.assertEqual(result.excluded_reasons["notes.md"], "not_allowed")

    def test_git_mode_reports_forced_untracked_include(self) -> None:
        """Git 模式应标记被 include 补充的未追踪文件。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "tracked.py").write_text("tracked", encoding="utf-8")
            (root / "run.command").write_text("run", encoding="utf-8")
            rules = FileSelectionRules(allow=("*.py",), include=("*.command",))

            result = select_files(
                root,
                {"tracked.py"},
                rules,
                include_candidates={"run.command"},
                tracked_candidates={"tracked.py"},
            )

        self.assertEqual(result.selected, frozenset({"tracked.py", "run.command"}))
        self.assertEqual(result.forced_untracked, frozenset({"run.command"}))

    def test_directory_hard_exclude_applies_at_any_depth(self) -> None:
        """目录级 hard_exclude 应在任意深度阻止元数据文件。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "GPT_SoVITS" / "__pycache__"
            nested.mkdir(parents=True)
            (nested / "main.pyc").write_bytes(b"cache")
            source = root / "GPT_SoVITS" / "main.py"
            source.write_text("main", encoding="utf-8")
            rules = FileSelectionRules(hard_exclude=("**/__pycache__/**",))

            result = collect_selected_files(root, rules, use_git_tracked=False)

        self.assertEqual(result.selected, frozenset({"GPT_SoVITS/main.py"}))


if __name__ == "__main__":
    unittest.main()
