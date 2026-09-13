from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import Literal

UpdatePlatform = Literal["windows", "macos"]
UpdateArch = Literal["x64", "arm64"]

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
