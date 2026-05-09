#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from collections import deque
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def _sha256_file(path: Path) -> str:
    """计算文件 SHA256。"""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_patch_triplet(zip_path: Path, sha256_path: Path, sig_path: Path, public_key_b64: str) -> None:
    """校验 zip、sha256、sig 三件套。"""

    expected_sha256 = sha256_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual_sha256 = _sha256_file(zip_path)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(f"SHA256 不匹配：{zip_path.name}")
    public_key_text = public_key_b64.strip()
    if not public_key_text:
        raise RuntimeError("缺少 Ed25519 公钥，请设置 UPDATE_ED25519_PUBLIC_KEY repository variable 或 secret。")
    public_key_bytes = base64.b64decode(public_key_text)
    if len(public_key_bytes) != 32:
        raise RuntimeError(f"Ed25519 公钥必须是 32 字节 raw public key 的 base64，当前解码后为 {len(public_key_bytes)} 字节。")
    public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
    signature = base64.b64decode(sig_path.read_text(encoding="utf-8").strip())
    try:
        public_key.verify(signature, zip_path.read_bytes())
    except InvalidSignature as exc:
        raise RuntimeError(f"签名不匹配：{zip_path.name}") from exc


def verify_patch_chain(index: dict[str, object], min_supported: str, latest: str) -> None:
    """确认 min_supported 到 latest 至少有一条完整路径。"""

    raw_patches = index.get("patches")
    if not isinstance(raw_patches, list):
        raise RuntimeError("index.patches 必须是数组")
    edges: dict[str, set[str]] = {}
    for patch in raw_patches:
        if not isinstance(patch, dict):
            continue
        base = str(patch.get("base_version") or "")
        target = str(patch.get("target_version") or "")
        if base and target:
            edges.setdefault(base, set()).add(target)
    queue: deque[str] = deque([min_supported])
    visited = {min_supported}
    while queue:
        version = queue.popleft()
        if version == latest:
            return
        for target in edges.get(version, set()):
            if target not in visited:
                visited.add(target)
                queue.append(target)
    raise RuntimeError(f"补丁链不完整：{min_supported} -> {latest}")


def verify_index(index_path: Path) -> None:
    """校验 update_index.json 结构和补丁链。"""

    data = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("update_index.json 顶层结构必须是对象")
    for field in ("schema", "app_id", "channel", "latest", "min_supported", "releases", "patches"):
        if field not in data:
            raise RuntimeError(f"update_index.json 缺少字段：{field}")
    latest = str(data["latest"])
    min_supported = str(data["min_supported"])
    verify_patch_chain(data, min_supported, latest)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="校验自动更新 release assets。")
    parser.add_argument("--index", required=True, help="update_index.json 路径。")
    parser.add_argument("--public-key-b64", required=True, help="Ed25519 公钥 base64。")
    parser.add_argument("--patch", action="append", default=[], help="patch zip，可重复。")
    parser.add_argument("--require-release-notes", default="release_notes.md", help="必须存在的公告文件。")
    return parser.parse_args()


def main() -> int:
    """CLI 入口。"""

    args = parse_args()
    verify_index(Path(args.index))
    notes_path = Path(args.require_release_notes)
    if args.require_release_notes and not notes_path.exists():
        raise RuntimeError(f"缺少 release notes：{notes_path}")
    for patch_name in args.patch:
        zip_path = Path(patch_name)
        verify_patch_triplet(
            zip_path=zip_path,
            sha256_path=Path(patch_name + ".sha256"),
            sig_path=Path(patch_name + ".sig"),
            public_key_b64=args.public_key_b64,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
