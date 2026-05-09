from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


PUBLIC_KEY_B64 = "WpVtdEyFYj6lxdNvNJXaVDBhq/UvYUIFEjNs5RDmgwA="


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    """计算文件 SHA256。"""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(block_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected_sha256: str) -> None:
    """校验文件 SHA256，不匹配时抛出 RuntimeError。"""

    actual_sha256 = sha256_file(path)
    if actual_sha256.lower() != expected_sha256.strip().lower():
        raise RuntimeError(f"SHA256 校验失败：期望 {expected_sha256}，实际 {actual_sha256}")


def _decode_base64_text(text: str, field_name: str) -> bytes:
    """解码 base64 文本，失败时抛出 RuntimeError。"""

    try:
        return base64.b64decode(text.strip(), validate=True)
    except Exception as exc:
        raise RuntimeError(f"{field_name} 不是合法 base64：{exc}") from exc


def _resolve_public_key(public_key_b64: str) -> str:
    """解析可用公钥，允许环境变量覆盖内置值。"""

    env_key = os.environ.get("DSAKIKO_UPDATE_PUBLIC_KEY_B64", "").strip()
    resolved = env_key or public_key_b64.strip()
    if not resolved:
        raise RuntimeError("未配置更新签名公钥，请设置 PUBLIC_KEY_B64 或 DSAKIKO_UPDATE_PUBLIC_KEY_B64")
    return resolved


def verify_ed25519_signature(
    path: Path,
    signature_b64: str,
    public_key_b64: str = PUBLIC_KEY_B64,
) -> None:
    """校验 patch zip 的 Ed25519 detached signature。"""

    public_key_bytes = _decode_base64_text(_resolve_public_key(public_key_b64), "公钥")
    signature = _decode_base64_text(signature_b64, "签名")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature, path.read_bytes())
    except InvalidSignature as exc:
        raise RuntimeError("Ed25519 签名校验失败") from exc
    except ValueError as exc:
        raise RuntimeError(f"Ed25519 公钥格式错误：{exc}") from exc


def verify_patch_asset(path: Path, expected_sha256: str, signature_b64: str) -> None:
    """先校验 SHA256，再校验 Ed25519 签名。"""

    verify_sha256(path, expected_sha256)
    verify_ed25519_signature(path, signature_b64)

