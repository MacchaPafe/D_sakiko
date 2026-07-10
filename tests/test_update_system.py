from __future__ import annotations

# ruff: noqa: E402

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "GPT_SoVITS"))
sys.path.insert(0, str(ROOT))

from tools.apply_update_patch import (
    UpdateResultRecorder,
    build_default_log_file,
    default_hpatch_bin,
    detect_arch,
    detect_platform,
    manifest_requires_macos_uv_sync,
    normalize_manifest_path,
    verify_manifest_and_version,
)
from tools.build_diff_patch import FileRecord, write_manifest
from tools.release.generate_update_index import parse_patch_filename
from tools.release.verify_update_assets import verify_patch_chain
from update.update_checker import build_update_plan
from update.update_launcher import build_apply_command, launch_detached_process
from update.update_models import parse_update_index
from update.update_paths import get_update_log_dir, get_update_result_file
from update.update_security import verify_patch_asset


class UpdateSystemTest(unittest.TestCase):
    """验证自动更新系统的核心纯逻辑。"""

    def test_build_update_plan_prefers_short_path(self) -> None:
        """补丁图生成应优先选择补丁数量更少的路径。"""

        index = parse_update_index(
            {
                "schema": 1,
                "app_id": "D_sakiko",
                "channel": "stable",
                "latest": "2.7.0",
                "min_supported": "2.6.5",
                "generated_at": "2026-05-09T00:00:00+00:00",
                "releases": {
                    "2.6.6": {"title": "2.6.6", "summary": "", "notes_urls": [], "critical": False},
                    "2.7.0": {"title": "2.7.0", "summary": "修复问题", "notes_urls": [], "critical": True},
                },
                "patches": [
                    {
                        "base_version": "2.6.5",
                        "target_version": "2.6.6",
                        "platform": "macos",
                        "arch": "universal",
                        "file": "a.zip",
                        "size": 10,
                        "sha256": "0" * 64,
                        "signature": "sig",
                        "urls": [{"name": "GitHub", "url": "https://example.com/a.zip"}],
                    },
                    {
                        "base_version": "2.6.6",
                        "target_version": "2.7.0",
                        "platform": "macos",
                        "arch": "universal",
                        "file": "b.zip",
                        "size": 10,
                        "sha256": "0" * 64,
                        "signature": "sig",
                        "urls": [{"name": "GitHub", "url": "https://example.com/b.zip"}],
                    },
                    {
                        "base_version": "2.6.5",
                        "target_version": "2.7.0",
                        "platform": "macos",
                        "arch": "universal",
                        "file": "c.zip",
                        "size": 100,
                        "sha256": "0" * 64,
                        "signature": "sig",
                        "urls": [{"name": "GitHub", "url": "https://example.com/c.zip"}],
                    },
                ],
            }
        )

        plan = build_update_plan(index, "2.6.5", "macos", "arm64")

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.target_version, "2.7.0")
        self.assertEqual([patch.file for patch in plan.patches], ["c.zip"])
        self.assertTrue(plan.critical)

    def test_verify_patch_asset_with_env_public_key(self) -> None:
        """补丁包校验应同时验证 SHA256 和 Ed25519 签名。"""

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        public_key_bytes = public_key.public_bytes_raw()
        with tempfile.TemporaryDirectory() as temp_dir:
            patch_file = Path(temp_dir) / "patch.zip"
            patch_file.write_bytes(b"demo patch")
            signature_b64 = base64.b64encode(private_key.sign(patch_file.read_bytes())).decode("ascii")
            sha256_value = __import__("hashlib").sha256(patch_file.read_bytes()).hexdigest()
            old_env = os.environ.get("DSAKIKO_UPDATE_PUBLIC_KEY_B64")
            os.environ["DSAKIKO_UPDATE_PUBLIC_KEY_B64"] = base64.b64encode(public_key_bytes).decode("ascii")
            try:
                verify_patch_asset(patch_file, sha256_value, signature_b64)
            finally:
                if old_env is None:
                    os.environ.pop("DSAKIKO_UPDATE_PUBLIC_KEY_B64", None)
                else:
                    os.environ["DSAKIKO_UPDATE_PUBLIC_KEY_B64"] = old_env

    def test_parse_patch_filename_supports_implicit_arch(self) -> None:
        """补丁文件名解析应兼容未显式写架构的旧命名。"""

        macos = parse_patch_filename("D_sakiko_2.6.5_to_2.7.0_macos.patch.zip")
        windows = parse_patch_filename("D_sakiko_2.6.5_to_2.7.0_windows.patch.zip")

        self.assertEqual(macos["arch"], "universal")
        self.assertEqual(windows["arch"], "x64")

    def test_verify_patch_chain_only_checks_present_platforms(self) -> None:
        """发布校验不应因为未发布的平台缺席而失败。"""

        index = {
            "patches": [
                {
                    "base_version": "2.6.5",
                    "target_version": "2.7.0",
                    "platform": "macos",
                    "arch": "universal",
                }
            ]
        }

        verify_patch_chain(index, "2.6.5", "2.7.0")

    def test_verify_patch_chain_rejects_incomplete_present_platform(self) -> None:
        """已经出现在 index 中的平台如果链路断裂，发布校验应失败。"""

        index = {
            "patches": [
                {
                    "base_version": "2.6.5",
                    "target_version": "2.7.0",
                    "platform": "macos",
                    "arch": "universal",
                },
                {
                    "base_version": "2.6.9",
                    "target_version": "2.7.0",
                    "platform": "windows",
                    "arch": "x64",
                },
            ]
        }

        with self.assertRaises(RuntimeError):
            verify_patch_chain(index, "2.6.5", "2.7.0")

    def test_manifest_path_rejects_directory_escape(self) -> None:
        """更新包 manifest 中的文件路径不允许逃逸 app_root。"""

        with self.assertRaises(RuntimeError):
            normalize_manifest_path("../version.json", "files[].path")
        with self.assertRaises(RuntimeError):
            normalize_manifest_path("/tmp/version.json", "files[].path")
        with self.assertRaises(RuntimeError):
            normalize_manifest_path("C:\\temp\\version.json", "files[].path")

    def test_verify_modified_file_reports_all_file_mismatches(self) -> None:
        """预校验应一次性报告所有文件级不匹配问题。"""

        from tools.apply_update_patch import verify_modified_file

        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir)
            first_file = app_root / "first.txt"
            second_file = app_root / "second.txt"
            first_file.write_text("current first", encoding="utf-8")
            second_file.write_text("current second", encoding="utf-8")
            manifest = {
                "files": [
                    {
                        "action": "modify",
                        "path": "first.txt",
                        "old_file_sha256": "0" * 64,
                    },
                    {
                        "action": "remove",
                        "path": "second.txt",
                        "old_file_sha256": "1" * 64,
                    },
                    {
                        "action": "modify",
                        "path": "missing.txt",
                        "old_file_sha256": "2" * 64,
                    },
                ]
            }

            with self.assertRaises(RuntimeError) as context:
                verify_modified_file(manifest, app_root)

            message = str(context.exception)
            self.assertIn("共发现 3 个问题", message)
            self.assertIn("待修改文件 SHA256 校验失败：first.txt", message)
            self.assertIn("待删除文件 SHA256 校验失败：second.txt", message)
            self.assertIn("待修改文件不存在或不是普通文件：missing.txt", message)

    def test_default_hpatch_bin_uses_windows_exe(self) -> None:
        """Windows 默认 hpatchz 路径应包含 .exe 扩展名。"""

        self.assertEqual(default_hpatch_bin("win32"), "tools/hpatchz.exe")
        self.assertEqual(default_hpatch_bin("darwin"), "tools/hpatchz")

    def test_verify_manifest_checks_identity_and_platform(self) -> None:
        """本地 updater 应在应用前再次校验补丁身份和平台架构。"""

        manifest = {
            "mode": "hdiff",
            "app_id": "D_sakiko",
            "channel": "stable",
            "platform": detect_platform(),
            "arch": detect_arch(),
            "min_updater_version": "1.0.0",
            "base_version": "2.6.5",
            "target_version": "2.7.0",
        }

        self.assertEqual(verify_manifest_and_version(manifest, "2.6.5"), ("2.6.5", "2.7.0"))
        bad_manifest = dict(manifest)
        bad_manifest["app_id"] = "OtherApp"
        with self.assertRaises(RuntimeError):
            verify_manifest_and_version(bad_manifest, "2.6.5")

    def test_build_apply_command_passes_explicit_packages(self) -> None:
        """自动更新启动 updater 时应只传入本次下载的 package。"""

        app_root = Path("/tmp/D_sakiko")
        command = build_apply_command(
            app_root=app_root,
            package_dirs=[app_root / ".updates/packages/2.7.0/a", app_root / ".updates/packages/2.7.1/b"],
            wait_pid=123,
            restart_command=None,
            log_file=None,
        )

        self.assertEqual(command.count("--package"), 2)
        self.assertIn(str(app_root / ".updates/packages/2.7.0/a"), command)
        self.assertIn(str(app_root / ".updates/packages/2.7.1/b"), command)

    def test_build_apply_command_passes_status_file_when_requested(self) -> None:
        """自动更新启动 updater 时应可要求更新器写结果记录。"""

        app_root = Path("/tmp/D_sakiko")
        status_file = app_root / "logs/update/last_update_result.json"
        command = build_apply_command(
            app_root=app_root,
            package_dirs=[],
            wait_pid=None,
            restart_command=None,
            log_file=None,
            status_file=status_file,
        )

        self.assertIn("--status-file", command)
        self.assertIn(str(status_file), command)

    @patch("update.update_launcher.subprocess.Popen")
    def test_detached_launcher_uses_new_session_on_unix(self, popen: object) -> None:
        """Unix detached 进程必须脱离主程序所在会话。"""

        launch_detached_process(["python", "worker.py"], Path("/tmp/D_sakiko"))

        call = popen.call_args
        self.assertTrue(call.kwargs["start_new_session"])

    def test_update_log_dir_uses_explicit_app_root(self) -> None:
        """自动更新日志目录应基于传入的 app_root。"""

        app_root = Path("/tmp/D_sakiko")

        self.assertEqual(get_update_log_dir(app_root), app_root / "logs" / "update")

    def test_update_result_file_uses_visible_log_dir(self) -> None:
        """更新结果记录应与更新日志位于同一可见目录。"""

        app_root = Path("/tmp/D_sakiko")

        self.assertEqual(get_update_result_file(app_root), app_root / "logs" / "update" / "last_update_result.json")

    def test_manual_update_default_log_file_uses_visible_log_dir(self) -> None:
        """手动更新默认日志应与自动更新使用同一日志目录。"""

        app_root = Path("/tmp/D_sakiko")

        self.assertEqual(
            build_default_log_file(app_root, "20260612_123456"),
            app_root / "logs" / "update" / "update_manual_20260612_123456.log",
        )

    def test_update_result_recorder_writes_relative_log_path(self) -> None:
        """更新结果记录中的日志路径应相对 app_root 保存。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir)
            log_file = app_root / "logs" / "update" / "update_1.0.0_to_1.1.0.log"
            status_file = app_root / "logs" / "update" / "last_update_result.json"
            recorder = UpdateResultRecorder(app_root, status_file, log_file)

            recorder.set_versions("1.0.0", "1.1.0")
            recorder.write_failed(rollback_performed=False, rollback_succeeded=None)

            result = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["base_version"], "1.0.0")
            self.assertEqual(result["target_version"], "1.1.0")
            self.assertEqual(result["rollback_performed"], False)
            self.assertIsNone(result["rollback_succeeded"])
            self.assertEqual(result["log_file"], "logs/update/update_1.0.0_to_1.1.0.log")
            self.assertEqual(result["notified"], False)

    def test_update_result_recorder_records_successful_rollback(self) -> None:
        """应用阶段失败且回滚成功时应记录回滚成功。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir)
            status_file = app_root / "logs" / "update" / "last_update_result.json"
            recorder = UpdateResultRecorder(app_root, status_file, app_root / "logs" / "update" / "update.log")

            recorder.write_failed(rollback_performed=True, rollback_succeeded=True)

            result = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["rollback_performed"], True)
            self.assertEqual(result["rollback_succeeded"], True)

    def test_write_manifest_marks_macos_uv_sync(self) -> None:
        """macOS 补丁修改依赖声明时应写入 uv sync 后处理标记。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_file = write_manifest(
                output_root=Path(temp_dir),
                manifest_name="manifest.json",
                patch_file_name="patch.hdiff",
                base_version="2.6.5",
                target_version="2.7.0",
                app_id="D_sakiko",
                channel="stable",
                platform="macos",
                arch="universal",
                min_updater_version="1.0.0",
                ignore_patterns=[],
                include_patterns=[],
                records=[FileRecord(path="uv.lock", action="modify", sha256="0" * 64, size=10, old_file_sha256="0" * 64)],
                remove_files=[],
                added_files=[],
                changed_files=["uv.lock"],
            )

            manifest = __import__("json").loads(manifest_file.read_text(encoding="utf-8"))

        self.assertEqual(manifest["post_update"], {"macos_uv_sync": True})
        self.assertTrue(manifest_requires_macos_uv_sync(manifest))

    def test_manifest_uv_sync_fallback_detects_dependency_files(self) -> None:
        """旧 manifest 缺少 post_update 时仍应根据文件列表识别依赖更新。"""

        manifest = {
            "files": [
                {"path": "pyproject.toml", "action": "modify"},
            ]
        }

        self.assertTrue(manifest_requires_macos_uv_sync(manifest))

    def test_list_files_with_ignored_parent_directory(self) -> None:
        """即使父文件夹被忽略，若其中的具体文件命中 include，也应正确包含。"""
        from tools.build_diff_patch import list_files

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            # 创建被忽略目录及内部文件
            wheels_dir = temp_path / "wheels"
            wheels_dir.mkdir()
            
            target_file = wheels_dir / "live2d_py-0.7.0.4-cp311-cp311-macosx_15_0_arm64.whl"
            target_file.write_text("dummy content")
            
            other_file = wheels_dir / "ignored_wheel.whl"
            other_file.write_text("ignored dummy content")

            # 忽略 wheels/** 规则下，若强制包含 target_file
            ignore_patterns = ["wheels/**"]
            include_patterns = ["wheels/live2d_py-0.7.0.4-cp311-cp311-macosx_15_0_arm64.whl"]

            files = list_files(temp_path, ignore_patterns, include_patterns)
            
            self.assertIn("wheels/live2d_py-0.7.0.4-cp311-cp311-macosx_15_0_arm64.whl", files)
            self.assertNotIn("wheels/ignored_wheel.whl", files)


if __name__ == "__main__":
    unittest.main()
