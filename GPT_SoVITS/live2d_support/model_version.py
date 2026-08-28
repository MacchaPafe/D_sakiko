from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast


Live2DVersion = Literal["v2", "v3"]


def read_live2d_model_json(model_json_path: str) -> dict[str, object]:
    """读取 Live2D 模型 JSON，并保证顶层是对象。"""
    with open(model_json_path, "r", encoding="utf-8") as model_file:
        data = json.load(model_file)
    if not isinstance(data, dict):
        raise ValueError(f"Live2D 模型 JSON 顶层不是对象：{model_json_path}")
    return cast(dict[str, object], data)


def detect_live2d_runtime_version(model_json_path: str) -> Live2DVersion:
    """根据模型文件名和 JSON 结构判断 Live2D runtime 版本。"""
    path = Path(model_json_path)
    if path.name.endswith(".model3.json"):
        return "v3"

    data = read_live2d_model_json(model_json_path)
    if isinstance(data.get("FileReferences"), dict):
        return "v3"
    if isinstance(data.get("model"), str):
        return "v2"

    raise ValueError(f"无法识别 Live2D 模型版本：{model_json_path}")
