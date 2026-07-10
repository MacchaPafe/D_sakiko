#!/usr/bin/env python3
from __future__ import annotations

# ruff: noqa: E402

import argparse
import base64
import importlib
import os
import stat
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

from tqdm import tqdm


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "GPT_SoVITS"))
sys.path.insert(0, str(REPO_ROOT))

from repair.repair_manifest import RepairManifest, object_relative_path, parse_manifest_bytes, sha256_file
from repair.repair_security import verify_manifest_signature
from tools.release.sign_patch_asset import load_private_key, load_private_key_from_env


class R2Client(Protocol):
    """描述上传工具使用的最小 S3 客户端接口。"""

    def head_object(self, **kwargs: object) -> object:
        """查询对象是否存在。"""

    def put_object(self, **kwargs: object) -> object:
        """上传单个对象。"""

    def upload_file(self, **kwargs: object) -> object:
        """以托管传输方式流式上传本地文件。"""


@dataclass(frozen=True)
class ValidatedDelivery:
    """保存已经完整复核的本地修复交付物。"""

    root: Path
    manifest_file: Path
    signature_file: Path
    manifest_bytes: bytes
    manifest: RepairManifest


def parse_args() -> argparse.Namespace:
    """解析 R2 修复资产上传参数。"""

    parser = argparse.ArgumentParser(description="复核、签名并上传 D_sakiko 修复资产到 Cloudflare R2。")
    parser.add_argument("--input", required=True, help="build_repair_manifest 输出目录或 zip。")
    parser.add_argument("--bucket", default=os.environ.get("R2_BUCKET", ""), help="R2 bucket 名称。")
    parser.add_argument("--endpoint-url", default=os.environ.get("R2_ENDPOINT_URL", ""), help="R2 S3 endpoint。")
    parser.add_argument("--prefix", default="repair", help="bucket 内远端前缀。")
    parser.add_argument("--private-key-env", default="UPDATE_ED25519_PRIVATE_KEY", help="发布私钥环境变量。")
    parser.add_argument("--private-key-file", default="", help="本地 Ed25519 私钥文件。")
    parser.add_argument("--resign-manifest", action="store_true", help="显式重新签名已有 manifest.json.sig。")
    parser.add_argument("--overwrite-manifest", action="store_true", help="显式覆盖已有远端 manifest 和签名。")
    parser.add_argument("--dry-run", action="store_true", help="完成本地复核和签名，但不连接 R2。")
    return parser.parse_args()


