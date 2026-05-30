#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""generate_live2d_model_json.py

在缺失 Live2D v2 `*.model.json`（模型描述文件）时，尽可能从模型目录中自动采集信息并生成。

能自动采集的内容（基于文件系统扫描）：
- `.moc` 模型文件
- `.png` 纹理贴图（按常见命名进行自然排序）
- `.mtn` 动作文件（生成 `motions`，确保 `idle` 组至少包含 1 个动作）
- `.exp.json` 表情文件（生成 `expressions`）
- `.physics.json` 物理文件（生成 `physics`）

无法从 `.moc` 中可靠自动提取的内容（需要手动补全/借助查看器）：
- HitArea 的 `id`（`hit_areas`）
- 布局/边界（`layout` 或你在 Live2DViewerEx 里看到的 `bounds`）
- 交互控制器（某些第三方查看器的 `controllers`）

注意：本项目有“不要使用中文路径”的提示。该脚本会对非 ASCII 路径给出警告，但仍会继续生成文件。

用法示例：
    python GPT_SoVITS/tools/generate_live2d_model_json.py \
        --model-dir "live2d_related/tomori/extra_model/冬季校服" \
        --output "3.model.json"

如果目录里有多个 `.moc` 或贴图命名不规范，可以手动指定：
    python GPT_SoVITS/tools/generate_live2d_model_json.py \
        --model-dir ... \
        --moc "tomori_school_winter-2023.moc" \
        --textures texture_00.png texture_01.png
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


def _has_non_ascii(s: str) -> bool:
    return any(ord(ch) > 127 for ch in s)


_natural_number_re = re.compile(r"(\d+)")


def _natural_sort_key(text: str):
    """对包含数字的文件名进行自然排序：texture_2.png < texture_10.png"""

    parts = _natural_number_re.split(text)
    key = []
    for p in parts:
        if p.isdigit():
            key.append(int(p))
        else:
            key.append(p.lower())
    return key


def _rel_posix(path: Path, base_dir: Path) -> str:
    return path.relative_to(base_dir).as_posix()


def _find_files(model_dir: Path, suffix: str) -> List[Path]:
    return sorted([p for p in model_dir.iterdir() if p.is_file() and p.name.lower().endswith(suffix)], key=lambda p: _natural_sort_key(p.name))


def _pick_one(paths: List[Path], kind: str, preferred_name: Optional[str]) -> Optional[Path]:
    if not paths:
        return None
    if preferred_name:
        for p in paths:
            if p.name == preferred_name:
                return p
        raise FileNotFoundError(f"指定的 {kind} 文件不存在：{preferred_name}")
    if len(paths) == 1:
        return paths[0]
    # 多个候选时：尽量挑选最像主文件的（例如名字里不带 extra/alt）
    for p in paths:
        if re.search(r"(main|default|base)", p.stem, flags=re.IGNORECASE):
            return p
    # 否则选第一个（自然排序）
    return paths[0]


def _guess_textures(model_dir: Path, explicit: Optional[List[str]] = None) -> List[Path]:
    if explicit:
        tex_paths = []
        for name in explicit:
            p = model_dir / name
            if not p.exists():
                raise FileNotFoundError(f"指定的纹理不存在：{name}")
            tex_paths.append(p)
        return tex_paths

    pngs = _find_files(model_dir, ".png")
    if not pngs:
        return []

    # 优先使用 texture_XX.png / tex_XX.png 这类常见命名
    preferred = [
        p
        for p in pngs
        if re.match(r"^(texture|tex)[-_]?\d+.*\.png$", p.name, flags=re.IGNORECASE)
    ]
    if preferred:
        return preferred

    # 否则全量返回（但很可能需要用户手动裁剪/排序）
    return pngs


def _build_motions(mtn_files: List[Path], model_dir: Path) -> dict:
    # LAppModel 会预加载 MotionGroup.IDLE == "idle"，所以必须确保 idle 至少有 1 个。
    def motion_item(p: Path) -> dict:
        return {
            "file": _rel_posix(p, model_dir),
            "fade_in": 500,
            "fade_out": 500,
        }

    idle_candidates = [p for p in mtn_files if re.search(r"\bidle\b", p.stem, flags=re.IGNORECASE) or p.stem.lower().startswith("idle")]
    if not idle_candidates and mtn_files:
        idle_candidates = [mtn_files[0]]

    motions = {}
    if idle_candidates:
        motions["idle"] = [motion_item(p) for p in idle_candidates]

    # 额外提供一个 all 组，方便在某些查看器/调试时快速遍历
    if mtn_files:
        motions["all"] = [motion_item(p) for p in mtn_files]

    return motions


