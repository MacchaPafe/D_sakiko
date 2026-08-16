from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_ROOT = (PROJECT_ROOT / "live2d_related").resolve()
REFERENCE_AUDIO_ROOT = (PROJECT_ROOT / "reference_audio").resolve()


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
            for root in (LIVE2D_ROOT, REFERENCE_AUDIO_ROOT)
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

    def register_character(self, character: Any) -> dict[str, Any]:
        avatar_url = None
        if character.icon_path and Path(character.icon_path).is_file():
            media_id = self.register_media(character.icon_path, "avatar")
            avatar_url = f"/api/v1/media/{media_id}"

        model_url = None
        if character.live2d_json and Path(character.live2d_json).is_file():
            model_path = Path(character.live2d_json).resolve()
            try:
                model_path.relative_to(LIVE2D_ROOT)
            except ValueError:
                model_path = None
            if model_path is not None:
                model_id = f"model_{character.character_folder_name}"
                self._models[model_id] = Live2DEntry(model_path.parent, model_path.name)
                model_url = f"/api/v1/live2d/{model_id}/{model_path.name}"

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
            "model_url": model_url,
            "accent": accent,
            "accent_soft": accent_soft,
        }

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

    def next_background(self) -> dict[str, Any]:
        self.background_index = (self.background_index + 1) % len(self.backgrounds)
        return self.current_background()