def _safe_zip_member(name: str) -> PurePosixPath:
    """校验 zip 条目路径和固定顶层布局。"""

    if not name or "\\" in name:
        raise RuntimeError(f"zip 条目路径非法：{name!r}")
    path = PurePosixPath(name.rstrip("/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError(f"zip 条目越界：{name!r}")
    top = path.parts[0]
    if top not in {"manifest.json", "manifest.json.sig", "audit-report.md", "objects"}:
        raise RuntimeError(f"zip 包含固定布局之外的条目：{name!r}")
    if top != "objects" and len(path.parts) != 1:
        raise RuntimeError(f"zip 条目布局非法：{name!r}")
    return path


def safe_extract_zip(zip_path: Path, destination: Path) -> None:
    """安全解包协作者交付 zip。"""

    destination.mkdir(parents=True, exist_ok=True)
    resolved_destination = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            relative = _safe_zip_member(info.filename)
            unix_mode = info.external_attr >> 16
            if stat.S_ISLNK(unix_mode):
                raise RuntimeError(f"zip 不允许包含符号链接：{info.filename}")
            if relative.parts[0] == "objects":
                if info.is_dir():
                    if len(relative.parts) > 4:
                        raise RuntimeError(f"zip object 目录布局非法：{info.filename}")
                else:
                    parts = relative.parts
                    if (
                        len(parts) != 5
                        or parts[1] != "sha256"
                        or len(parts[2]) != 2
                        or len(parts[3]) != 2
                        or len(parts[4]) != 64
                        or parts[2] != parts[4][:2]
                        or parts[3] != parts[4][2:4]
                        or any(char not in "0123456789abcdef" for char in parts[4])
                    ):
                        raise RuntimeError(f"zip object 文件布局非法：{info.filename}")
            target = (resolved_destination / relative).resolve(strict=False)
            if resolved_destination not in target.parents and target != resolved_destination:
                raise RuntimeError(f"zip 条目解析后越界：{info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)


@contextmanager
def materialize_input(input_path: Path) -> Iterator[Path]:
    """把目录或 zip 统一转换成可读取的交付目录。"""

    resolved = input_path.expanduser().resolve()
    if resolved.is_dir():
        yield resolved
        return
    if not resolved.is_file() or resolved.suffix.lower() != ".zip":
        raise RuntimeError(f"--input 必须是目录或 zip：{resolved}")
    with tempfile.TemporaryDirectory(prefix="d_sakiko_repair_upload_") as temp_dir:
        root = Path(temp_dir) / "delivery"
        safe_extract_zip(resolved, root)
        yield root


def validate_delivery(root: Path) -> ValidatedDelivery:
    """复核 manifest schema、路径及其引用的全部 objects。"""

    manifest_file = root / "manifest.json"
    signature_file = root / "manifest.json.sig"
    if not manifest_file.is_file():
        raise RuntimeError("交付物缺少 manifest.json")
    manifest_bytes = manifest_file.read_bytes()
    manifest = parse_manifest_bytes(manifest_bytes, check_case_conflicts=True)
    for entry in manifest.files:
        object_file = root / object_relative_path(entry.sha256)
        if not object_file.is_file():
            raise RuntimeError(f"交付物缺少 object：{entry.sha256}（{entry.path}）")
        if object_file.stat().st_size != entry.size:
            raise RuntimeError(f"object 大小不匹配：{entry.sha256}（{entry.path}）")
        actual_sha = sha256_file(object_file)
        if actual_sha != entry.sha256:
            raise RuntimeError(f"object SHA256 不匹配：{entry.sha256}，实际 {actual_sha}")
    return ValidatedDelivery(
        root=root,
        manifest_file=manifest_file,
        signature_file=signature_file,
        manifest_bytes=manifest_bytes,
        manifest=manifest,
    )


def ensure_signature(
    delivery: ValidatedDelivery,
    *,
    resign: bool,
    private_key_file: Path | None,
    private_key_env: str,
) -> bytes:
    """验签已有签名，或在明确条件下用发布私钥签名。"""

    if delivery.signature_file.is_file() and not resign:
        signature_bytes = delivery.signature_file.read_bytes()
        verify_manifest_signature(delivery.manifest_bytes, signature_bytes)
        return signature_bytes
    if private_key_file is not None:
        private_key = load_private_key(private_key_file.read_text(encoding="utf-8"))
    else:
        private_key = load_private_key_from_env(private_key_env)
    signature_bytes = base64.b64encode(private_key.sign(delivery.manifest_bytes)) + b"\n"
    delivery.signature_file.write_bytes(signature_bytes)
    verify_manifest_signature(delivery.manifest_bytes, signature_bytes)
    return signature_bytes


def _remote_key(prefix: str, relative: str) -> str:
    """拼接无重复斜杠的 R2 object key。"""

    clean_prefix = prefix.strip("/")
    clean_relative = relative.lstrip("/")
    return f"{clean_prefix}/{clean_relative}" if clean_prefix else clean_relative


def manifest_remote_relative(manifest: RepairManifest) -> str:
    """返回 manifest 在修复资源根目录下的相对 key。"""

    return (
        f"manifests/{manifest.app_id}/{manifest.channel}/{manifest.version}/"
        f"{manifest.platform}-{manifest.arch}.json"
    )


def _is_not_found_error(error: Exception) -> bool:
    """判断 S3 客户端异常是否表示对象不存在。"""

    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return False
    error_data = response.get("Error")
    if not isinstance(error_data, dict):
        return False
    return str(error_data.get("Code", "")) in {"404", "NoSuchKey", "NotFound"}


def object_exists(client: R2Client, bucket: str, key: str) -> bool:
    """使用 HeadObject 判断远端对象是否存在。"""

    try:
        client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _is_not_found_error(exc):
            return False
        raise
    return True


def upload_delivery(
    client: R2Client,
    bucket: str,
    prefix: str,
    delivery: ValidatedDelivery,
    signature_bytes: bytes,
    *,
    overwrite_manifest: bool,
) -> list[str]:
    """按 objects、签名、manifest 的顺序上传完整交付物。"""

    manifest_relative = manifest_remote_relative(delivery.manifest)
    manifest_key = _remote_key(prefix, manifest_relative)
    signature_key = f"{manifest_key}.sig"
    if object_exists(client, bucket, manifest_key) and not overwrite_manifest:
        raise RuntimeError("远端 manifest 已存在；如确需覆盖，请添加 --overwrite-manifest")

    actions: list[str] = []
    pending_objects: list[tuple[Path, str, int]] = []
    unique_hashes = sorted({entry.sha256 for entry in delivery.manifest.files})
    for sha256_value in unique_hashes:
        local_object = delivery.root / object_relative_path(sha256_value)
        object_key = _remote_key(prefix, object_relative_path(sha256_value).as_posix())
        if object_exists(client, bucket, object_key):
            actions.append(f"skip {object_key}")
            continue
        pending_objects.append((local_object, object_key, local_object.stat().st_size))

    total_bytes = sum(size for _, _, size in pending_objects) + len(signature_bytes) + len(delivery.manifest_bytes)
    with tqdm(
        total=total_bytes,
        desc="上传 R2",
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        dynamic_ncols=True,
    ) as progress:
        for local_object, object_key, _ in pending_objects:
            client.upload_file(
                Filename=str(local_object),
                Bucket=bucket,
                Key=object_key,
                ExtraArgs={
                    "ContentType": "application/octet-stream",
                    "CacheControl": "public, max-age=31536000, immutable",
                },
                Callback=progress.update,
            )
            actions.append(f"put {object_key}")
        client.put_object(
            Bucket=bucket,
            Key=signature_key,
            Body=signature_bytes,
            ContentType="text/plain; charset=utf-8",
            CacheControl="no-store",
        )
        progress.update(len(signature_bytes))
        actions.append(f"put {signature_key}")
        client.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=delivery.manifest_bytes,
            ContentType="application/json; charset=utf-8",
            CacheControl="no-store",
        )
        progress.update(len(delivery.manifest_bytes))
        actions.append(f"put {manifest_key}")
    return actions


def create_r2_client(endpoint_url: str) -> R2Client:
    """延迟导入 boto3 并创建 S3 兼容客户端。"""

    try:
        module = importlib.import_module("boto3")
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少发布工具依赖 boto3，请安装项目的 release 可选依赖") from exc
    factory = cast(Callable[..., object], getattr(module, "client"))
    return cast(R2Client, factory("s3", endpoint_url=endpoint_url, region_name="auto"))


def main() -> int:
    """命令行入口。"""

    args = parse_args()
    try:
        with materialize_input(Path(args.input)) as root:
            delivery = validate_delivery(root)
            private_key_file = Path(args.private_key_file).expanduser().resolve() if args.private_key_file else None
            signature_bytes = ensure_signature(
                delivery,
                resign=args.resign_manifest,
                private_key_file=private_key_file,
                private_key_env=args.private_key_env,
            )
            manifest_relative = manifest_remote_relative(delivery.manifest)
            print(f"[复核] {len(delivery.manifest.files)} 个文件，目标：{_remote_key(args.prefix, manifest_relative)}")
            if args.dry_run:
                print("[完成] dry-run：本地复核与签名通过，未连接 R2。")
                return 0
            if not args.bucket or not args.endpoint_url:
                raise RuntimeError("上传需要 --bucket 和 --endpoint-url（或 R2_BUCKET/R2_ENDPOINT_URL）")
            client = create_r2_client(args.endpoint_url)
            actions = upload_delivery(
                client,
                args.bucket,
                args.prefix,
                delivery,
                signature_bytes,
                overwrite_manifest=args.overwrite_manifest,
            )
            for action in actions:
                print(f"[R2] {action}")
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
