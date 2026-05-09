#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def load_private_key(secret_text: str) -> Ed25519PrivateKey:
    """从 base64 raw seed、base64 DER 或 PEM secret 读取 Ed25519 私钥。"""

    text = secret_text.strip()
    if not text:
        raise RuntimeError("私钥 secret 为空")
    if "BEGIN" in text:
        key = serialization.load_pem_private_key(text.encode("utf-8"), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise RuntimeError("PEM 私钥不是 Ed25519 类型")
        return key

    raw = base64.b64decode(text)
    if len(raw) == 32:
        return Ed25519PrivateKey.from_private_bytes(raw)
    key = serialization.load_der_private_key(raw, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeError("DER 私钥不是 Ed25519 类型")
    return key


def load_private_key_from_env(env_name: str) -> Ed25519PrivateKey:
    """从环境变量读取 Ed25519 私钥。"""

    secret = os.environ.get(env_name, "")
    return load_private_key(secret)


def sign_file(path: Path, private_key: Ed25519PrivateKey) -> str:
    """对文件原始字节签名，返回 base64 signature。"""

    signature = private_key.sign(path.read_bytes())
    return base64.b64encode(signature).decode("ascii")


def write_signature_file(signature_b64: str, output_path: Path) -> None:
    """写出 .sig 文件。"""

    output_path.write_text(signature_b64.strip() + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="为 patch zip 生成 Ed25519 detached signature。")
    parser.add_argument("--private-key-env", default="UPDATE_ED25519_PRIVATE_KEY", help="保存私钥的环境变量名。")
    parser.add_argument("--private-key-file", default="", help="本地私钥文件，调试时使用。")
    parser.add_argument("--input", required=True, help="待签名 patch zip。")
    parser.add_argument("--output", required=True, help="输出 .sig 文件。")
    return parser.parse_args()


def main() -> int:
    """CLI 入口。"""

    args = parse_args()
    if args.private_key_file:
        private_key = load_private_key(Path(args.private_key_file).read_text(encoding="utf-8"))
    else:
        private_key = load_private_key_from_env(args.private_key_env)
    signature = sign_file(Path(args.input), private_key)
    write_signature_file(signature, Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

