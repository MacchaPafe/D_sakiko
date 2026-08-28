"""Renderer-neutral audio metadata used by shared Live2D decisions."""
from __future__ import annotations

import os
import wave


def read_audio_duration_seconds(audio_file_path: str) -> float:
    """Match master Pygame's WAV-first, mixer-fallback duration semantics."""
    if not audio_file_path or not os.path.isfile(audio_file_path):
        return 0.0
    try:
        with wave.open(audio_file_path, "rb") as audio_file:
            frame_rate = audio_file.getframerate()
            if frame_rate <= 0:
                return 0.0
            return audio_file.getnframes() / frame_rate
    except Exception:
        pass
    try:
        import pygame
        return float(pygame.mixer.Sound(audio_file_path).get_length())
    except Exception:
        return 0.0
