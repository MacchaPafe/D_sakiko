"""Renderer-independent Live2D behaviour decisions.

This module deliberately starts with the Pygame emotion/audio segment path.  It
does not load a model or play media: adapters report capabilities and execution
facts, while this core makes the group/index/priority decision exactly once.
"""
from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Iterable, Mapping
from uuid import uuid4

from live2d_support.motion_semantics import motion_group_for_emotion
from live2d_support.motion_selection import resolve_positioned_motion_group
from live2d_support.expression_policy import select_expression_for_motion


@dataclass(frozen=True)
class MotionCapability:
    """Ordered motions available to a renderer for one group."""
    group: str
    count: int


@dataclass(frozen=True)
class ExactMotion:
    """A fully decided motion; an adapter must not choose another one."""
    group: str
    index: int
    priority: int = 3
    position: str = "C"
    expression_id: str | None = None


@dataclass(frozen=True)
class PlaySegment:
    """Atomic decision for one audio-backed emotion segment."""
    command_id: str
    turn_id: str
    segment_id: str
    motion: ExactMotion | None
    audio_path: str
    audio_duration_seconds: float


@dataclass(frozen=True)
class StartAudio:
    """Exact audio command emitted only after its motion lifecycle fact."""
    command_id: str
    audio_path: str


class SharedLive2DBehavior:
    """Small authoritative state for the first migrated behaviour slice."""

    def __init__(self, *, rng: Random | None = None) -> None:
        self._rng = rng or Random()
        self._capabilities: dict[str, MotionCapability] = {}
        self._active_command: PlaySegment | None = None
        self._motion_active = False
        self._audio_active = False
        self._audio_start_dispatched = False

    @property
    def legacy_motion_complete(self) -> bool:
        """Compatibility projection of Pygame's ``not mixer.get_busy()``."""
        return not self._audio_active

    @property
    def active_command(self) -> PlaySegment | None:
        return self._active_command

    def set_capabilities(self, motions: Mapping[str, int]) -> None:
        self._capabilities = {
            str(group): MotionCapability(str(group), int(count))
            for group, count in motions.items()
            if int(count) > 0
        }
        # Counts-only reports do not carry file or expression facts.  Drop
        # any previous detailed catalog so a later decision cannot reuse
        # state from the old model.
        self._motion_files_by_group = {}
        self._expression_ids = frozenset()

    def set_model_catalog(
        self,
        motion_files_by_group: Mapping[str, Iterable[str]],
        expression_ids: Iterable[str] = (),
    ) -> None:
        """Install renderer-reported capability facts for exact decisions.

        The catalog contains concrete group names (including optional ``_C``
        variants), ordered motion files, and supported expression IDs.  It is
        intentionally data-only: neither renderer gets to make a second
        selection after this method has accepted the facts.
        """
        normalized = {
            str(group): tuple(str(path) for path in files)
            for group, files in motion_files_by_group.items()
        }
        self.set_capabilities({group: len(files) for group, files in normalized.items()})
        self._motion_files_by_group = normalized
        self._expression_ids = frozenset(str(expression_id) for expression_id in expression_ids)

    def start_emotion_segment(
        self, *, turn_id: str, segment_id: str, emotion: str,
        audio_path: str, audio_duration_seconds: float = 0.0,
    ) -> PlaySegment | None:
        """Resolve master Pygame's emotion mapping to one exact motion.

        The Pygame compatibility boundary dequeues its paired audio *before*
        resolving this mapping.  Thus an unknown label produces no command,
        while that boundary still preserves the source FIFO-consumption order.
        """
        group = motion_group_for_emotion(emotion, default="")
        resolved_group = resolve_positioned_motion_group(group, "C", self._capabilities)
        capability = self._capabilities.get(resolved_group)
        if not group:
            return None
        motion = None
        if capability is not None:
            motion_index = self._rng.randrange(capability.count)
            motion_files = getattr(self, "_motion_files_by_group", {})
            motion_file = motion_files.get(resolved_group, (None,) * capability.count)[motion_index]
            expression_ids = getattr(self, "_expression_ids", frozenset())
            motion = ExactMotion(
                resolved_group,
                motion_index,
                expression_id=select_expression_for_motion(resolved_group, motion_file, expression_ids),
            )
        command = PlaySegment(
            command_id=uuid4().hex,
            turn_id=turn_id,
            segment_id=segment_id,
            motion=motion,
            audio_path=audio_path,
            audio_duration_seconds=max(0.0, audio_duration_seconds),
        )
        self._active_command = command
        self._motion_active = False
        self._audio_active = False
        self._audio_start_dispatched = False
        return command

    def start_named_motion(self, *, turn_id: str, segment_id: str, group: str, priority: int) -> PlaySegment | None:
        capability = self._capabilities.get(group)
        if capability is None:
            return None
        command = PlaySegment(uuid4().hex, turn_id, segment_id, ExactMotion(group, self._rng.randrange(capability.count), priority), "", 0.0)
        self._active_command, self._motion_active, self._audio_active, self._audio_start_dispatched = command, False, False, False
        return command

    def motion_started(self, command_id: str) -> StartAudio | None:
        if not self._matches(command_id):
            return None
        self._motion_active = True
        return self._issue_audio_start()

    def motion_rejected(self, command_id: str) -> StartAudio | None:
        if not self._matches(command_id):
            return None
        self._motion_active = False
        return self._issue_audio_start()

    def motion_finished(self, command_id: str) -> bool:
        if not self._matches(command_id):
            return False
        self._motion_active = False
        return True

    def audio_started(self, command_id: str) -> bool:
        if not self._matches(command_id):
            return False
        self._audio_active = True
        return True

    def audio_ended(self, command_id: str) -> bool:
        if not self._matches(command_id):
            return False
        self._audio_active = False
        self._active_command = None
        return True

    def command_failed(self, command_id: str, phase: str) -> StartAudio | bool:
        """Normalize an adapter failure into a non-blocking lifecycle fact."""
        if not self._matches(command_id):
            return False
        if phase == "motion_start":
            self._motion_active = False
            return self._issue_audio_start()
        self._audio_active = False
        self._active_command = None
        return True

    def cancel(self) -> bool:
        """Cancel the active command without making a renderer-side decision."""
        had_active = self._active_command is not None
        self._active_command = None
        self._motion_active = False
        self._audio_active = False
        self._audio_start_dispatched = False
        return had_active

    def _matches(self, command_id: str) -> bool:
        return self._active_command is not None and self._active_command.command_id == command_id

    def _issue_audio_start(self) -> StartAudio | None:
        if self._active_command is None or self._audio_start_dispatched:
            return None
        self._audio_start_dispatched = True
        return StartAudio(self._active_command.command_id, self._active_command.audio_path)
