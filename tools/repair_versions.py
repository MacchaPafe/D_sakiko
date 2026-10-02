"""读取手动修复的快捷版本选项，不加载客户端或发布工具。"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPAIR_VERSIONS_PATH = Path("tools/repair_versions.json")


def load_repair_versions(path: Path) -> tuple[str, ...]:
    """读取有序版本数组；文件或格式错误由调用方决定如何处理。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("修复版本列表必须是 JSON 字符串数组")
    versions: list[str] = []
    for item in data:
        if not isinstance(item, str) or re.fullmatch(r"v?\d+(?:\.\d+)*", item) is None:
            raise ValueError(f"修复版本列表包含无效版本：{item!r}")
        if item in versions:
            raise ValueError(f"修复版本列表包含重复版本：{item}")
        versions.append(item)
    return tuple(versions)
