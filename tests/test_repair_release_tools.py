from __future__ import annotations

# ruff: noqa: E402

import base64
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "GPT_SoVITS"))
sys.path.insert(0, str(ROOT))

from tools.release.build_repair_manifest import RepairBuildProfile, build_delivery, zip_path_for_output
from tools.release.file_selection import FileSelectionRules
from tools.release.upload_repair_assets import (
    create_r2_client,
    ensure_signature,
    safe_extract_zip,
    upload_delivery,
    validate_delivery,
)


class NotFoundError(Exception):
    """模拟 S3 HeadObject 的对象不存在错误。"""

    def __init__(self) -> None:
        """构造 boto3 风格的错误响应。"""

        super().__init__("not found")
        self.response: dict[str, object] = {"Error": {"Code": "404"}}


class FakeR2Client:
    """记录测试上传顺序的最小 R2 客户端。"""

    def __init__(self) -> None:
        """初始化空对象集合和调用记录。"""

        self.objects: set[str] = set()
        self.puts: list[dict[str, object]] = []
        self.uploads: list[dict[str, object]] = []

    def head_object(self, **kwargs: object) -> object:
        """在对象不存在时抛出模拟 404。"""

        key = str(kwargs["Key"])
        if key not in self.objects:
            raise NotFoundError()
        return {}

    def put_object(self, **kwargs: object) -> object:
        """记录上传参数并把 key 标记为存在。"""

        self.objects.add(str(kwargs["Key"]))
        self.puts.append(dict(kwargs))
        return {}

    def upload_file(self, **kwargs: object) -> object:
        """模拟托管上传并按实际文件大小触发进度回调。"""

        self.objects.add(str(kwargs["Key"]))
        self.uploads.append(dict(kwargs))
        callback = kwargs["Callback"]
        if not callable(callback):
            raise TypeError("Callback 必须可调用")
        callback(Path(str(kwargs["Filename"])).stat().st_size)
        return {}


class FakeBotoModule:
    """记录 boto3.client 创建参数的测试替身。"""

    def __init__(self, client: FakeR2Client) -> None:
        """保存待返回客户端并初始化调用记录。"""

        self.client_instance = client
        self.calls: list[tuple[str, dict[str, object]]] = []

    def client(self, service_name: str, **kwargs: object) -> FakeR2Client:
        """记录服务名和 R2 配置后返回测试客户端。"""

        self.calls.append((service_name, dict(kwargs)))
        return self.client_instance


