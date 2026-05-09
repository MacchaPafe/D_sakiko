#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


PATCH_NAME_RE = re.compile(
    r"^(?P<app_id>D_sakiko)_(?P<base>[^_]+)_to_(?P<target>[^_]+)_(?P<platform>windows|macos)"
    r"(?:_(?P<arch>x64|arm64|universal))?\.patch\.zip$"
)


def parse_patch_filename(filename: str) -> dict[str, str]:
    """从 D_sakiko_2.7.0_to_2.7.1_macos.patch.zip 解析版本和平台。"""

    match = PATCH_NAME_RE.match(filename)
    if match is None:
        raise ValueError(f"补丁文件名不符合规范：{filename}")
    groups = match.groupdict()
    arch = groups.get("arch") or ("universal" if groups["platform"] == "macos" else "x64")
    return {
        "app_id": groups["app_id"],
        "base_version": groups["base"],
        "target_version": groups["target"],
        "platform": groups["platform"],
        "arch": arch,
    }


def load_previous_index(path: Path | None) -> dict[str, object]:
    """读取最新正式 release 中的旧 update_index.json。"""

    if path is None or not path.exists() or path.stat().st_size == 0:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("旧 update_index.json 顶层结构必须是对象")
    return dict(data)


def build_release_record(
    version: str,
    title: str,
    summary: str,
    notes_urls: list[dict[str, str]],
    critical: bool,
) -> dict[str, object]:
    """构造 releases 中的版本展示信息。"""

    return {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "title": title.strip() or version,
        "summary": summary.strip(),
        "notes_urls": notes_urls,
        "critical": critical,
    }


def _asset_download_url(asset: dict[str, object], owner: str, repo: str, tag: str) -> str:
    """从 asset 元数据中解析浏览器下载 URL。"""

    for key in ("browser_download_url", "download_url"):
        value = asset.get(key)
        if isinstance(value, str) and value:
            return value
    name = str(asset.get("name") or "")
    if not owner or not repo or not tag or not name:
        value = asset.get("url")
        if isinstance(value, str):
            return value
        raise RuntimeError(f"asset 缺少下载 URL：{asset}")
    return f"https://github.com/{owner}/{repo}/releases/download/{tag}/{quote(name)}"


def build_patch_record(
    asset: dict[str, object],
    sha256: str,
    signature_b64: str,
    urls: list[dict[str, str]],
) -> dict[str, object]:
    """构造 patches 中的一条边。"""

    name = str(asset.get("name") or "")
    parsed = parse_patch_filename(name)
    size_value = asset.get("size")
    size = size_value if isinstance(size_value, int) and not isinstance(size_value, bool) else 0
    return {
        "base_version": parsed["base_version"],
        "target_version": parsed["target_version"],
        "platform": parsed["platform"],
        "arch": parsed["arch"],
        "file": name,
        "size": size,
        "sha256": sha256.strip().lower(),
        "signature": signature_b64.strip(),
        "urls": urls,
    }


def _version_key(version: str) -> tuple[int, ...]:
    """将版本号转换为可比较元组。"""

    return tuple(int(part or "0") for part in re.findall(r"\d+", version))


def prune_unsupported_patches(index: dict[str, object]) -> dict[str, object]:
    """移除低于 min_supported 且不再需要的 patch。"""

    min_supported = str(index.get("min_supported") or "")
    raw_patches = index.get("patches")
    if not isinstance(raw_patches, list) or not min_supported:
        return index
    index["patches"] = [
        patch for patch in raw_patches
        if isinstance(patch, dict) and _version_key(str(patch.get("base_version") or "")) >= _version_key(min_supported)
    ]
    return index


