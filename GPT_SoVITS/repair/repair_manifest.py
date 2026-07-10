from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath


APP_ID = "D_sakiko"
MANIFEST_SCHEMA = 1
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MODE_PATTERN = re.compile(r"^0[0-7]{3}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
VERSION_PATTERN = re.compile(r"^v?\d+(?:\.\d+)*$")
TOP_LEVEL_FIELDS = {
    "schema",
    "app_id",
    "channel",
    "version",
    "platform",
    "arch",
    "min_repair_client_version",
    "generated_at",
    "files",
}
FILE_FIELDS = {"path", "sha256", "size", "mode"}


@dataclass(frozen=True)
class RepairFileEntry:
    """描述 manifest 中一个可修复文件。"""

    path: str
    sha256: str
    size: int
    mode: str


@dataclass(frozen=True)
class RepairManifest:
    """描述当前版本和平台的完整修复清单。"""

    schema: int
    app_id: str
    channel: str
    version: str
    platform: str
    arch: str
    min_repair_client_version: str
    generated_at: str
    files: tuple[RepairFileEntry, ...]


def sha256_bytes(content: bytes) -> str:
    """计算字节内容的 SHA256。"""

    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    """以流式方式计算文件 SHA256。"""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_manifest_path(value: str, field_name: str = "files[].path") -> str:
    """校验并返回安全的 POSIX 相对路径。"""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    text = value.strip()
    if "\\" in text:
        raise ValueError(f"{field_name} 不允许使用反斜杠：{value!r}")
    posix_path = PurePosixPath(text)
    windows_path = PureWindowsPath(text)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ValueError(f"{field_name} 必须是相对路径：{value!r}")
    if any(part in {"", ".", ".."} for part in posix_path.parts):
        raise ValueError(f"{field_name} 不允许包含空段、当前目录或上级目录：{value!r}")
    return posix_path.as_posix()


def resolve_under_root(root: Path, relative_path: str, field_name: str = "files[].path") -> Path:
    """把合法相对路径解析到 root 下并再次验证包含关系。"""

    normalized = normalize_manifest_path(relative_path, field_name)
    resolved_root = root.resolve()
    target = (resolved_root / PurePosixPath(normalized)).resolve(strict=False)
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError(f"{field_name} 解析后超出允许目录：{relative_path!r}")
    return target


def version_key(version: str) -> tuple[int, ...]:
    """把简单数字版本转换成可比较元组。"""

    raw = version.strip()
    if VERSION_PATTERN.fullmatch(raw) is None:
        raise ValueError(f"版本号格式无效：{version!r}")
    text = raw.lstrip("v")
    values: list[int] = []
    for segment in text.split("."):
        match = re.match(r"^(\d+)", segment)
        if match is None:
            raise ValueError(f"版本号格式无效：{version!r}")
        values.append(int(match.group(1)))
    return tuple(values)


def compare_versions(left: str, right: str) -> int:
    """比较两个版本并返回 -1、0 或 1。"""

    left_values = list(version_key(left))
    right_values = list(version_key(right))
    width = max(len(left_values), len(right_values))
    left_values.extend([0] * (width - len(left_values)))
    right_values.extend([0] * (width - len(right_values)))
    return (left_values > right_values) - (left_values < right_values)


def object_relative_path(sha256_value: str) -> PurePosixPath:
    """根据 SHA256 返回内容寻址 object 相对路径。"""

    normalized = sha256_value.strip().lower()
    if SHA256_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"SHA256 格式无效：{sha256_value!r}")
    return PurePosixPath("objects", "sha256", normalized[:2], normalized[2:4], normalized)


def _require_exact_fields(data: dict[str, object], fields: set[str], location: str) -> None:
    """要求 JSON 对象字段与 schema 完全一致。"""

    missing = sorted(fields - data.keys())
    unknown = sorted(data.keys() - fields)
    if missing:
        raise ValueError(f"{location} 缺少字段：{', '.join(missing)}")
    if unknown:
        raise ValueError(f"{location} 包含未知字段：{', '.join(unknown)}")


def _require_string(data: dict[str, object], key: str, location: str) -> str:
    """读取必需的非空字符串字段。"""

    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} 必须是非空字符串")
    return value.strip()


