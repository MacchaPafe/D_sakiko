from __future__ import annotations

import json
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build_diff_patch import FileRecord, write_manifest
from tools.release import build_update_patch_release as release


class ReleaseBuildTest(unittest.TestCase):
    """验证前端构建的发布顺序和更新器版本的兼容要求。"""

    def setUp(self) -> None:
        """准备不依赖真实仓库和发布产物的配置。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.frontend = self.root / "dsakiko_webui" / "frontend"
        self.frontend.mkdir(parents=True)
        (self.frontend / "package.json").write_text("{}", encoding="utf-8")
        self.profile = self.root / "profiles.json"
        self.profile.write_text(json.dumps({
            "defaults": {"current": ".", "min_updater_version": "1.0.0"},
            "profiles": {"macos-arm64": {"platform": "macos", "arch": "arm64"}},
        }), encoding="utf-8")
        self.local = self.root / "local.json"
        self.options = release.CliOptions(
            allow_non_forward_version=False, allow_cross_platform_build=False,
            allow_pyproject_version_mismatch=False, confirm_untracked_include=True,
            confirm_user_assets_change=True, yes=False, no_zip=True, dry_run=False,
        )

    def config(self, *extra: str) -> release.BuildConfig:
        """通过真实参数解析和配置合并生成测试配置。"""

        argv = ["release", "--profile", "macos-arm64", "--base-version", "3.5.0",
                "--target-version", "4.0.0", "--old", str(self.root / "old"), *extra]
        with patch.object(sys, "argv", argv), patch.object(release, "PROFILE_FILE", self.profile), \
                patch.object(release, "LOCAL_FILE", self.local):
            return release.build_config(release.parse_args(), self.root)

    def test_frontend_default_and_config_cli_overrides(self) -> None:
        """默认构建前端，本地配置可关闭，显式命令行具有最高优先级。"""

        self.assertTrue(self.config().build_frontend)
        self.local.write_text(json.dumps({
            "profile_overrides": {"macos-arm64": {"build_frontend": False}},
        }), encoding="utf-8")
        self.assertFalse(self.config().build_frontend)
        self.assertTrue(self.config("--build-frontend").build_frontend)
        self.assertFalse(self.config("--no-build-frontend").build_frontend)

    def test_pyproject_version_reader(self) -> None:
        """不同解释器的 TOML 读取都应正确处理注释、其他字段和版本。"""
        path = self.root / "pyproject.toml"
        path.write_text(
            '# 测试版本元数据\n[project]\nname = "d-sakiko"\nversion = "4.0.1" # 发布版本\n'
            'dependencies = ["example>=1"]\n', encoding="utf-8",
        )
        self.assertEqual(release.read_pyproject_version(path), "4.0.1")
        path.write_text('[project]\nname = "d-sakiko"\n', encoding="utf-8")
        with self.assertRaisesRegex(release.BuildError, "缺少 project.version"):
            release.read_pyproject_version(path)

    def test_missing_toml_parser_explains_manual_dependency(self) -> None:
        """没有任何 TOML 解析器时给出安装命令，不输出导入堆栈。"""
        result = subprocess.run(
            [sys.executable, "-c",
             'import sys; sys.modules["tomllib"] = None; sys.modules["tomli"] = None; '
             'from tools.release import build_update_patch_release'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("python -m pip install -r tools/release/requirements.txt", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def prepare_preflight(self) -> release.BuildConfig:
        """准备可通过真实预检查的独立待打包目录。"""
        config = self.config("--no-use-git-tracked", "--no-build-frontend")
        config.old.mkdir()
        (config.old / "version.json").write_text('{"version":"3.5.0"}', encoding="utf-8")
        (config.current / "version.json").write_text('{"version":"4.0.0"}', encoding="utf-8")
        (config.current / "pyproject.toml").write_text('[project]\nversion = "4.0.0"\n', encoding="utf-8")
        for executable in (config.hdiff_bin, config.hpatch_bin):
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.touch()
            executable.chmod(0o755)
        return config

    def test_preflight_checks_current_directory_repair_choices(self) -> None:
        """待打包目录缺少目标版本时警告，即使工具仓库列表包含该版本。"""
        config = self.prepare_preflight()
        path = config.current / release.REPAIR_VERSIONS_PATH
        for versions in (["4.0.0", "3.5.0"], ["3.5.0"]):
            with self.subTest(versions=versions):
                path.write_text(json.dumps(versions), encoding="utf-8")
                output = io.StringIO()
                with patch.object(release, "LOCAL_FILE", self.local), \
                        patch.object(release, "detect_current_platform", return_value="macos"), \
                        contextlib.redirect_stderr(output):
                    release.preflight(config, self.options, self.root)
                if "4.0.0" in versions:
                    self.assertEqual(output.getvalue(), "")
                else:
                    self.assertIn("当前版本不在手动修复的可选项中（4.0.0）", output.getvalue())
                    self.assertIn(str(path), output.getvalue())

    def test_preflight_missing_or_broken_repair_choices_only_warns(self) -> None:
        """列表缺失或损坏时警告，但仍能完成发布预检查。"""
        config = self.prepare_preflight()
        path = config.current / release.REPAIR_VERSIONS_PATH
        for contents in (None, "{broken", "{}"):
            with self.subTest(contents=contents):
                if contents is not None:
                    path.write_text(contents, encoding="utf-8")
                output = io.StringIO()
                with patch.object(release, "LOCAL_FILE", self.local), \
                        patch.object(release, "detect_current_platform", return_value="macos"), \
                        contextlib.redirect_stderr(output):
                    release.preflight(config, self.options, self.root)
                self.assertIn("无法检查手动修复的可选项", output.getvalue())
                self.assertIn(str(path), output.getvalue())

    def test_dry_run_and_yes_still_warn_about_missing_repair_choice(self) -> None:
        """真实发布入口的预演和自动确认不会隐藏版本遗漏。"""
        config = self.prepare_preflight()
        (config.current / release.REPAIR_VERSIONS_PATH).write_text('["3.5.0"]', encoding="utf-8")
        output = io.StringIO()
        argv = ["release", "--profile", "macos-arm64", "--base-version", "3.5.0",
                "--target-version", "4.0.0", "--dry-run", "--yes"]
        with patch.object(sys, "argv", argv), patch.object(release, "build_config", return_value=config), \
                patch.object(release, "LOCAL_FILE", self.local), \
                patch.object(release, "detect_current_platform", return_value="macos"), \
                patch.object(release.subprocess, "run") as run, \
                contextlib.redirect_stderr(output), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(release.main(), 0)
            run.assert_not_called()
        self.assertIn("当前版本不在手动修复的可选项中", output.getvalue())

    def test_repeated_replace_raises_minimum_and_preserves_higher_cli_version(self) -> None:
        """重复 replace 参数进入底层命令，较低要求自动提高，较高要求保持。"""

        args = ("--replace", "first.json", "--replace", "second.json")
        config = self.config(*args, "--min-updater-version", "1.0.0")
        self.assertEqual(config.replace, ["first.json", "second.json"])
        self.assertEqual(config.min_updater_version, "1.1.0")
        command = release.build_command(config, no_zip=True)
        self.assertEqual(command.count("--replace"), 2)
        self.assertEqual(command[command.index("--min-updater-version") + 1], "1.1.0")
        self.assertEqual(self.config(*args, "--min-updater-version", "1.10.0").min_updater_version, "1.10.0")
        self.assertEqual(self.config().min_updater_version, "1.0.0")

    def test_direct_manifest_build_enforces_feature_minimum(self) -> None:
        """直接使用底层构建接口也不会生成低于功能要求的 manifest。"""

        for configured, replace, expected in (
            ("1.0.0", False, "1.0.0"), ("0.9.0", False, "1.0.0"),
            ("1.0.0", True, "1.1.0"), ("1.1", True, "1.1"),
            ("1.10.0", True, "1.10.0"),
        ):
            with self.subTest(configured=configured, replace=replace):
                records = [FileRecord("model.json", "replace", "0" * 64, 2)] if replace else []
                manifest_path = write_manifest(
                    output_root=self.root, manifest_name="manifest.json", patch_file_name="patch.hdiff",
                    base_version="3.5.0", target_version="4.0.0", app_id="D_sakiko", channel="stable",
                    platform="macos", arch="arm64", min_updater_version=configured,
                    ignore_patterns=[], include_patterns=[], records=records,
                    remove_files=[], added_files=[], changed_files=[],
                )
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["min_updater_version"], expected)
                self.assertEqual(manifest["format_version"], 4 if replace else 3)

    def test_frontend_runs_before_patch_and_records_build(self) -> None:
        """先完成前端构建再生成补丁，并记录此次构建选项。"""

        config = self.config()
        config.output.mkdir()
        calls: list[str] = []

        def execute(command: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
            """模拟构建命令并验证执行阶段的先后关系。"""

            if command == ["/fake/pnpm", "build"]:
                self.assertEqual(cwd, self.frontend)
                calls.append("frontend")
                (self.frontend / "dist").mkdir()
                (self.frontend / "dist" / "index.html").write_text("fresh", encoding="utf-8")
            else:
                self.assertEqual(calls, ["frontend"])
                self.assertEqual(cwd, self.root)
                calls.append("patch")
            return subprocess.CompletedProcess(command, 0)

        with patch.object(release.shutil, "which", return_value="/fake/pnpm"), \
                patch.object(release.subprocess, "run", side_effect=execute), \
                patch.object(release, "postflight"), patch.object(release, "current_git_commit", return_value="test"):
            release.run_build(config, self.options)
        self.assertEqual(calls, ["frontend", "patch"])
        metadata = json.loads((config.output / "build_metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(metadata["build_frontend"])
        self.assertEqual(metadata["frontend_cwd"], str(self.frontend))

    def test_frontend_failure_preserves_existing_patch(self) -> None:
        """前端失败时不执行补丁构建，也不清理原有发布产物。"""

        config = self.config()
        config.output.mkdir()
        existing = config.output / "patch.hdiff"
        existing.write_bytes(b"previous release")
        with patch.object(release.shutil, "which", return_value="/fake/pnpm"), \
                patch.object(release.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
            with self.assertRaisesRegex(release.BuildError, "pnpm build 执行失败"):
                release.run_build(config, self.options)
            run.assert_called_once_with(["/fake/pnpm", "build"], cwd=self.frontend)
        self.assertEqual(existing.read_bytes(), b"previous release")

    def test_dry_run_and_disabled_frontend_never_execute_pnpm(self) -> None:
        """预演及关闭选项均不查找或调用 pnpm。"""

        with patch.object(release.shutil, "which") as which, patch.object(release.subprocess, "run") as run:
            release.run_build(self.config(), release.CliOptions(
                allow_non_forward_version=False, allow_cross_platform_build=False,
                allow_pyproject_version_mismatch=False, confirm_untracked_include=True,
                confirm_user_assets_change=True, yes=False, no_zip=True, dry_run=True,
            ))
            release.build_frontend(self.config("--no-build-frontend"), dry_run=False)
            which.assert_not_called()
            run.assert_not_called()

    def test_missing_pnpm_and_missing_dist_stop_build(self) -> None:
        """缺少构建工具或成功命令未产出页面时，拒绝继续打包。"""

        config = self.config()
        with patch.object(release.shutil, "which", return_value=None):
            with self.assertRaisesRegex(release.BuildError, "未找到 pnpm"):
                release.build_frontend(config, dry_run=False)
        with patch.object(release.shutil, "which", return_value="/fake/pnpm"), \
                patch.object(release.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            with self.assertRaisesRegex(release.BuildError, "缺少 dist/index.html"):
                release.build_frontend(config, dry_run=False)


if __name__ == "__main__":
    unittest.main()