def generate_model_json(
    model_dir: Path,
    output_path: Path,
    moc_name: Optional[str] = None,
    textures: Optional[List[str]] = None,
    physics_name: Optional[str] = None,
) -> dict:
    moc_files = _find_files(model_dir, ".moc")
    moc_path = _pick_one(moc_files, kind=".moc", preferred_name=moc_name)
    if moc_path is None:
        raise FileNotFoundError(f"在目录中未找到 .moc：{model_dir}")

    texture_paths = _guess_textures(model_dir, explicit=textures)

    physics_files = [
        p for p in model_dir.iterdir() if p.is_file() and p.name.lower().endswith(".physics.json")
    ]
    physics_files = sorted(physics_files, key=lambda p: _natural_sort_key(p.name))
    physics_path = _pick_one(physics_files, kind=".physics.json", preferred_name=physics_name)

    mtn_files = _find_files(model_dir, ".mtn")
    exp_files = [
        p for p in model_dir.iterdir() if p.is_file() and p.name.lower().endswith(".exp.json")
    ]
    exp_files = sorted(exp_files, key=lambda p: _natural_sort_key(p.name))

    data = {
        # live2d-py 的 ModelSettingJson 并不强依赖 version/type，但保留它们对兼容第三方查看器更友好
        "version": "3.1",
        "type": 0,
        "model": _rel_posix(moc_path, model_dir),
        "textures": [_rel_posix(p, model_dir) for p in texture_paths],
        "motions": _build_motions(mtn_files, model_dir),
        "expressions": [
            {"name": p.name[: -len(".exp.json")], "file": _rel_posix(p, model_dir)}
            for p in exp_files
        ],
        # hit_areas 基本无法从 moc 自动恢复，先生成空列表，后续可手动补
        "hit_areas": [],
    }

    if physics_path is not None:
        data["physics"] = _rel_posix(physics_path, model_dir)

    # 如果纹理为空，仍然写出（避免 key 缺失），但模型大概率无法正常显示
    if "textures" not in data:
        data["textures"] = []

    # 输出路径通常在 model_dir 下
    output_path.parent.mkdir(parents=True, exist_ok=True)

    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="从 Live2D v2 模型目录自动生成 *.model.json")
    parser.add_argument("--model-dir", required=True, help="模型目录（包含 .moc/.png/.mtn 等文件）")
    parser.add_argument("--output", default="3.model.json", help="输出文件名或路径（默认：3.model.json）")
    parser.add_argument("--moc", default=None, help="当目录中有多个 .moc 时，手动指定使用哪个")
    parser.add_argument("--physics", default=None, help="当目录中有多个 .physics.json 时，手动指定使用哪个")
    parser.add_argument(
        "--textures",
        nargs="*",
        default=None,
        help="手动指定纹理列表（顺序很重要），例如: --textures texture_00.png texture_01.png",
    )

    args = parser.parse_args()

    model_dir = Path(args.model_dir).expanduser().resolve()
    if not model_dir.exists() or not model_dir.is_dir():
        raise FileNotFoundError(f"模型目录不存在或不是目录：{model_dir}")

    if _has_non_ascii(str(model_dir)):
        print(f"[WARN] 检测到非 ASCII 路径：{model_dir}")
        print("       本项目文档提示尽量避免中文路径；若运行时异常，请将项目移动到纯英文路径。")

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = (model_dir / output_path).resolve()

    data = generate_model_json(
        model_dir=model_dir,
        output_path=output_path,
        moc_name=args.moc,
        textures=args.textures,
        physics_name=args.physics,
    )

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    print(f"[OK] 已生成：{output_path}")
    print(f"     model: {data.get('model')}")
    print(f"     textures: {len(data.get('textures', []))}")
    print(f"     motions.idle: {len(data.get('motions', {}).get('idle', []))}")
    print(f"     expressions: {len(data.get('expressions', []))}")
    if data.get("physics"):
        print(f"     physics: {data.get('physics')}")

    if not data.get("textures"):
        print("[WARN] 未发现任何 .png 纹理文件，模型很可能无法正常显示。")
    if not data.get("motions") or not data.get("motions", {}).get("idle"):
        print("[WARN] 未发现任何 .mtn 动作文件或未能生成 idle 组；模型将缺少待机动作。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
