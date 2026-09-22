"""提供世界书规范化 JSON 与文件摘要。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical_json_bytes(value: object) -> bytes:
    """把 JSON 兼容值编码为稳定的 UTF-8 字节。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    """计算 JSON 兼容值的规范化 SHA-256。"""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    """流式计算文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
