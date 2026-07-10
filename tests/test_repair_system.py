from __future__ import annotations

# ruff: noqa: E402

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "GPT_SoVITS"))
sys.path.insert(0, str(ROOT))

from repair.repair_manifest import (
    RepairFileEntry,
    RepairManifest,
    compare_versions,
    manifest_to_dict,
    normalize_manifest_path,
    object_relative_path,
    parse_manifest_bytes,
)
from repair.repair_checker import (
    FetchedManifest,
    RepairCandidate,
    RepairCheckResult,
    RepairVersionUnsupported,
    fetch_manifest,
    prepare_repair,
    scan_local_files,
)
from repair.repair_security import verify_manifest_signature


class FakeResponse:
    """提供客户端测试所需的最小 HTTP 响应。"""

    def __init__(self, status_code: int, content: bytes) -> None:
        """保存状态码和响应体。"""

        self.status_code = status_code
        self.content = content

    def iter_content(self, chunk_size: int) -> tuple[bytes, ...]:
        """把响应体按单块返回。"""

        del chunk_size
        return (self.content,)


class FakeSession:
    """按 URL 返回预设响应并记录关闭状态。"""

    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        """保存 URL 到响应的映射。"""

        self.responses = responses
        self.headers: dict[str, str] = {}
        self.closed = False

    def get(
        self,
        url: str,
        *,
        timeout: tuple[float, float],
        stream: bool = False,
    ) -> FakeResponse:
        """返回 URL 对应的预设响应。"""

        del timeout, stream
        return self.responses[url]

    def close(self) -> None:
        """记录会话关闭。"""

        self.closed = True