class RepairReleaseToolsTest(unittest.TestCase):
    """验证修复资产生成、交付校验和上传顺序。"""

    def test_zip_path_preserves_full_version_and_profile(self) -> None:
        """多段版本号不得被 Path suffix 规则截断。"""

        output = Path("dist/repair/D_sakiko_3.2.0_macos-arm64_repair_assets")

        self.assertEqual(
            zip_path_for_output(output),
            Path("dist/repair/D_sakiko_3.2.0_macos-arm64_repair_assets.zip"),
        )

    def test_r2_client_uses_cloudflare_auto_region(self) -> None:
        """上传器应显式使用 Cloudflare R2 要求的 auto region。"""

        boto_module = FakeBotoModule(FakeR2Client())
        with patch("tools.release.upload_repair_assets.importlib.import_module", return_value=boto_module):
            client = create_r2_client("https://account.r2.cloudflarestorage.com")

        self.assertIs(client, boto_module.client_instance)
        self.assertEqual(
            boto_module.calls,
            [
                (
                    "s3",
                    {
                        "endpoint_url": "https://account.r2.cloudflarestorage.com",
                        "region_name": "auto",
                    },
                )
            ],
        )

    def test_build_delivery_uses_package_bytes_and_exclusions(self) -> None:
        """生成器只应纳入发布包中的允许文件和平台 include。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            package = root / "package"
            output = root / "delivery"
            source.mkdir()
            (package / "GPT_SoVITS" / "configs").mkdir(parents=True)
            (package / "runtime").mkdir()
            (package / "GPT_SoVITS" / "main2.py").write_text("released bytes\n", encoding="utf-8")
            (package / "GPT_SoVITS" / "configs" / "tts_infer.yaml").write_text("device: cpu\n", encoding="utf-8")
            (package / "runtime" / "python.exe").write_bytes(b"runtime")
            (package / "run.command").write_text("#!/bin/bash\n", encoding="utf-8")
            (package / "version.json").write_text('{"version":"3.2.0"}', encoding="utf-8")
            profile = RepairBuildProfile(
                name="macos-arm64",
                app_id="D_sakiko",
                channel="stable",
                platform_name="macos",
                arch="arm64",
                min_repair_client_version="1.0.0",
                rules=FileSelectionRules(
                    allow=("GPT_SoVITS/*.py", "GPT_SoVITS/**/*.py", "version.json"),
                    include=("*.command",),
                    hard_exclude=("runtime/**", "GPT_SoVITS/configs/*.yaml"),
                ),
            )
            tracked = {
                "GPT_SoVITS/main2.py",
                "GPT_SoVITS/configs/tts_infer.yaml",
                "version.json",
            }
            with patch("tools.release.build_repair_manifest.list_git_tracked_files", return_value=tracked):
                result = build_delivery(
                    source,
                    package,
                    profile,
                    "3.2.0",
                    output,
                    zip_file=output.with_suffix(".zip"),
                    overwrite=False,
                )

            paths = {entry.path for entry in result.manifest.files}
            self.assertEqual(paths, {"GPT_SoVITS/main2.py", "run.command", "version.json"})
            self.assertEqual(result.selection.forced_untracked, frozenset({"run.command"}))
            self.assertTrue(result.zip_file and result.zip_file.exists())
            validate_delivery(output)

    def test_safe_extract_rejects_parent_escape(self) -> None:
        """协作者 zip 中的上级目录条目必须被拒绝。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../manifest.json", "{}")
            with self.assertRaises(RuntimeError):
                safe_extract_zip(archive_path, root / "out")

    def test_upload_order_is_objects_signature_manifest(self) -> None:
        """R2 发布入口必须最后出现，避免客户端看见未完成资产。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content = b"official"
            sha256_value = __import__("hashlib").sha256(content).hexdigest()
            object_path = root / "objects" / "sha256" / sha256_value[:2] / sha256_value[2:4] / sha256_value
            object_path.parent.mkdir(parents=True)
            object_path.write_bytes(content)
            manifest = {
                "schema": 1,
                "app_id": "D_sakiko",
                "channel": "stable",
                "version": "3.2.0",
                "platform": "macos",
                "arch": "arm64",
                "min_repair_client_version": "1.0.0",
                "generated_at": "2026-07-10T00:00:00+00:00",
                "files": [{"path": "main.py", "sha256": sha256_value, "size": len(content), "mode": "0644"}],
            }
            manifest_bytes = (json.dumps(manifest) + "\n").encode("utf-8")
            (root / "manifest.json").write_bytes(manifest_bytes)
            private_key = Ed25519PrivateKey.generate()
            signature = base64.b64encode(private_key.sign(manifest_bytes)) + b"\n"
            old_key = os.environ.get("DSAKIKO_UPDATE_PUBLIC_KEY_B64")
            os.environ["DSAKIKO_UPDATE_PUBLIC_KEY_B64"] = base64.b64encode(
                private_key.public_key().public_bytes_raw()
            ).decode("ascii")
            try:
                delivery = validate_delivery(root)
                client = FakeR2Client()
                actions = upload_delivery(
                    client,
                    "bucket",
                    "repair",
                    delivery,
                    signature,
                    overwrite_manifest=False,
                )
            finally:
                if old_key is None:
                    os.environ.pop("DSAKIKO_UPDATE_PUBLIC_KEY_B64", None)
                else:
                    os.environ["DSAKIKO_UPDATE_PUBLIC_KEY_B64"] = old_key

        self.assertTrue(actions[0].startswith("put repair/objects/"))
        self.assertTrue(actions[-2].endswith(".json.sig"))
        self.assertTrue(actions[-1].endswith(".json"))
        extra_args = client.uploads[0]["ExtraArgs"]
        self.assertIsInstance(extra_args, dict)
        self.assertEqual(extra_args["CacheControl"], "public, max-age=31536000, immutable")
        self.assertEqual(client.puts[-1]["CacheControl"], "no-store")

    def test_upload_stage_signs_unsigned_collaborator_delivery(self) -> None:
        """最终上传方应能为协作者未签名交付物补签。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content = b"official"
            sha256_value = __import__("hashlib").sha256(content).hexdigest()
            object_path = root / "objects" / "sha256" / sha256_value[:2] / sha256_value[2:4] / sha256_value
            object_path.parent.mkdir(parents=True)
            object_path.write_bytes(content)
            manifest = {
                "schema": 1,
                "app_id": "D_sakiko",
                "channel": "stable",
                "version": "3.2.0",
                "platform": "windows",
                "arch": "x64",
                "min_repair_client_version": "1.0.0",
                "generated_at": "2026-07-10T00:00:00+00:00",
                "files": [{"path": "main.py", "sha256": sha256_value, "size": len(content), "mode": "0644"}],
            }
            (root / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            delivery = validate_delivery(root)
            private_key = Ed25519PrivateKey.generate()
            old_private = os.environ.get("TEST_REPAIR_PRIVATE_KEY")
            old_public = os.environ.get("DSAKIKO_UPDATE_PUBLIC_KEY_B64")
            os.environ["TEST_REPAIR_PRIVATE_KEY"] = base64.b64encode(private_key.private_bytes_raw()).decode("ascii")
            os.environ["DSAKIKO_UPDATE_PUBLIC_KEY_B64"] = base64.b64encode(
                private_key.public_key().public_bytes_raw()
            ).decode("ascii")
            try:
                signature = ensure_signature(
                    delivery,
                    resign=False,
                    private_key_file=None,
                    private_key_env="TEST_REPAIR_PRIVATE_KEY",
                )
                signature_exists = delivery.signature_file.exists()
            finally:
                if old_private is None:
                    os.environ.pop("TEST_REPAIR_PRIVATE_KEY", None)
                else:
                    os.environ["TEST_REPAIR_PRIVATE_KEY"] = old_private
                if old_public is None:
                    os.environ.pop("DSAKIKO_UPDATE_PUBLIC_KEY_B64", None)
                else:
                    os.environ["DSAKIKO_UPDATE_PUBLIC_KEY_B64"] = old_public

        self.assertTrue(signature.endswith(b"\n"))
        self.assertTrue(signature_exists)


if __name__ == "__main__":
    unittest.main()
