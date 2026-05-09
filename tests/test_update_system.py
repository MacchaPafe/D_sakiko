from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "GPT_SoVITS"))
sys.path.insert(0, str(ROOT))

from tools.release.generate_update_index import parse_patch_filename
from update.update_checker import build_update_plan
from update.update_models import parse_update_index
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


if __name__ == "__main__":
    unittest.main()