def make_manifest_bytes(path: str = "GPT_SoVITS/main2.py") -> bytes:
    """构造测试使用的最小合法 manifest。"""

    manifest = RepairManifest(
        schema=1,
        app_id="D_sakiko",
        channel="stable",
        version="3.2.0",
        platform="macos",
        arch="arm64",
        min_repair_client_version="1.0.0",
        generated_at="2026-07-10T00:00:00+00:00",
        files=(RepairFileEntry(path=path, sha256="a" * 64, size=10, mode="0644"),),
    )
    return (json.dumps(manifest_to_dict(manifest), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class RepairManifestTest(unittest.TestCase):
    """验证修复 manifest 的严格解析与签名边界。"""

    def test_parse_valid_manifest(self) -> None:
        """合法 manifest 应保留版本、平台和文件信息。"""

        manifest = parse_manifest_bytes(make_manifest_bytes())

        self.assertEqual(manifest.version, "3.2.0")
        self.assertEqual(manifest.files[0].path, "GPT_SoVITS/main2.py")

    def test_rejects_unknown_and_duplicate_fields(self) -> None:
        """未知字段和重复路径必须关闭修复流程。"""

        data = json.loads(make_manifest_bytes())
        data["unexpected"] = True
        with self.assertRaises(ValueError):
            parse_manifest_bytes(json.dumps(data).encode("utf-8"))

        data.pop("unexpected")
        data["files"].append(dict(data["files"][0]))
        with self.assertRaises(ValueError):
            parse_manifest_bytes(json.dumps(data).encode("utf-8"))

    def test_rejects_version_path_injection(self) -> None:
        """会参与远端 key 拼接的版本字段不得包含路径字符。"""

        data = json.loads(make_manifest_bytes())
        data["version"] = "../../3.2.0"

        with self.assertRaises(ValueError):
            parse_manifest_bytes(json.dumps(data).encode("utf-8"))

    def test_rejects_unsafe_paths(self) -> None:
        """绝对路径、上级目录和反斜杠路径必须被拒绝。"""

        for path in ("../main.py", "/tmp/main.py", "C:/temp/main.py", "GPT_SoVITS\\main.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                normalize_manifest_path(path)

    def test_generation_can_reject_casefold_conflicts(self) -> None:
        """生成侧应拦截大小写不敏感路径冲突。"""

        data = json.loads(make_manifest_bytes("GPT_SoVITS/Foo.py"))
        second = dict(data["files"][0])
        second["path"] = "GPT_SoVITS/foo.py"
        data["files"].append(second)

        with self.assertRaises(ValueError):
            parse_manifest_bytes(json.dumps(data).encode("utf-8"), check_case_conflicts=True)

    def test_object_path_uses_hash_prefix_sharding(self) -> None:
        """object key 应使用 SHA256 前四位分两层。"""

        value = "abcdef" + "0" * 58

        self.assertEqual(object_relative_path(value).as_posix(), f"objects/sha256/ab/cd/{value}")

    def test_version_comparison_normalizes_width(self) -> None:
        """简单数字版本比较应兼容位数不同的版本。"""

        self.assertEqual(compare_versions("1.0", "1.0.0"), 0)
        self.assertLess(compare_versions("1.0.9", "1.1.0"), 0)

    def test_signature_covers_raw_manifest_bytes(self) -> None:
        """签名只对完全相同的 manifest 原始字节有效。"""

        private_key = Ed25519PrivateKey.generate()
        public_key_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode("ascii")
        manifest_bytes = make_manifest_bytes()
        signature_bytes = base64.b64encode(private_key.sign(manifest_bytes)) + b"\n"
        old_key = os.environ.get("DSAKIKO_UPDATE_PUBLIC_KEY_B64")
        os.environ["DSAKIKO_UPDATE_PUBLIC_KEY_B64"] = public_key_b64
        try:
            verify_manifest_signature(manifest_bytes, signature_bytes)
            with self.assertRaises(RuntimeError):
                verify_manifest_signature(manifest_bytes.rstrip(), signature_bytes)
        finally:
            if old_key is None:
                os.environ.pop("DSAKIKO_UPDATE_PUBLIC_KEY_B64", None)
            else:
                os.environ["DSAKIKO_UPDATE_PUBLIC_KEY_B64"] = old_key

    def test_fetch_manifest_distinguishes_all_404(self) -> None:
        """所有源明确 404 时应报告当前版本不支持。"""

        relative = "manifests/D_sakiko/stable/3.2.0/macos-arm64.json"
        session = FakeSession(
            {
                f"https://one/{relative}": FakeResponse(404, b""),
                f"https://two/{relative}": FakeResponse(404, b""),
            }
        )

        with self.assertRaises(RepairVersionUnsupported):
            fetch_manifest(("https://one", "https://two"), "3.2.0", "macos", "arm64", session=session)

    def test_fetch_manifest_uses_same_source_signature_and_fallback(self) -> None:
        """首个源验签失败后应从下一个源成对获取清单和签名。"""

        manifest_bytes = make_manifest_bytes()
        private_key = Ed25519PrivateKey.generate()
        signature = base64.b64encode(private_key.sign(manifest_bytes)) + b"\n"
        relative = "manifests/D_sakiko/stable/3.2.0/macos-arm64.json"
        session = FakeSession(
            {
                f"https://one/{relative}": FakeResponse(200, manifest_bytes),
                f"https://one/{relative}.sig": FakeResponse(200, b"bad"),
                f"https://two/{relative}": FakeResponse(200, manifest_bytes),
                f"https://two/{relative}.sig": FakeResponse(200, signature),
            }
        )
        old_key = os.environ.get("DSAKIKO_UPDATE_PUBLIC_KEY_B64")
        os.environ["DSAKIKO_UPDATE_PUBLIC_KEY_B64"] = base64.b64encode(
            private_key.public_key().public_bytes_raw()
        ).decode("ascii")
        try:
            fetched = fetch_manifest(
                ("https://one", "https://two"),
                "3.2.0",
                "macos",
                "arm64",
                session=session,
            )
        finally:
            if old_key is None:
                os.environ.pop("DSAKIKO_UPDATE_PUBLIC_KEY_B64", None)
            else:
                os.environ["DSAKIKO_UPDATE_PUBLIC_KEY_B64"] = old_key

        self.assertEqual(fetched.source_base_url, "https://two")

    def test_scan_only_reports_missing_or_hash_mismatch(self) -> None:
        """扫描不应检查 mode，也不应处理 manifest 外的额外文件。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir)
            good = app_root / "good.py"
            good.write_bytes(b"good")
            (app_root / "extra.py").write_bytes(b"extra")
            manifest_data = json.loads(make_manifest_bytes("good.py"))
            manifest_data["files"][0]["sha256"] = __import__("hashlib").sha256(b"good").hexdigest()
            manifest_data["files"][0]["size"] = 4
            manifest_data["files"].append(
                {"path": "missing.py", "sha256": "b" * 64, "size": 1, "mode": "0755"}
            )
            manifest = parse_manifest_bytes(json.dumps(manifest_data).encode("utf-8"))

            candidates = scan_local_files(app_root, manifest)

        self.assertEqual([candidate.entry.path for candidate in candidates], ["missing.py"])
        self.assertEqual(candidates[0].original_state, "missing")

    def test_prepare_repair_downloads_and_writes_plan(self) -> None:
        """所有 object 校验完成后才应生成可交接的 repair plan。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir)
            content = b"official bytes"
            sha256_value = __import__("hashlib").sha256(content).hexdigest()
            manifest_data = json.loads(make_manifest_bytes("main.py"))
            manifest_data["files"][0].update({"sha256": sha256_value, "size": len(content)})
            manifest_bytes = json.dumps(manifest_data).encode("utf-8")
            manifest = parse_manifest_bytes(manifest_bytes)
            fetched = FetchedManifest(
                manifest=manifest,
                content=manifest_bytes,
                sha256=__import__("hashlib").sha256(manifest_bytes).hexdigest(),
                source_base_url="https://cdn",
            )
            candidate = RepairCandidate(
                entry=manifest.files[0],
                original_state="missing",
                original_sha256=None,
            )
            result = RepairCheckResult(fetched_manifest=fetched, candidates=(candidate,), base_urls=("https://cdn",))
            object_url = f"https://cdn/objects/sha256/{sha256_value[:2]}/{sha256_value[2:4]}/{sha256_value}"
            session = FakeSession({object_url: FakeResponse(200, content)})

            prepared = prepare_repair(app_root, result, session=session)
            plan = json.loads(prepared.plan_file.read_text(encoding="utf-8"))

        self.assertEqual(plan["files"][0]["path"], "main.py")
        self.assertEqual(plan["files"][0]["original_state"], "missing")
        self.assertEqual(plan["manifest_sha256"], fetched.sha256)


if __name__ == "__main__":
    unittest.main()
