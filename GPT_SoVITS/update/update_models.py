from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


UpdateChannel = Literal["stable"]
UpdatePlatform = Literal["windows", "macos"]
UpdateArch = Literal["x64", "arm64", "universal"]


@dataclass(frozen=True)
class ReleaseNoteUrl:
    """描述一个 release notes 候选地址。"""

    name: str
    url: str


@dataclass(frozen=True)
class ReleaseInfo:
    """描述一个版本在更新 UI 中展示的信息。"""

    version: str
    date: str
    title: str
    summary: str
    notes_urls: tuple[ReleaseNoteUrl, ...]
    critical: bool = False


@dataclass(frozen=True)
class PatchUrl:
    """描述一个补丁包下载候选地址。"""

    name: str
    url: str


@dataclass(frozen=True)
class PatchInfo:
    """描述补丁图中的一条版本边。"""

    base_version: str
    target_version: str
    platform: UpdatePlatform
    arch: UpdateArch
    file: str
    size: int
    sha256: str
    signature: str
    urls: tuple[PatchUrl, ...]


@dataclass(frozen=True)
class UpdateIndex:
    """描述远端 update_index.json 的规范化内容。"""

    schema: int
    app_id: str
    channel: UpdateChannel
    latest: str
    min_supported: str
    generated_at: str
    releases: dict[str, ReleaseInfo]
    patches: tuple[PatchInfo, ...]


@dataclass(frozen=True)
class UpdatePlan:
    """描述客户端从当前版本更新到最新版本的执行计划。"""

    current_version: str
    target_version: str
    releases: tuple[ReleaseInfo, ...]
    patches: tuple[PatchInfo, ...]
    total_size: int
    critical: bool


@dataclass(frozen=True)
class DownloadedPatch:
    """描述已经下载、校验并解压完成的补丁包。"""

    patch: PatchInfo
    zip_path: Path
    package_dir: Path


def _require_dict(data: object, field_name: str) -> dict[str, object]:
    """读取字典字段，类型不符合时抛出错误。"""

    if not isinstance(data, dict):
        raise ValueError(f"{field_name} 必须是对象")
    return dict(data)


def _require_list(data: object, field_name: str) -> list[object]:
    """读取列表字段，类型不符合时抛出错误。"""

    if not isinstance(data, list):
        raise ValueError(f"{field_name} 必须是数组")
    return data


def _require_str(data: object, field_name: str) -> str:
    """读取字符串字段，空值或类型不符合时抛出错误。"""

    if not isinstance(data, str) or not data.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return data.strip()


def _optional_str(data: object, default: str = "") -> str:
    """读取可选字符串字段，缺省时返回默认值。"""

    if data is None:
        return default
    if not isinstance(data, str):
        return default
    return data.strip()


def _optional_bool(data: object, default: bool = False) -> bool:
    """读取可选布尔字段，缺省时返回默认值。"""

    if isinstance(data, bool):
        return data
    return default


def _require_int(data: object, field_name: str) -> int:
    """读取整数字段，类型不符合时抛出错误。"""

    if isinstance(data, bool) or not isinstance(data, int):
        raise ValueError(f"{field_name} 必须是整数")
    return data


def _parse_channel(value: object) -> UpdateChannel:
    """解析更新通道。"""

    channel = _require_str(value, "channel")
    if channel != "stable":
        raise ValueError(f"不支持的更新通道：{channel}")
    return "stable"


def _parse_platform(value: object) -> UpdatePlatform:
    """解析补丁平台。"""

    platform = _require_str(value, "platform")
    if platform not in {"windows", "macos"}:
        raise ValueError(f"不支持的平台：{platform}")
    return "windows" if platform == "windows" else "macos"


def _parse_arch(value: object) -> UpdateArch:
    """解析补丁架构。"""

    arch = _require_str(value, "arch")
    if arch not in {"x64", "arm64", "universal"}:
        raise ValueError(f"不支持的架构：{arch}")
    if arch == "x64":
        return "x64"
    if arch == "arm64":
        return "arm64"
    return "universal"


def release_note_url_from_dict(data: dict[str, object]) -> ReleaseNoteUrl:
    """解析 release note 的候选 URL。"""

    return ReleaseNoteUrl(
        name=_require_str(data.get("name"), "notes_urls[].name"),
        url=_require_str(data.get("url"), "notes_urls[].url"),
    )


def release_info_from_dict(version: str, data: dict[str, object]) -> ReleaseInfo:
    """解析单个 release 展示信息。"""

    raw_urls = _require_list(data.get("notes_urls", []), f"releases[{version}].notes_urls")
    notes_urls = tuple(
        release_note_url_from_dict(_require_dict(item, "notes_urls[]"))
        for item in raw_urls
    )
    return ReleaseInfo(
        version=version,
        date=_optional_str(data.get("date"), ""),
        title=_optional_str(data.get("title"), version) or version,
        summary=_optional_str(data.get("summary"), ""),
        notes_urls=notes_urls,
        critical=_optional_bool(data.get("critical"), False),
    )


def patch_url_from_dict(data: dict[str, object]) -> PatchUrl:
    """解析补丁下载候选 URL。"""

    return PatchUrl(
        name=_require_str(data.get("name"), "patch.urls[].name"),
        url=_require_str(data.get("url"), "patch.urls[].url"),
    )


def patch_info_from_dict(data: dict[str, object]) -> PatchInfo:
    """解析单个 patch 边。"""

    raw_urls = _require_list(data.get("urls"), "patch.urls")
    urls = tuple(patch_url_from_dict(_require_dict(item, "patch.urls[]")) for item in raw_urls)
    if not urls:
        raise ValueError("patch.urls 不能为空")
    size = _require_int(data.get("size"), "patch.size")
    if size < 0:
        raise ValueError("patch.size 不能为负数")
    return PatchInfo(
        base_version=_require_str(data.get("base_version"), "patch.base_version"),
        target_version=_require_str(data.get("target_version"), "patch.target_version"),
        platform=_parse_platform(data.get("platform")),
        arch=_parse_arch(data.get("arch")),
        file=_require_str(data.get("file"), "patch.file"),
        size=size,
        sha256=_require_str(data.get("sha256"), "patch.sha256").lower(),
        signature=_require_str(data.get("signature"), "patch.signature"),
        urls=urls,
    )


def parse_update_index(data: dict[str, object]) -> UpdateIndex:
    """从 JSON dict 解析并校验 UpdateIndex 基本结构。"""

    raw_releases = _require_dict(data.get("releases"), "releases")
    releases: dict[str, ReleaseInfo] = {}
    for version, release_data in raw_releases.items():
        releases[version] = release_info_from_dict(
            version,
            _require_dict(release_data, f"releases[{version}]"),
        )

    raw_patches = _require_list(data.get("patches"), "patches")
    patches = tuple(patch_info_from_dict(_require_dict(item, "patches[]")) for item in raw_patches)
    latest = _require_str(data.get("latest"), "latest")
    if latest not in releases:
        releases[latest] = ReleaseInfo(
            version=latest,
            date="",
            title=latest,
            summary="",
            notes_urls=(),
            critical=False,
        )
    return UpdateIndex(
        schema=_require_int(data.get("schema"), "schema"),
        app_id=_require_str(data.get("app_id"), "app_id"),
        channel=_parse_channel(data.get("channel")),
        latest=latest,
        min_supported=_require_str(data.get("min_supported"), "min_supported"),
        generated_at=_optional_str(data.get("generated_at"), ""),
        releases=releases,
        patches=patches,
    )