def parse_manifest_dict(data: dict[str, object], *, check_case_conflicts: bool = False) -> RepairManifest:
    """严格解析修复 manifest 字典。"""

    _require_exact_fields(data, TOP_LEVEL_FIELDS, "manifest")
    schema = data["schema"]
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != MANIFEST_SCHEMA:
        raise ValueError(f"不支持的 manifest schema：{schema!r}")
    raw_files = data["files"]
    if not isinstance(raw_files, list):
        raise ValueError("manifest.files 必须是数组")
    entries: list[RepairFileEntry] = []
    seen_paths: set[str] = set()
    seen_casefold: dict[str, str] = {}
    for index, raw_entry in enumerate(raw_files):
        if not isinstance(raw_entry, dict):
            raise ValueError(f"manifest.files[{index}] 必须是对象")
        entry = {str(key): value for key, value in raw_entry.items()}
        location = f"manifest.files[{index}]"
        _require_exact_fields(entry, FILE_FIELDS, location)
        path = normalize_manifest_path(_require_string(entry, "path", location), f"{location}.path")
        if path in seen_paths:
            raise ValueError(f"manifest.files[].path 重复：{path}")
        casefolded = path.casefold()
        if check_case_conflicts and casefolded in seen_casefold:
            raise ValueError(f"路径存在大小写不敏感冲突：{seen_casefold[casefolded]} / {path}")
        sha256_value = _require_string(entry, "sha256", location).lower()
        if SHA256_PATTERN.fullmatch(sha256_value) is None:
            raise ValueError(f"{location}.sha256 格式无效")
        size = entry["size"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(f"{location}.size 必须是非负整数")
        mode = _require_string(entry, "mode", location)
        if MODE_PATTERN.fullmatch(mode) is None:
            raise ValueError(f"{location}.mode 格式无效")
        entries.append(RepairFileEntry(path=path, sha256=sha256_value, size=size, mode=mode))
        seen_paths.add(path)
        seen_casefold[casefolded] = path

    manifest = RepairManifest(
        schema=schema,
        app_id=_require_string(data, "app_id", "manifest"),
        channel=_require_string(data, "channel", "manifest"),
        version=_require_string(data, "version", "manifest"),
        platform=_require_string(data, "platform", "manifest"),
        arch=_require_string(data, "arch", "manifest"),
        min_repair_client_version=_require_string(data, "min_repair_client_version", "manifest"),
        generated_at=_require_string(data, "generated_at", "manifest"),
        files=tuple(entries),
    )
    version_key(manifest.version)
    version_key(manifest.min_repair_client_version)
    if IDENTIFIER_PATTERN.fullmatch(manifest.app_id) is None:
        raise ValueError("manifest.app_id 格式无效")
    if IDENTIFIER_PATTERN.fullmatch(manifest.channel) is None:
        raise ValueError("manifest.channel 格式无效")
    if manifest.platform not in {"macos", "windows"}:
        raise ValueError(f"manifest.platform 无效：{manifest.platform}")
    if manifest.arch not in {"x64", "arm64", "universal"}:
        raise ValueError(f"manifest.arch 无效：{manifest.arch}")
    try:
        datetime.fromisoformat(manifest.generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("manifest.generated_at 必须是 ISO 8601 时间") from exc
    return manifest


def parse_manifest_bytes(content: bytes, *, check_case_conflicts: bool = False) -> RepairManifest:
    """从 UTF-8 JSON 原始字节严格解析修复 manifest。"""

    try:
        decoded: object = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest 不是合法 UTF-8 JSON：{exc}") from exc
    if not isinstance(decoded, dict):
        raise ValueError("manifest 顶层必须是对象")
    data = {str(key): value for key, value in decoded.items()}
    return parse_manifest_dict(data, check_case_conflicts=check_case_conflicts)


def manifest_to_dict(manifest: RepairManifest) -> dict[str, object]:
    """把 manifest 模型转换成稳定字段顺序的 JSON 字典。"""

    return {
        "schema": manifest.schema,
        "app_id": manifest.app_id,
        "channel": manifest.channel,
        "version": manifest.version,
        "platform": manifest.platform,
        "arch": manifest.arch,
        "min_repair_client_version": manifest.min_repair_client_version,
        "generated_at": manifest.generated_at,
        "files": [
            {"path": entry.path, "sha256": entry.sha256, "size": entry.size, "mode": entry.mode}
            for entry in manifest.files
        ],
    }