def merge_index(
    previous: dict[str, object],
    new_patches: list[dict[str, object]],
    release_info: dict[str, object],
    latest: str,
    min_supported: str,
) -> dict[str, object]:
    """合并旧索引和新 patch，保留 min_supported 到 latest 的补丁链。"""

    releases: dict[str, object] = {}
    old_releases = previous.get("releases")
    if isinstance(old_releases, dict):
        releases.update(old_releases)
    releases[latest] = release_info

    patch_key_to_patch: dict[tuple[str, str, str, str], dict[str, object]] = {}
    old_patches = previous.get("patches")
    if isinstance(old_patches, list):
        for patch in old_patches:
            if not isinstance(patch, dict):
                continue
            key = (
                str(patch.get("base_version") or ""),
                str(patch.get("target_version") or ""),
                str(patch.get("platform") or ""),
                str(patch.get("arch") or ""),
            )
            patch_key_to_patch[key] = dict(patch)
    for patch in new_patches:
        key = (
            str(patch.get("base_version") or ""),
            str(patch.get("target_version") or ""),
            str(patch.get("platform") or ""),
            str(patch.get("arch") or ""),
        )
        patch_key_to_patch[key] = patch

    merged: dict[str, object] = {
        "schema": 1,
        "app_id": "D_sakiko",
        "channel": "stable",
        "latest": latest,
        "min_supported": min_supported,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "releases": releases,
        "patches": sorted(
            patch_key_to_patch.values(),
            key=lambda patch: (
                str(patch.get("base_version") or ""),
                str(patch.get("target_version") or ""),
                str(patch.get("platform") or ""),
                str(patch.get("arch") or ""),
            ),
        ),
    }
    return prune_unsupported_patches(merged)


def _load_assets(path: Path) -> list[dict[str, object]]:
    """读取 release asset 列表。"""

    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        assets = data.get("assets")
    else:
        assets = data
    if not isinstance(assets, list):
        raise RuntimeError("assets-json 必须是数组或包含 assets 数组的对象")
    return [dict(item) for item in assets if isinstance(item, dict)]


def _load_notes_urls(path: Path) -> list[dict[str, str]]:
    """读取 release notes 候选 URL 列表。"""

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError("notes-urls-json 必须是数组")
    result: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        url = item.get("url")
        if isinstance(name, str) and isinstance(url, str):
            result.append({"name": name, "url": url})
    return result


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="合并 release assets 并生成 update_index.json。")
    parser.add_argument("--previous-index", default="", help="旧 update_index.json。")
    parser.add_argument("--assets-json", required=True, help="release assets JSON。")
    parser.add_argument("--latest", required=True, help="最新版本号。")
    parser.add_argument("--min-supported", required=True, help="最低支持版本号。")
    parser.add_argument("--release-title", required=True, help="当前 release 标题。")
    parser.add_argument("--release-summary", required=True, help="当前 release 摘要。")
    parser.add_argument("--notes-urls-json", required=True, help="release_notes.md 候选 URL JSON。")
    parser.add_argument("--critical", default="false", choices=["true", "false"], help="是否紧急更新。")
    parser.add_argument("--github-owner", default="", help="GitHub owner，用于补全下载 URL。")
    parser.add_argument("--github-repo", default="", help="GitHub repo，用于补全下载 URL。")
    parser.add_argument("--gitee-owner", default="", help="Gitee owner，用于补全下载 URL。")
    parser.add_argument("--gitee-repo", default="", help="Gitee repo，用于补全下载 URL。")
    parser.add_argument("--tag", required=True, help="release tag。")
    parser.add_argument("--output", default="update_index.json", help="输出路径。")
    return parser.parse_args()


def _read_sidecar_text(asset_name: str, suffix: str) -> str:
    """读取与 asset 同目录的 sidecar 文件。"""

    return Path(asset_name + suffix).read_text(encoding="utf-8").strip()


def main() -> int:
    """CLI 入口。"""

    args = parse_args()
    previous_index = load_previous_index(Path(args.previous_index) if args.previous_index else None)
    assets = _load_assets(Path(args.assets_json))
    patch_assets = [asset for asset in assets if str(asset.get("name") or "").endswith(".patch.zip")]
    new_patches: list[dict[str, object]] = []
    for asset in patch_assets:
        name = str(asset.get("name") or "")
        github_url = _asset_download_url(asset, args.github_owner, args.github_repo, args.tag)
        urls = [{"name": "GitHub", "url": github_url}]
        if args.gitee_owner and args.gitee_repo:
            urls.append({
                "name": "Gitee",
                "url": f"https://gitee.com/{args.gitee_owner}/{args.gitee_repo}/releases/download/{args.tag}/{quote(name)}",
            })
        new_patches.append(
            build_patch_record(
                asset=asset,
                sha256=_read_sidecar_text(name, ".sha256"),
                signature_b64=_read_sidecar_text(name, ".sig"),
                urls=urls,
            )
        )

    release_info = build_release_record(
        version=args.latest,
        title=args.release_title,
        summary=args.release_summary,
        notes_urls=_load_notes_urls(Path(args.notes_urls_json)),
        critical=args.critical == "true",
    )
    merged = merge_index(previous_index, new_patches, release_info, args.latest, args.min_supported)
    Path(args.output).write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

