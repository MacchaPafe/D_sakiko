"""加载并校验字幕 OCR 布局档案。"""

from __future__ import annotations

import json
from pathlib import Path

from .models import RelativeRegion, SubtitleOCRProfile


PROFILE_ROOT = Path(__file__).resolve().parent / "profiles"
DEFAULT_PROFILE_ID = "bilibili_1080p"


def load_profile(profile: str | Path = DEFAULT_PROFILE_ID) -> SubtitleOCRProfile:
    """按内置名称或 JSON 路径加载字幕布局档案。"""

    candidate = Path(profile)
    profile_path = candidate if candidate.suffix.lower() == ".json" else PROFILE_ROOT / f"{profile}.json"
    if not profile_path.exists():
        raise FileNotFoundError(f"字幕 OCR 布局档案不存在: {profile_path}")
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"字幕 OCR 布局档案必须是 JSON 对象: {profile_path}")
    return SubtitleOCRProfile.model_validate(payload)


def region_pixels(region: RelativeRegion, width: int, height: int) -> tuple[int, int, int, int]:
    """把相对矩形换算为像素坐标。"""

    return (
        int(width * region.left),
        int(height * region.top),
        int(width * region.right),
        int(height * region.bottom),
    )
