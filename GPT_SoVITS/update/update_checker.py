from __future__ import annotations

import json
import os
import platform
import sys
from collections import deque
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .update_models import (
    ReleaseInfo,
    ReleaseNoteUrl,
    PatchInfo,
    UpdateArch,
    UpdateIndex,
    UpdatePlan,
    UpdatePlatform,
    parse_update_index,
)


APP_ID = "D_sakiko"
CHANNEL = "stable"
DEFAULT_INDEX_URLS = (
    "https://github.com/MacchaPafe/D_sakiko/releases/latest/download/update_index.json",
    "https://gitee.com/Li-Yan-Xiao/D_sakiko/releases/download/latest/update_index.json",
)


def get_configured_index_urls() -> tuple[str, ...]:
    """读取内置和环境变量配置的更新索引 URL。"""

    env_urls = tuple(
        item.strip()
        for item in os.environ.get("DSAKIKO_UPDATE_INDEX_URLS", "").split(",")
        if item.strip()
    )
    if env_urls:
        return env_urls
    return tuple(url for url in DEFAULT_INDEX_URLS if "<" not in url and ">" not in url)


def read_current_version(version_file: Path) -> str:
    """读取本地 version.json 中的 version。"""

    data = json.loads(version_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("version.json 顶层结构必须是对象")
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("version.json 缺少 version 字段")
    return version.strip()


def detect_platform() -> UpdatePlatform:
    """返回 windows 或 macos。"""

    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    raise RuntimeError(f"暂不支持当前平台自动更新：{sys.platform}")


def detect_arch() -> UpdateArch:
    """返回 x64、arm64 或 universal。"""

    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64", "x64"}:
        return "x64"
    raise RuntimeError(f"暂不支持当前架构自动更新：{machine}")


def fetch_update_index(url: str, timeout: float = 8.0) -> UpdateIndex:
    """从单个 URL 拉取 update_index.json。"""

    request = Request(url, headers={"User-Agent": "D_sakiko-Updater/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except URLError as exc:
        raise RuntimeError(f"拉取更新索引失败：{url}，错误：{exc}") from exc

    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("update_index.json 顶层结构必须是对象")
    index = parse_update_index(dict(data))
    if index.app_id != APP_ID:
        raise RuntimeError(f"app_id 不匹配：{index.app_id}")
    if index.channel != CHANNEL:
        raise RuntimeError(f"channel 不匹配：{index.channel}")
    return index


def fetch_first_available_index(urls: list[str] | tuple[str, ...]) -> tuple[UpdateIndex, str]:
    """按顺序拉取 index，返回第一个可用结果和来源 URL。"""

    errors: list[str] = []
    for url in urls:
        try:
            return fetch_update_index(url), url
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("所有更新索引 URL 均不可用：\n" + "\n".join(errors))


def _version_key(version: str) -> tuple[int, ...]:
    """将版本号转换为可比较元组。"""

    parts: list[int] = []
    for item in version.strip().lstrip("v").split("."):
        number = ""
        for char in item:
            if char.isdigit():
                number += char
            else:
                break
        parts.append(int(number or "0"))
    return tuple(parts)


def _patch_matches_arch(patch_arch: UpdateArch, current_arch: UpdateArch) -> bool:
    """判断补丁架构是否适配当前架构。"""

    return patch_arch == current_arch or patch_arch == "universal"


def find_patch_path(
    patches: tuple[PatchInfo, ...],
    current_version: str,
    target_version: str,
    platform_name: UpdatePlatform,
    arch: UpdateArch,
) -> tuple[PatchInfo, ...]:
    """在 patch 边列表中搜索版本路径，优先补丁数少、体积小、来源多的路径。"""

    queue: deque[tuple[str, tuple[PatchInfo, ...]]] = deque()
    queue.append((current_version, ()))
    visited_best: dict[str, tuple[int, int]] = {current_version: (0, 0)}
    candidates: list[tuple[PatchInfo, ...]] = []

    while queue:
        version, path = queue.popleft()
        if version == target_version:
            candidates.append(path)
            continue
        for patch in patches:
            if patch.base_version != version:
                continue
            if patch.platform != platform_name or not _patch_matches_arch(patch.arch, arch):
                continue
            new_path = (*path, patch)
            new_size = sum(item.size for item in new_path)
            previous_best = visited_best.get(patch.target_version)
            current_score = (len(new_path), new_size)
            if previous_best is not None and previous_best <= current_score:
                continue
            visited_best[patch.target_version] = current_score
            queue.append((patch.target_version, new_path))

    if not candidates:
        return ()
    return min(
        candidates,
        key=lambda path: (
            len(path),
            sum(item.size for item in path),
            -sum(len(item.urls) for item in path),
        ),
    )


def build_update_plan(
    index: UpdateIndex,
    current_version: str,
    platform_name: UpdatePlatform,
    arch: UpdateArch,
) -> UpdatePlan | None:
    """根据补丁图生成从当前版本到 latest 的更新计划；已是最新则返回 None。"""

    if _version_key(current_version) >= _version_key(index.latest):
        return None
    if _version_key(current_version) < _version_key(index.min_supported):
        raise RuntimeError(f"当前版本 {current_version} 低于最低支持版本 {index.min_supported}，请手动更新。")

    patch_path = find_patch_path(index.patches, current_version, index.latest, platform_name, arch)
    if not patch_path:
        raise RuntimeError(f"未找到 {current_version} -> {index.latest} 的可用补丁链")

    release_infos: list[ReleaseInfo] = []
    for patch in patch_path:
        info = index.releases.get(patch.target_version)
        if info is not None:
            release_infos.append(info)
    return UpdatePlan(
        current_version=current_version,
        target_version=index.latest,
        releases=tuple(release_infos),
        patches=patch_path,
        total_size=sum(patch.size for patch in patch_path),
        critical=any(info.critical for info in release_infos),
    )


def fetch_release_notes(notes_urls: tuple[ReleaseNoteUrl, ...], timeout: float = 8.0) -> tuple[str, str]:
    """按顺序拉取 release_notes.md，返回 markdown 内容和来源名称。"""

    errors: list[str] = []
    for item in notes_urls:
        name = getattr(item, "name", "")
        url = getattr(item, "url", "")
        if not isinstance(name, str) or not isinstance(url, str) or not url:
            continue
        request = Request(url, headers={"User-Agent": "D_sakiko-Updater/1.0"})
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8"), name or url
        except Exception as exc:
            errors.append(f"{name or url}: {exc}")
    raise RuntimeError("所有 release notes URL 均不可用：\n" + "\n".join(errors))
