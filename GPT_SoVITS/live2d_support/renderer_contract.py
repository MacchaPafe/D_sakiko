"""Versioned renderer command/fact contract for the shared Live2D behavior."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from live2d_support.shared_behavior import PlaySegment, StartAudio


PROTOCOL_VERSION = 2


def motion_command(segment: PlaySegment) -> dict[str, Any] | None:
    """Serialize an already-resolved motion without renderer-side choices."""
    motion = segment.motion
    if motion is None:
        return None
    return {
        "v": PROTOCOL_VERSION,
        "type": "play_motion",
        "data": {
            "token": segment.command_id,
            "turn_id": segment.turn_id,
            "segment_id": segment.segment_id,
            "group": motion.group,
            "index": motion.index,
            "priority": motion.priority,
            "position": motion.position,
            "expression_id": motion.expression_id,
        },
    }


def audio_command(command: StartAudio, segment: PlaySegment) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": "play_audio",
        "data": {
            "token": command.command_id,
            "turn_id": segment.turn_id,
            "segment_id": segment.segment_id,
            "path": command.audio_path,
        },
    }


def normalize_renderer_fact(message: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return a lifecycle fact and command token, including failure paths.

    ``command_failed`` is intentionally not dropped: a motion-start failure
    normalizes to ``motion_rejected`` (which permits the Pygame-compatible
    audio fallback); other phases stay explicit failures for the caller.
    """
    raw_type = message.get("type")
    data = message.get("data")
    if not isinstance(raw_type, str) or not isinstance(data, Mapping):
        return None
    token = str(data.get("token") or data.get("motion_token") or data.get("audio_token") or "")
    if not token:
        return None
    if raw_type in {"motion_started", "motion_finished", "motion_rejected", "audio_started", "audio_ended"}:
        return raw_type, token
    if raw_type == "command_failed":
        phase = str(data.get("phase") or data.get("reason") or "")
        return ("motion_rejected" if phase in {"motion_start", "motion_not_started"} else "command_failed", token)
    return None
