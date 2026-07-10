from __future__ import annotations

from update.update_security import verify_ed25519_bytes


def verify_manifest_signature(manifest_bytes: bytes, signature_bytes: bytes) -> None:
    """使用更新系统内置公钥校验修复 manifest 原始字节。"""

    try:
        signature_text = signature_bytes.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeError("manifest 签名文件必须是 ASCII Base64 文本") from exc
    verify_ed25519_bytes(manifest_bytes, signature_text)
