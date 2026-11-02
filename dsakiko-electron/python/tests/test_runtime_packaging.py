"""发布材料生成的文件完整性测试，不下载或构建依赖。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import ModuleType
import unittest
import zipfile


def load_materials() -> ModuleType:
    """加载独立发布脚本，不要求它安装为 Python 包。"""
    source = Path(__file__).resolve().parents[2] / "scripts" / "runtime" / "materials.py"
    spec = importlib.util.spec_from_file_location("runtime_materials", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载发布脚本")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimePackagingTests(unittest.TestCase):
    """验证实际归档及 wheel 内容与声明保持一致。"""

    def test_archive_metadata_matches_files(self) -> None:
        """中文路径、大文件声明和归档摘要均由实际内容计算。"""
        materials = load_materials()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "材料"
            root.mkdir()
            (root / "文件.txt").write_bytes(b"runtime payload")
            archive = Path(directory) / "runtime.zip"
            materials.archive_tree(root, archive)
            metadata = json.loads(archive.with_suffix(".metadata.json").read_text())
            self.assertEqual(metadata["expandedBytes"], len(b"runtime payload"))
            self.assertEqual(metadata["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(package.read("文件.txt"), b"runtime payload")

    def test_symlink_does_not_replace_good_archive(self) -> None:
        """错误材料不能覆盖已生成的包，也不能留下半成品归档。"""
        materials = load_materials()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            (root / "target").write_text("data")
            try:
                (root / "link").symlink_to(root / "target")
            except OSError:
                self.skipTest("当前 Windows 账户不能创建符号链接")
            archive = Path(directory) / "runtime.zip"
            archive.write_bytes(b"previous")
            with self.assertRaises(ValueError):
                materials.archive_tree(root, archive)
            self.assertEqual(archive.read_bytes(), b"previous")
            self.assertFalse(archive.with_suffix(".zip.partial").exists())

    def test_wheel_lock_uses_built_wheel_hash(self) -> None:
        """本地构建 wheel 的哈希和许可证进入最终离线发布材料。"""
        materials = load_materials()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "wheelhouse").mkdir()
            wheel = root / "wheelhouse" / "example-1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as package:
                package.writestr("example-1.0.dist-info/METADATA", "Name: example\nVersion: 1.0\n")
                package.writestr("example-1.0.dist-info/licenses/LICENSE", "example license")
            materials.wheel_lock(root)
            expected = hashlib.sha256(wheel.read_bytes()).hexdigest()
            self.assertIn("example==1.0 --hash=sha256:" + expected, (root / "requirements.lock").read_text())
            self.assertEqual(len(list((root / "licenses").rglob("*LICENSE"))), 1)


if __name__ == "__main__":
    unittest.main()
