"""防止 UI 目录重组后再次出现旧包引用或跨控件体系放置组件。"""
import ast
import importlib.util
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "GPT_SoVITS"
PACKAGES = {"ui_fluent_widget", "ui_qt", "ui_motion_editor", "ui_small_theater"}
LEGACY_PACKAGES = {"ui", "ui_main"}
LEGACY_MODULE_PATH = re.compile(
    r"\b(?:" + "|".join(LEGACY_PACKAGES) + r")\.(?:components|custom_widgets|interfaces|controllers|threads|file_manager)\b"
)


class UiPackageBoundariesTest(unittest.TestCase):
    def source_files(self):
        """覆盖入口、界面、测试及启动器中的静态和函数内导入。"""
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        paths = {ROOT / name for name in tracked if name.endswith(".py") and (ROOT / name).is_file()}
        for directory in PACKAGES:
            paths.update((SOURCE / directory).rglob("*.py"))
        yield from sorted(paths)

    def test_import_targets_and_package_boundaries(self):
        for path in self.source_files():
            package = ".".join(path.relative_to(SOURCE).parts[:-1]) if path.is_relative_to(SOURCE) else ""
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    name = node.module or ""
                    if node.level and package.split(".")[0] in PACKAGES:
                        name = importlib.util.resolve_name("." * node.level + name, package)
                    names = [name]
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    with self.subTest(path=str(path.relative_to(ROOT)), line=node.lineno):
                        self.assertIsNone(LEGACY_MODULE_PATH.search(node.value))
                for name in names:
                    with self.subTest(path=str(path.relative_to(ROOT)), line=node.lineno, module=name):
                        root = name.split(".")[0]
                        self.assertNotIn(root, LEGACY_PACKAGES)
                        if root in PACKAGES:
                            target = SOURCE / name.replace(".", "/")
                            self.assertTrue(target.is_dir() or target.with_suffix(".py").is_file())
                        if package.split(".")[0] in PACKAGES - {"ui_fluent_widget"}:
                            self.assertNotEqual(root, "qfluentwidgets")

    def test_no_legacy_package_initializers(self):
        for name in ("ui", "ui_main"):
            self.assertFalse((SOURCE / name / "__init__.py").exists())
        for name in PACKAGES:
            self.assertTrue((SOURCE / name / "__init__.py").is_file())


if __name__ == "__main__":
    unittest.main()
