"""小剧场编排的资源投影、时长和初始化，不创建图形运行时。"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from live2d_support.performance_catalog import load_performance_catalog, read_config
from live2d_support.performance_policy import resolve_performance
from live2d_support.motion_semantics import motion_group_for_emotion
from performance_types import PerformanceSelection


@dataclass
class AudioSpan:
    """区分正常无声和损坏音频，避免用降级时长删除原编排。"""

    duration_ms: int
    path: str = ""
    missing: bool = False


def audio_span(path: str, text: str) -> AudioSpan:
    """读取真实音频时长；无声按原文字公式，失败保留缺失标志。"""
    if path:
        import soundfile as sf
        try:
            info = sf.info(path)
            if info.frames > 0 and info.samplerate > 0:
                return AudioSpan(round(info.frames * 1000 / info.samplerate), path)
        except (OSError, RuntimeError):
            pass
    return AudioSpan(100 * len(text) + 500, missing=bool(path))


class TheaterResources:
    """将当前服装实际资源转为两种运行时共用的编辑和匹配信息。"""

    def __init__(self, model_path: str, direction: str = "C"):
        """读取模型索引；V2 使用相对文件路径作为逻辑键。"""
        self.path = Path(model_path) if model_path else None
        self.direction = direction
        self.version = None
        self.catalog = None
        self.items = {"motions": [], "expressions": []}
        self.duration_cache = {}
        if self.path is None or not self.path.is_file():
            return
        data = read_config(self.path)
        self.catalog = load_performance_catalog(self.path)
        self.version = self.catalog.version
        if self.version == "v3":
            for kind in self.items:
                values = self.catalog.motions if kind == "motions" else self.catalog.expressions
                for key in values:
                    asset = self.catalog.motion(key, direction) if kind == "motions" else None
                    file = asset.file if asset else values[key] if kind == "expressions" else None
                    if file:
                        description = self.catalog.descriptions.get(kind, {}).get(key, "")
                        self.items[kind].append(self._item(key, file, description))
        else:
            files = set()
            for entries in data.get("motions", {}).values():
                for entry in entries:
                    file = entry.get("file")
                    if file and file not in files and (self.path.parent / file).is_file():
                        files.add(file)
                        self.items["motions"].append(self._item(file, file, ""))

    def _item(self, key: str, file: str, description: str) -> dict:
        """从文件名去掉已知资源后缀取得名称，描述只用于搜索和悬停。"""
        name = Path(file).name
        for suffix in (".json", ".motion3", ".exp3", ".mtn", ".exp"):
            if name.endswith(suffix):
                name = name[:-len(suffix)]
        return {"resource": {"logical_id": key, "file_name": Path(file).name},
                "file": file, "name": name, "description": description}

    def resolve(self, kind: str, resource: object):
        """先按逻辑键匹配，再唯一文件名匹配；歧义不猜测。"""
        if not isinstance(resource, dict):
            return None
        for item in self.items[kind]:
            if item["resource"]["logical_id"] == resource.get("logical_id"):
                return item
        matches = [item for item in self.items[kind]
                   if item["resource"]["file_name"] == resource.get("file_name")]
        return matches[0] if len(matches) == 1 else None

    def duration(self, resource: object):
        """缓存具体动作单轮长度，无法读取时返回未知而不是零秒。"""
        item = self.resolve("motions", resource)
        if item is None:
            return None
        file = item["file"]
        if file not in self.duration_cache:
            duration = None
            try:
                path = self.path.parent / file
                if self.version == "v3":
                    value = json.loads(path.read_text(encoding="utf-8-sig"))["Meta"]["Duration"]
                    duration = round(float(value) * 1000)
                else:
                    from live2d.v2.core.motion.live2d_motion import Live2DMotion
                    duration = Live2DMotion.loadMotion(path.read_bytes()).getLoopDurationMSec()
            except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
                duration = None
            self.duration_cache[file] = duration if duration and duration > 0 else None
        return self.duration_cache[file]

    def defaults(self, performance: object, emotion: str) -> dict:
        """把原默认演出解析为明确的句首事件，不在之后的回放重新抽取。"""
        tracks = {"motions": [], "expressions": []}
        if self.catalog is None:
            return tracks
        if self.version == "v3":
            plan = resolve_performance(self.catalog, PerformanceSelection.from_value(performance),
                                       emotion, self.direction)
            selections = {"motions": plan.state.motion_id, "expressions": plan.state.expression}
            for kind, key in selections.items():
                item = self.resolve(kind, {"logical_id": key})
                if item:
                    tracks[kind].append({"at_ms": 0, "resource": item["resource"].copy()})
        else:
            data = read_config(self.path)
            from live2d_support.motion_selection import select_random_motion
            group = motion_group_for_emotion(emotion, default="IDLE")
            files = {key: tuple(entry["file"] for entry in entries if entry.get("file"))
                     for key, entries in data.get("motions", {}).items()}
            chosen = select_random_motion(group, "C", files, files)
            if chosen:
                item = self.resolve("motions", {"logical_id": files[chosen[0]][chosen[1]]})
                if item:
                    tracks["motions"].append({"at_ms": 0, "resource": item["resource"].copy()})
        return tracks


def assign_colors(timeline: dict, palette: list) -> None:
    """仅为缺失颜色的新事件分配持久颜色，重排不改已有颜色。"""
    from performance_types import timeline_tracks, valid_timeline_events
    import re
    colors = list(dict.fromkeys(palette)) or ["#7799CC", "#FF5522"]
    for tracks in timeline_tracks(timeline).values():
        previous = None
        for event in valid_timeline_events(tracks.get("motions"), "motions"):
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(event.get("color", ""))):
                event["color"] = random.choice([color for color in colors if color != previous] or colors)
            previous = event["color"]
