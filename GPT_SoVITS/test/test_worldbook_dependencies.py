"""世界书包 SemVer 依赖审计测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rag.worldbook.models import PackageDependency, WorldbookManifest
from rag.worldbook.package_loader import WorldbookPackageLoader
from rag.worldbook.versioning import parse_semver, validate_version_spec, version_satisfies


def _write_empty_package(
    root: Path,
    package_id: str,
    version: str,
    dependencies: list[PackageDependency] | None = None,
) -> None:
    """写入不含内容分片的最小合法测试包。"""

    package_dir = root / package_id
    package_dir.mkdir(parents=True)
    manifest = WorldbookManifest(
        package_id=package_id,
        package_version=version,
        display_name=package_id,
        package_type="season",
        timeline_id="main",
        dependencies=dependencies or [],
    )
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )


class WorldbookDependencyTest(unittest.TestCase):
    """验证显式版本范围与完整依赖闭包。"""

    def test_supported_version_comparators(self) -> None:
        """比较器应支持合取范围与标准预发布优先级。"""

        self.assertTrue(version_satisfies("1.4.0", ">=1.0.0,<2.0.0"))
        self.assertFalse(version_satisfies("2.0.0", ">=1.0.0,<2.0.0"))
        self.assertLess(parse_semver("1.0.0-rc.1"), parse_semver("1.0.0"))
        with self.assertRaises(ValueError):
            validate_version_spec("^1.0.0")

    def test_version_mismatch_makes_dependent_unavailable(self) -> None:
        """直接依赖版本不满足时应阻断依赖包。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_package(root, "mygo", "1.0.0")
            _write_empty_package(
                root,
                "mujica",
                "1.0.0",
                [PackageDependency(package_id="mygo", version_spec=">=2.0.0")],
            )

            results = WorldbookPackageLoader(root).discover()

        self.assertEqual(results["mygo"].readiness.value, "ready")
        self.assertEqual(results["mujica"].readiness.value, "unavailable")
        self.assertIn("dependency_version_mismatch", {issue.code for issue in results["mujica"].issues})

    def test_unavailable_dependency_propagates_through_closure(self) -> None:
        """依赖闭包中的缺失包应使全部下游包不可用。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_package(
                root,
                "base",
                "1.0.0",
                [PackageDependency(package_id="missing")],
            )
            _write_empty_package(
                root,
                "middle",
                "1.0.0",
                [PackageDependency(package_id="base")],
            )
            _write_empty_package(
                root,
                "root",
                "1.0.0",
                [PackageDependency(package_id="middle")],
            )

            results = WorldbookPackageLoader(root).discover()

        self.assertEqual(results["base"].readiness.value, "unavailable")
        self.assertEqual(results["middle"].readiness.value, "unavailable")
        self.assertEqual(results["root"].readiness.value, "unavailable")

    def test_cycle_makes_all_cycle_dependents_unavailable(self) -> None:
        """依赖循环及其上游根包都不得参与检索。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_package(root, "a", "1.0.0", [PackageDependency(package_id="b")])
            _write_empty_package(root, "b", "1.0.0", [PackageDependency(package_id="a")])
            _write_empty_package(root, "root", "1.0.0", [PackageDependency(package_id="a")])

            results = WorldbookPackageLoader(root).discover()

        self.assertEqual(results["a"].readiness.value, "unavailable")
        self.assertEqual(results["b"].readiness.value, "unavailable")
        self.assertEqual(results["root"].readiness.value, "unavailable")


if __name__ == "__main__":
    unittest.main()
