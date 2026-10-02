from __future__ import annotations

import hashlib
import json
import mimetypes
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_ROOT = (PROJECT_ROOT / "live2d_related").resolve()
REFERENCE_AUDIO_ROOT = (PROJECT_ROOT / "reference_audio").resolve()
CHAR_HEADPROF_ROOT = (PROJECT_ROOT / "GPT_SoVITS" / "assets" / "char_headprof").resolve()


@dataclass(frozen=True)
class MediaEntry:
    path: Path
    media_type: str


@dataclass(frozen=True)
class Live2DEntry:
    root: Path
    model_filename: str


class AssetRegistry:
    def __init__(self) -> None:
        self._media: dict[str, MediaEntry] = {}
        self._models: dict[str, Live2DEntry] = {}
        self._lock = Lock()
        self.backgrounds: list[dict[str, Any]] = []
        self.background_index = 0
        self._load_backgrounds()

    def _load_backgrounds(self) -> None:
        paths = sorted(
            path for path in LIVE2D_ROOT.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        )
        colors = ["#CFD9DC", "#D8D1DF", "#CBD8D4", "#E1D6CD"]
        for index, path in enumerate(paths):
            media_id = self.register_media(path, "background")
            self.backgrounds.append({
                "id": f"background_{path.stem}",
                "name": path.stem,
                "image_url": f"/api/v1/media/{media_id}",
                "color": colors[index % len(colors)],
            })
        if not self.backgrounds:
            self.backgrounds.append({
                "id": "background_default",
                "name": "默认背景",
                "image_url": None,
                "color": "#CFD9DC",
            })

    def register_media(self, path: str | Path, kind: str) -> str:
        resolved = Path(path).expanduser().resolve()
        if not any(
            resolved == root or resolved.is_relative_to(root)
            for root in (LIVE2D_ROOT, REFERENCE_AUDIO_ROOT, CHAR_HEADPROF_ROOT)
        ):
            raise ValueError("媒体文件不在允许目录中")
        digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:24]
        media_id = f"media_{kind}_{digest}"
        media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        with self._lock:
            self._media[media_id] = MediaEntry(resolved, media_type)
        return media_id

    def media(self, media_id: str) -> MediaEntry | None:
        return self._media.get(media_id)

    def avatar_options(self, character: Any) -> list[dict[str, str]]:
        """实时枚举该角色所有头像来源；只接受来源目录内的图片。"""
        character_root = LIVE2D_ROOT / character.character_folder_name
        preferred_icon = character_root / f"{character.character_folder_name}_icon.png"
        downloaded_root = CHAR_HEADPROF_ROOT / "webui_chat_mode_avatars" / character.character_folder_name
        sources = [(preferred_icon, character_root)]
        sources.extend((path, character_root) for path in sorted(character_root.glob("*")))
        sources.append((CHAR_HEADPROF_ROOT / f"{character.character_name}.png", CHAR_HEADPROF_ROOT))
        sources.extend((path, downloaded_root) for path in sorted(downloaded_root.rglob("*")))
        options = []
        seen = set()
        revision = time.time_ns()
        for path, root in sources:
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or not path.is_file():
                continue
            resolved = path.resolve()
            # 不通过符号链接读取其他角色或任意主机文件。
            if not resolved.is_relative_to(root.absolute()):
                continue
            if not resolved.is_relative_to(PROJECT_ROOT) or resolved in seen:
                continue
            seen.add(resolved)
            media_id = self.register_media(resolved, "avatar")
            options.append({
                "id": resolved.relative_to(PROJECT_ROOT).as_posix(),
                "name": path.stem,
                "image_url": f"/api/v1/media/{media_id}?v={revision}",
            })
        return options

    def register_character(self, character: Any, avatar_id: str | None = None,
                           *, options: list[dict[str, str]] | None = None) -> dict[str, Any]:
        options = self.avatar_options(character) if options is None else options
        selected = next((item for item in options if item["id"] == avatar_id), None)
        if selected is None:
            # 保持原有默认来源；仅下载的头像由用户明确选择后启用。
            downloaded = (CHAR_HEADPROF_ROOT / "webui_chat_mode_avatars").relative_to(PROJECT_ROOT).as_posix() + "/"
            selected = next((item for item in options if not item["id"].startswith(downloaded)), None)
        avatar_url = selected["image_url"] if selected else None

        palettes = [
            ("#168779", "#DCEFEC"),
            ("#C24F67", "#F7E2E7"),
            ("#486FA8", "#DFE8F5"),
            ("#8A643C", "#F1E7DB"),
            ("#675AA7", "#E9E5F6"),
        ]
        palette_index = int(hashlib.sha256(character.character_folder_name.encode()).hexdigest()[:2], 16)
        accent, accent_soft = palettes[palette_index % len(palettes)]
        return {
            "id": character.character_folder_name,
            "name": character.character_name,
            "avatar_url": avatar_url,
            "avatar_id": selected["id"] if selected else None,
            "accent": accent,
            "accent_soft": accent_soft,
        }

    def register_live2d_model(self, model_path: Path) -> str:
        """注册已解析的 Live2D 模型，并返回不泄露主机路径的 URL。"""
        resolved = model_path.expanduser().resolve()
        try:
            resolved.relative_to(LIVE2D_ROOT)
        except ValueError as exc:
            raise ValueError("Live2D 模型不在允许目录中") from exc
        if not resolved.is_file():
            raise ValueError("Live2D 模型文件不存在")

        digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:24]
        model_id = f"model_{digest}"
        with self._lock:
            self._models[model_id] = Live2DEntry(resolved.parent, resolved.name)
        return f"/api/v1/live2d/{model_id}/{resolved.name}"

    def live2d_file(self, model_id: str, asset_path: str) -> Path | None:
        entry = self._models.get(model_id)
        if entry is None or not asset_path or "\x00" in asset_path:
            return None
        candidate = (entry.root / asset_path).resolve()
        try:
            candidate.relative_to(entry.root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def current_background(self) -> dict[str, Any]:
        return self.backgrounds[self.background_index]

    def live2d_document(self, model_id: str, asset_path: str, single: bool = False) -> dict[str, object] | None:
        """仅为已校验资产生成播放视图，拒绝未注册或越界资源。"""
        path = self.live2d_file(model_id, asset_path)
        entry = self._models.get(model_id)
        if path is None or entry is None:
            return None
        if path == entry.root / entry.model_filename and path.name.endswith((".model3.json", ".model.json")):
            gpt_path = str(PROJECT_ROOT / "GPT_SoVITS")
            if gpt_path not in sys.path:
                sys.path.insert(0, gpt_path)
            from live2d_support.performance_catalog import projected_model_document
            return projected_model_document(path)
        if single and path.name.endswith(".motion3.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("Meta"), dict):
                data["Meta"]["Loop"] = False
                return data
        return None

    def next_background(self) -> dict[str, Any]:
        self.background_index = (self.background_index + 1) % len(self.backgrounds)
        return self.current_background()
