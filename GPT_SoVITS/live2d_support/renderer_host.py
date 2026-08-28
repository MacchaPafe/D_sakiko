"""Shared behavior host for non-Pygame renderers such as Electron."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from collections import deque
from copy import deepcopy
from queue import Empty
from threading import Event
import time
from uuid import uuid4
from typing import Any

from live2d_support.renderer_contract import audio_command, motion_command, normalize_renderer_fact
from live2d_support.shared_behavior import ExactMotion, PlaySegment, StartAudio
from live2d_support.behavior_scheduler import ScheduledMotion
from live2d_support.sakiko_conversion import SakikoConversionDecision, SharedSakikoConversion
from live2d_support.authoritative_owner import AuthoritativeLive2DOwner


CommandEmitter = Callable[[dict[str, Any]], None]


class SharedRendererHost:
    """Adapt shared behavior to a command/fact transport without SDK imports."""

    def __init__(self, emit: CommandEmitter, owner: AuthoritativeLive2DOwner,
                 legacy_motion_complete_value=None, conversion_state_callback=None) -> None:
        self._emit = emit
        self._behavior = owner.behavior
        self._scheduler = owner.scheduler
        self._bye_token = ""
        self._bye_requested = False
        self._scheduled_tokens: dict[str, str] = {}
        self._renderer_is_sakiko = False
        self._connected_renderer_ids: set[str] = set()
        self._renderer_ids: set[str] = set()
        self._retired_renderer_ids: set[str] = set()
        self._unavailable_renderer_ids: set[str] = set()
        self._ready = False
        self._sakiko_conversion = owner.sakiko_conversion
        self._conversion_state_callback = conversion_state_callback
        self._conversion_commit_by_token: dict[str, SakikoConversionDecision] = {}
        self._pending_conversion: SakikoConversionDecision | None = None
        self._pending_conversion_model_token = ""
        self._pending_conversion_renderers: set[str] = set()
        self._pending_conversion_ready_renderers: set[str] = set()
        self._pending_conversion_switch: dict[str, Any] | None = None
        # The owner may finish a conversion before a renderer connects. Keep
        # the already-resolved commands so that a late renderer executes the
        # same result without causing another business decision.
        self._conversion_replay_switch: dict[str, Any] | None = None
        self._conversion_replay_motion: dict[str, Any] | None = None
        self._conversion_replay_renderers: set[str] = set()
        self._model_urls: dict[str, str] = {}
        self._model_urls_by_renderer: dict[str, dict[str, str]] = {}
        self._renderer_roles: dict[str, str] = {}
        self._renderer_model_keys: dict[str, str] = {}
        self._renderer_instances: dict[str, str] = {}
        self._renderer_tokens: dict[str, str] = {}
        self._renderer_runtime_versions: dict[str, str] = {}
        self._renderer_catalogs: dict[str, tuple[str, dict[str, Any], tuple[str, ...]]] = {}
        self._renderer_capabilities: dict[str, dict[str, bool]] = {}
        self._audio_owner_by_command: dict[str, str] = {}
        self._audio_dispatched_tokens: set[str] = set()
        self._current_model_switch: dict[str, Any] | None = None
        self._pending_model_switch: dict[str, Any] | None = None
        self._pending_model_switch_renderers: set[str] = set()
        self._model_switch_deliveries: dict[str, str] = {}
        self._motion_expected: dict[str, set[str]] = {}
        self._motion_started: dict[str, set[str]] = {}
        self._motion_finished: dict[str, set[str]] = {}
        self._motion_terminal: dict[str, set[str]] = {}
        self._motion_launch_resolved: set[str] = set()
        self._emotion_lifecycle_expected: dict[str, set[str]] = {}
        self._legacy_motion_complete_value = legacy_motion_complete_value
        self._project_legacy_motion_complete()

    def start_bye(self) -> bool:
        if self._bye_requested:
            return False
        self._bye_requested = True
        command = self._behavior.start_named_motion(turn_id="", segment_id="", group="bye", priority=3)
        if command is None or (self._renderer_ids and not self._motion_renderer_ids()):
            self._emit({"type": "close_renderer", "data": {"reason": "bye_motion_unavailable"}})
            return False
        self._bye_token = command.command_id
        motion = motion_command(command)
        assert motion is not None
        motion_targets = self._motion_renderer_ids()
        if motion_targets:
            motion.setdefault("data", {})["target_renderer_ids"] = sorted(motion_targets)
        self._track_motion_command(command.command_id)
        self._emit(motion)
        return True

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def all_renderers_unavailable(self) -> bool:
        return bool(self._unavailable_renderer_ids) and not self._renderer_ids

    @property
    def model_switch_pending(self) -> bool:
        """Whether emotion decisions must wait for a matching ready fact."""
        return self._pending_model_switch is not None or self._pending_conversion is not None

    def reject_emotion_segment(self, *, turn_id: str, segment_id: str, emotion: str,
                               audio_path: str, audio_duration_seconds: float = 0.0) -> bool:
        """Resolve a terminal no-runtime fact without leaving owner state active."""
        segment = self._behavior.start_emotion_segment(
            turn_id=turn_id, segment_id=segment_id, emotion=emotion,
            audio_path=audio_path, audio_duration_seconds=audio_duration_seconds,
        )
        if segment is None:
            return False
        self._behavior.command_failed(segment.command_id, "renderer_unavailable")
        self._scheduler.set_audio_busy(False)
        self._project_legacy_motion_complete()
        return True

    def set_thinking(self, active: bool) -> bool:
        """Accept an upstream fact; only the shared scheduler owns its timer."""
        self._scheduler.set_thinking(active)
        self._emit({"type": "thinking_changed", "data": {"active": active}})
        return True

    def handle_runtime_control(self, data: Mapping[str, Any]) -> bool:
        """Route legacy UI controls through the owner, preserving mechanics-only renderers."""
        command_type = str(data.get("type") or "")
        if command_type == "start_talking":
            return self._emit_scheduled(self._scheduler.request_motion("talking_motion", 4, "talking"))
        if command_type == "stop_talking":
            talking_tokens = {
                token for token, purpose in self._scheduled_tokens.items()
                if purpose == "talking"
            }
            self._scheduled_tokens = {
                token: purpose for token, purpose in self._scheduled_tokens.items()
                if purpose != "talking"
            }
            for token in talking_tokens:
                self._clear_motion_tracking(token)
            self._scheduler.stop_talking()
            self._emit({"type": "stop_motion", "data": {}})
            return True
        if command_type == "cancel_turn":
            self._behavior.cancel()
            self._audio_owner_by_command.clear()
            self._audio_dispatched_tokens.clear()
            self._scheduled_tokens.clear()
            self._scheduler.reset_after_cancel()
            self._bye_token = ""
            self._bye_requested = False
            self._motion_expected.clear()
            self._motion_started.clear()
            self._motion_finished.clear()
            self._motion_terminal.clear()
            self._motion_launch_resolved.clear()
            self._emotion_lifecycle_expected.clear()
            self._pending_model_switch = None
            self._pending_model_switch_renderers.clear()
            self._pending_conversion = None
            self._pending_conversion_model_token = ""
            self._pending_conversion_renderers.clear()
            self._pending_conversion_ready_renderers.clear()
            self._pending_conversion_switch = None
            self._conversion_replay_switch = None
            self._conversion_replay_motion = None
            self._conversion_replay_renderers.clear()
            self._project_legacy_motion_complete()
            self._emit({"type": "stop_audio", "data": {}})
            self._emit({"type": "stop_motion", "data": {}})
            self._emit({"type": "reset", "data": {}})
            return True
        if command_type == "exit":
            return self.start_bye()
        if command_type in {"change_l2d_background", "switch_l2d_fps", "toggle_l2d_layout_edit"}:
            self._emit({"type": command_type, "data": dict(data)})
            return True
        if command_type == "switch_live2d":
            payload = dict(data)
            payload.pop("type", None)
            payload.setdefault("model_token", uuid4().hex)
            # Upstream resets the segment-local repeat loop as soon as a
            # normal model switch is accepted.  Keep global idle/thinking
            # deadlines intact.
            self._scheduler.reset_long_audio()
            # A normal Qt model switch is a newer authoritative decision than
            # any completed Sakiko conversion barrier.  Retire conversion
            # delivery/replay state without touching persistent Sakiko
            # black/white and mask state.
            self._invalidate_conversion_commands()
            self._pending_conversion = None
            self._pending_conversion_model_token = ""
            self._pending_conversion_renderers.clear()
            self._pending_conversion_ready_renderers.clear()
            self._pending_conversion_switch = None
            self._conversion_replay_switch = None
            self._conversion_replay_motion = None
            self._conversion_replay_renderers.clear()
            self._conversion_commit_by_token.clear()
            model_key = str(
                payload.get("character_folder_name")
                or payload.get("character_folder")
                or ""
            ).lower()
            character_name = str(payload.get("character_name") or "")
            if (model_key == "sakiko" or character_name == "祥子") and self._sakiko_conversion.is_black:
                # Master always returns to the black/costume model while the
                # persistent Sakiko state is black. Resolve that variant once
                # here so neither renderer repeats this business decision.
                payload["model_json"] = "../live2d_related/sakiko/live2D_model_costume/3.model.json"
                payload["electron_model_url"] = (
                    "http://127.0.0.1:9877/model/sakiko/live2D_model_costume/3.model.json"
                )
            model_path = str(payload.get("model_json") or payload.get("model_url") or "")
            if model_path and "electron_model_url" not in payload:
                marker = "live2d_related"
                if marker in model_path.replace("\\", "/"):
                    relative = model_path.replace("\\", "/").split(marker, 1)[1].lstrip("/")
                    payload["electron_model_url"] = f"http://127.0.0.1:9877/model/{relative}"
            self._current_model_switch = deepcopy(payload)
            self._pending_model_switch = payload
            self._pending_model_switch_renderers = set(self._connected_renderer_ids)
            for renderer_id in self._pending_model_switch_renderers:
                self._model_switch_deliveries.pop(self._renderer_instance_key(renderer_id), None)
            if self._pending_model_switch_renderers:
                self._emit_model_switch(self._pending_model_switch_renderers)
            return True
        return False

    def start_emotion_segment(self, *, turn_id: str, segment_id: str, emotion: str, audio_path: str, audio_duration_seconds: float = 0.0) -> bool:
        segment = self._behavior.start_emotion_segment(
            turn_id=turn_id, segment_id=segment_id, emotion=emotion, audio_path=audio_path, audio_duration_seconds=audio_duration_seconds,
        )
        if segment is None:
            return False
        command = motion_command(segment)
        if command is None:
            self._audio_owner_by_command[segment.command_id] = self._audio_owner_renderer_id() or ""
            self._emit_audio(self._behavior.motion_rejected(segment.command_id), segment)
        else:
            self._scheduler.start_segment(segment.motion.group, segment.audio_duration_seconds)
            command_payload = command
            motion_targets = self._motion_renderer_ids()
            if not motion_targets and self._renderer_ids:
                self._emit_audio(self._behavior.motion_rejected(segment.command_id), segment)
                return True
            if motion_targets:
                command_payload.setdefault("data", {})["target_renderer_ids"] = sorted(motion_targets)
            self._track_motion_command(segment.command_id, motion_targets)
            # Pygame remains the compatibility authority for audio-backed
            # emotion timing. Electron receives the same motion, but its
            # callback must not hold back or advance the master audio edge.
            self._audio_owner_by_command[segment.command_id] = self._audio_owner_renderer_id() or ""
            audio_owner = self._audio_owner_by_command[segment.command_id]
            self._emotion_lifecycle_expected[segment.command_id] = (
                {audio_owner} if audio_owner in motion_targets else set(motion_targets)
            )
            self._emit(command_payload)
        return True

    def handle_renderer_fact(self, message: Mapping[str, Any]) -> bool:
        data = message.get("data")
        if not isinstance(data, Mapping):
            return False
        if message.get("type") == "renderer_hello":
            renderer_id = str(data.get("renderer_id") or "")
            if not renderer_id:
                return False
            self._retired_renderer_ids.discard(renderer_id)
            self._unavailable_renderer_ids.discard(renderer_id)
            self._connected_renderer_ids.add(renderer_id)
            self._renderer_roles[renderer_id] = str(data.get("renderer_role") or "")
            previous_instance = self._renderer_instances.get(renderer_id)
            if previous_instance is not None:
                next_instance = str(data.get("renderer_instance_id") or renderer_id)
                if previous_instance != next_instance:
                    self._model_switch_deliveries.pop(
                        f"{renderer_id}:{previous_instance}", None,
                    )
            self._renderer_instances[renderer_id] = str(data.get("renderer_instance_id") or renderer_id)
            self._renderer_tokens[renderer_id] = str(data.get("model_token") or "")
            self._renderer_runtime_versions[renderer_id] = str(data.get("runtime_version") or "")
            self._renderer_model_keys[renderer_id] = str(data.get("model_key") or "")
            if self._pending_model_switch is not None:
                expected_token = str(self._pending_model_switch.get("model_token") or "")
                if self._renderer_tokens[renderer_id] != expected_token:
                    self._pending_model_switch_renderers.add(renderer_id)
                    self._emit_model_switch({renderer_id})
            elif self._pending_conversion_switch is not None:
                expected_token = str(self._pending_conversion_switch.get("model_token") or "")
                if self._renderer_tokens[renderer_id] != expected_token:
                    self._pending_conversion_renderers.add(renderer_id)
                    self._emit_model_switch_payload(self._pending_conversion_switch, {renderer_id})
            else:
                # Model synchronization is an authoritative command. The
                # transport does not replay model commands independently.
                self._maybe_sync_noncanonical_renderer(renderer_id)
            return True
        unavailable_transport = message.get("type") == "renderer_unavailable"
        unavailable_role = ""
        unavailable_instance = ""
        if unavailable_transport:
            # A runtime that cannot load a model must not remain an execution
            # target. Its transport remains connected so a later authoritative
            # model switch can recover the same renderer instance.
            renderer_id = str(data.get("renderer_id") or "")
            if renderer_id:
                self._unavailable_renderer_ids.add(renderer_id)
                unavailable_role = self._renderer_roles.get(renderer_id, str(data.get("renderer_role") or ""))
                unavailable_instance = self._renderer_instances.get(
                    renderer_id, str(data.get("renderer_instance_id") or renderer_id),
                )
            message = {
                "type": "renderer_disconnected",
                "data": {
                    "renderer_id": data.get("renderer_id"),
                    "renderer_instance_id": data.get("renderer_instance_id"),
                },
            }
        if message.get("type") == "renderer_disconnected":
            renderer_id = str(data.get("renderer_id") or "")
            if not renderer_id or renderer_id not in self._connected_renderer_ids:
                return False
            instance_id = str(data.get("renderer_instance_id") or "")
            if instance_id and self._renderer_instances.get(renderer_id) not in {None, instance_id}:
                return False
            self._connected_renderer_ids.discard(renderer_id)
            self._renderer_ids.discard(renderer_id)
            self._retired_renderer_ids.add(renderer_id)
            self._model_switch_deliveries.pop(self._renderer_instance_key(renderer_id), None)
            self._renderer_roles.pop(renderer_id, None)
            self._renderer_instances.pop(renderer_id, None)
            self._renderer_tokens.pop(renderer_id, None)
            self._renderer_runtime_versions.pop(renderer_id, None)
            self._renderer_model_keys.pop(renderer_id, None)
            self._renderer_catalogs.pop(renderer_id, None)
            self._renderer_capabilities.pop(renderer_id, None)
            self._model_urls_by_renderer.pop(renderer_id, None)
            self._pending_model_switch_renderers.discard(renderer_id)
            if self._pending_model_switch is not None and not self._pending_model_switch_renderers:
                self._emit_model_switch_behavior()
            active = self._behavior.active_command
            if active is not None and self._audio_owner_by_command.get(active.command_id) == renderer_id:
                replacement = self._audio_owner_renderer_id()
                if replacement:
                    self._audio_owner_by_command[active.command_id] = replacement
                    lifecycle_expected = self._emotion_lifecycle_expected.get(active.command_id)
                    if lifecycle_expected is not None and renderer_id in lifecycle_expected:
                        replacement_motion_targets = self._motion_renderer_ids()
                        lifecycle_expected.clear()
                        if replacement in replacement_motion_targets:
                            lifecycle_expected.add(replacement)
                        else:
                            lifecycle_expected.update(replacement_motion_targets)
                    if active.command_id in self._audio_dispatched_tokens:
                        self._emit_audio(StartAudio(active.command_id, active.audio_path), active)
                else:
                    self._audio_owner_by_command.pop(active.command_id, None)
                    self._audio_dispatched_tokens.discard(active.command_id)
                    self._behavior.command_failed(active.command_id, "renderer_unavailable")
                    self._scheduler.set_audio_busy(False)
                    self._scheduler.motion_finished("emotion")
                    self._clear_motion_tracking(active.command_id)
                    self._project_legacy_motion_complete()
                    active = None
            for token, expected in list(self._motion_expected.items()):
                expected.discard(renderer_id)
                self._motion_started.get(token, set()).discard(renderer_id)
                self._motion_finished.get(token, set()).discard(renderer_id)
                self._motion_terminal.get(token, set()).discard(renderer_id)
                purpose = self._scheduled_tokens.get(token)
                if purpose is not None:
                    self._resolve_scheduled_motion(token, purpose, disconnected=True)
                elif active is not None and active.command_id == token:
                    self._resolve_emotion_motion(token, active)
            if self._pending_conversion is not None:
                self._pending_conversion_renderers.discard(renderer_id)
                self._finish_pending_conversion_if_ready()
            if unavailable_transport:
                self._connected_renderer_ids.add(renderer_id)
                self._retired_renderer_ids.discard(renderer_id)
                self._renderer_roles[renderer_id] = unavailable_role
                self._renderer_instances[renderer_id] = unavailable_instance
            self._apply_canonical_catalog()
            self._ready = bool(self._audio_renderer_ids())
            return True
        if message.get("type") == "renderer_ready":
            renderer_id = str(data.get("renderer_id") or "")
            renderer_instance_id = str(data.get("renderer_instance_id") or renderer_id or "anonymous")
            if renderer_id:
                previous_instance = self._renderer_instances.get(renderer_id)
                if previous_instance is not None and previous_instance != renderer_instance_id:
                    return False
                expected_token = ""
                if self._pending_model_switch is not None:
                    expected_token = str(self._pending_model_switch.get("model_token") or "")
                elif self._pending_conversion_switch is not None:
                    expected_token = str(self._pending_conversion_switch.get("model_token") or "")
                if expected_token and str(data.get("model_token") or "") != expected_token:
                    # A stale ready fact may acknowledge transport health, but
                    # must not overwrite the active catalog or model metadata.
                    if self._pending_model_switch is not None:
                        self._pending_model_switch_renderers.add(renderer_id)
                        self._emit_model_switch({renderer_id})
                    elif self._pending_conversion_switch is not None:
                        self._pending_conversion_renderers.add(renderer_id)
                        self._emit_model_switch_payload(self._pending_conversion_switch, {renderer_id})
                    return True
                # Only a matching instance/token may publish model metadata.
                # Transport liveness is recorded after this validation so a
                # stale ready fact cannot replace the active renderer state.
                self._retired_renderer_ids.discard(renderer_id)
                self._unavailable_renderer_ids.discard(renderer_id)
                self._connected_renderer_ids.add(renderer_id)
                self._renderer_ids.add(renderer_id)
                self._renderer_roles[renderer_id] = str(data.get("renderer_role") or self._renderer_roles.get(renderer_id, ""))
                self._renderer_instances[renderer_id] = renderer_instance_id
            motion_files = data.get("motion_files_by_group")
            expression_ids = data.get("expression_ids", ())
            normalized_expression_ids = tuple(expression_ids) if isinstance(expression_ids, (list, tuple)) else ()
            if renderer_id:
                if isinstance(motion_files, Mapping):
                    self._renderer_catalogs[renderer_id] = ("files", dict(motion_files), normalized_expression_ids)
                else:
                    groups = data.get("motion_groups", {})
                    self._renderer_catalogs[renderer_id] = ("groups", dict(groups) if isinstance(groups, Mapping) else {}, ())
                self._renderer_capabilities[renderer_id] = self._normalize_capabilities(data)
                self._apply_canonical_catalog()
            elif isinstance(motion_files, Mapping):
                self._behavior.set_model_catalog(motion_files, normalized_expression_ids)
                self._scheduler.set_model_catalog(motion_files, normalized_expression_ids)
            else:
                self._behavior.set_capabilities(data.get("motion_groups", {}))
                self._scheduler.set_catalog(data.get("motion_groups", {}))
            self._ready = bool(self._audio_renderer_ids()) if self._renderer_ids else True
            model_key = str(data.get("model_key", ""))
            self._renderer_is_sakiko = model_key.lower() == "sakiko"
            if renderer_id:
                self._renderer_model_keys[renderer_id] = model_key
                self._renderer_tokens[renderer_id] = str(data.get("model_token") or "")
                self._renderer_runtime_versions[renderer_id] = str(data.get("runtime_version") or "")
            urls = data.get("model_urls")
            if isinstance(urls, Mapping):
                normalized_urls = {str(key): str(value) for key, value in urls.items() if value}
                self._model_urls_by_renderer[renderer_id] = normalized_urls
                self._model_urls = normalized_urls
            if (renderer_id and self._pending_model_switch is None
                    and self._pending_conversion is None and self._conversion_replay_switch is None):
                self._maybe_sync_noncanonical_renderer(renderer_id)
                for secondary_id in sorted(self._renderer_ids):
                    if secondary_id != renderer_id:
                        self._maybe_sync_noncanonical_renderer(secondary_id)
            if self._pending_conversion is not None and renderer_id:
                expected_token = self._pending_conversion_model_token
                actual_token = str(data.get("model_token") or "")
                if expected_token and actual_token != expected_token and self._pending_conversion_renderers:
                    self._pending_conversion_renderers.add(renderer_id)
                    self._emit_model_switch_payload(
                        self._pending_conversion_switch or {}, {renderer_id},
                    )
                else:
                    self._pending_conversion_ready_renderers.add(renderer_id)
                    self._pending_conversion_renderers.discard(renderer_id)
                self._finish_pending_conversion_if_ready()
            elif renderer_id and self._conversion_replay_switch is not None:
                # A renderer that joins after the conversion barrier must be
                # brought to the owner's current model and receive the exact
                # motion command already sent to the other renderers.
                expected_token = str(self._conversion_replay_switch.get("model_token") or "")
                actual_token = str(data.get("model_token") or "")
                renderer_instance_key = self._renderer_instance_key(renderer_id)
                if actual_token != expected_token:
                    switch = deepcopy(self._conversion_replay_switch)
                    switch["target_renderer_ids"] = [renderer_id]
                    self._emit({"type": "switch_live2d", "data": switch})
                elif renderer_instance_key not in self._conversion_replay_renderers and self._conversion_replay_motion is not None:
                    motion = deepcopy(self._conversion_replay_motion)
                    motion.setdefault("data", {})["target_renderer_ids"] = [renderer_id]
                    self._conversion_replay_renderers.add(renderer_instance_key)
                    self._emit(motion)
            if self._pending_model_switch is not None and renderer_id:
                expected_token = str(self._pending_model_switch.get("model_token") or "")
                actual_token = str(data.get("model_token") or "")
                if actual_token == expected_token:
                    self._pending_model_switch_renderers.discard(renderer_id)
                    if not self._pending_model_switch_renderers:
                        self._emit_model_switch_behavior()
                else:
                    self._pending_model_switch_renderers.add(renderer_id)
                    self._emit_model_switch({renderer_id})
            return True
        fact_renderer_id = str(data.get("renderer_id") or "")
        if fact_renderer_id in self._unavailable_renderer_ids:
            return False
        if fact_renderer_id in self._retired_renderer_ids:
            return False
        if self._renderer_ids and fact_renderer_id and fact_renderer_id not in self._renderer_ids:
            return False
        if fact_renderer_id:
            fact_instance = str(data.get("renderer_instance_id") or "")
            current_instance = self._renderer_instances.get(fact_renderer_id)
            if fact_instance and current_instance and fact_instance != current_instance:
                return False
        if message.get("type") == "renderer_intent" and data.get("intent") == "click":
            command = self._scheduler.click(is_sakiko=self._canonical_renderer_is_sakiko())
            return self._emit_scheduled(command)
        normalized = normalize_renderer_fact(message)
        if normalized is None:
            return False
        fact, token = normalized
        scheduled_purpose = self._scheduled_tokens.get(token)
        if scheduled_purpose is not None:
            if fact in {"motion_started", "motion_rejected", "motion_finished", "command_failed"}:
                recorded_kind = "motion_rejected" if fact in {"motion_rejected", "command_failed"} else fact
                if not self._record_motion_fact(token, fact_renderer_id, recorded_kind):
                    return True
                self._resolve_scheduled_motion(token, scheduled_purpose)
                return True
        active = self._behavior.active_command
        if fact == "motion_started":
            if active is not None and token == active.command_id:
                if not self._record_motion_fact(token, fact_renderer_id, fact):
                    return True
                self._resolve_emotion_motion(token, active)
            return True
        if fact == "motion_rejected":
            if active is not None and token == active.command_id:
                if not self._record_motion_fact(token, fact_renderer_id, fact):
                    return True
                self._resolve_emotion_motion(token, active)
            return True
        if fact == "motion_finished":
            if active is not None and token == active.command_id:
                if not self._record_motion_fact(token, fact_renderer_id, fact):
                    return True
                self._resolve_emotion_motion(token, active)
                return True
            handled = self._behavior.motion_finished(token)
            if handled and token == self._bye_token:
                self._bye_token = ""
                self._emit({"type": "close_renderer", "data": {"reason": "bye_motion_finished"}})
            return handled
        if fact == "audio_started":
            owner = self._audio_owner_by_command.get(token)
            if owner and fact_renderer_id and fact_renderer_id != owner:
                return True
            handled = self._behavior.audio_started(token)
            if handled:
                self._scheduler.set_audio_busy(True)
                self._project_legacy_motion_complete()
            return handled
        if fact == "audio_ended":
            owner = self._audio_owner_by_command.get(token)
            if owner and fact_renderer_id and fact_renderer_id != owner:
                return True
            handled = self._behavior.audio_ended(token)
            if handled:
                self._scheduler.set_audio_busy(False)
                self._audio_owner_by_command.pop(token, None)
                self._audio_dispatched_tokens.discard(token)
                self._project_legacy_motion_complete()
            return handled
        if fact == "command_failed":
            phase = str(data.get("phase") or "unknown")
            # Only the designated Pygame audio owner may resolve an emotion
            # command failure. A faster Electron failure must not trigger the
            # fallback audio path before Pygame has reported its own result.
            if active is not None and token == active.command_id:
                owner = self._audio_owner_by_command.get(token)
                if owner and fact_renderer_id and fact_renderer_id != owner:
                    return True
            self._emit_audio(
                self._behavior.command_failed(token, phase),
                active,
            )
            if phase != "motion_start":
                self._audio_owner_by_command.pop(token, None)
                self._audio_dispatched_tokens.discard(token)
                self._project_legacy_motion_complete()
            return True
        return False

    def start_sakiko_conversion(self, conversion, model_urls: Mapping[str, str]) -> bool:
        """Decide once; the renderer only reloads the requested model."""
        # Upstream retires the previous segment repeat loop as soon as a
        # conversion intent is observed, even when the runtime gate rejects
        # that conversion.
        self._scheduler.reset_long_audio()
        pending_target = (
            self._pending_conversion.resulting_is_black
            if self._pending_conversion is not None
            and self._pending_conversion.resulting_is_black is not None
            else self._sakiko_conversion.is_black
        )
        self._invalidate_conversion_commands()
        if conversion == "toggle":
            conversion = not pending_target
        # Apply the upstream guard even when the current runtime is
        # audio-only because its model has no motion capability.
        canonical_id = self._canonical_runtime_id()
        if canonical_id is not None:
            canonical_key = self._renderer_model_keys.get(canonical_id, "").lower()
            runtime_version = self._renderer_runtime_versions.get(canonical_id, "").lower()
            runtime_role = self._renderer_roles.get(canonical_id, "").lower()
            if canonical_key != "sakiko":
                return False
            if runtime_role == "pygame" and runtime_version != "v2":
                return False
            # A renderer that only provides audio (or reports no motion
            # capability) cannot execute the conversion model/motion pair.
            # Keep the upstream Sakiko gate strict for Null/audio-only
            # runtimes while allowing Electron's legacy ready schema, which
            # does not include runtime_version, when it has motion support.
            if not self._renderer_capabilities.get(canonical_id, {}).get("motion", False):
                return False
        # A newer Sakiko conversion supersedes a normal model switch that has
        # not crossed its renderer barrier yet.
        self._pending_model_switch = None
        self._pending_model_switch_renderers.clear()
        decision = self._sakiko_conversion.preview(conversion)
        if decision.model_target == "current":
            return self._emit_conversion_motion(decision)
        pygame_urls = next(
            (urls for renderer_id, urls in self._model_urls_by_renderer.items()
             if self._renderer_roles.get(renderer_id) == "pygame"),
            {},
        )
        candidates = model_urls if isinstance(model_urls, Mapping) and model_urls else (pygame_urls or self._model_urls)
        model_url = str(candidates.get(decision.model_target, ""))
        if not model_url:
            return False
        self._conversion_replay_switch = None
        self._conversion_replay_motion = None
        self._conversion_replay_renderers.clear()
        self._pending_conversion = decision
        self._pending_conversion_model_token = uuid4().hex
        self._pending_conversion_renderers = set(self._connected_renderer_ids)
        electron_urls = next(
            (urls for renderer_id, urls in self._model_urls_by_renderer.items()
             if self._renderer_roles.get(renderer_id) == "electron"),
            {},
        )
        self._pending_conversion_switch = {
            "model_url": model_url,
            "electron_model_url": electron_urls.get(decision.model_target, model_url),
            "character_folder": "sakiko",
            "character_folder_name": "sakiko",
            "model_token": self._pending_conversion_model_token,
        }
        for renderer_id in self._pending_conversion_renderers:
            self._model_switch_deliveries.pop(self._renderer_instance_key(renderer_id), None)
        self._emit_model_switch_payload(
            self._pending_conversion_switch, self._pending_conversion_renderers,
        )
        return True

    def _emit_conversion_motion(self, decision: SakikoConversionDecision) -> bool:
        # A current-model conversion has no model barrier; commit the
        # authoritative mask/state before emitting its presentation motion.
        if decision.model_target == "current":
            self._sakiko_conversion.commit(decision)
            if self._conversion_state_callback is not None:
                try:
                    self._conversion_state_callback(self._sakiko_conversion.is_black, self._sakiko_conversion.mask_on)
                except Exception:
                    pass
        expression = self._scheduler.resolve_semantic_expression(decision.semantic_expression) if decision.semantic_expression else None
        if decision.fixed_index is None:
            command = self._scheduler.request_motion(decision.motion_group, decision.priority, decision.purpose)
        else:
            command = self._scheduler.request_fixed_motion(decision.motion_group, decision.fixed_index, decision.priority, decision.purpose)
        if command is not None and expression is not None:
            command = ScheduledMotion(command.group, command.index, command.priority, command.purpose, expression)
        return self._emit_scheduled(command, replay_for_late_renderers=True)

    def tick(self, *, include_long_audio: bool = True) -> bool:
        if self.model_switch_pending:
            return False
        if self._renderer_ids and not self._motion_renderer_ids():
            return False
        return self._emit_scheduled(self._scheduler.tick(include_long_audio=include_long_audio))

    def tick_long_audio(self) -> bool:
        if self.model_switch_pending:
            return False
        if self._renderer_ids and not self._motion_renderer_ids():
            return False
        return self._emit_scheduled(self._scheduler.tick_long_audio())

    def _emit_scheduled(self, scheduled: ScheduledMotion | None, *, replay_for_late_renderers: bool = False,
                        conversion_decision: SakikoConversionDecision | None = None) -> bool:
        if scheduled is None:
            return False
        motion_targets = self._motion_renderer_ids()
        if self._renderer_ids and not motion_targets:
            return False
        self._scheduler.motion_requested(scheduled.purpose)
        token = uuid4().hex
        command = PlaySegment(token, "scheduler", scheduled.purpose, ExactMotion(
            scheduled.group, scheduled.index, scheduled.priority, expression_id=scheduled.expression_id,
        ), "", 0.0)
        self._scheduled_tokens[token] = scheduled.purpose
        if conversion_decision is not None:
            self._conversion_commit_by_token[token] = conversion_decision
        motion = motion_command(command)
        assert motion is not None
        if motion_targets:
            motion.setdefault("data", {})["target_renderer_ids"] = sorted(motion_targets)
        self._track_motion_command(token, motion_targets)
        if replay_for_late_renderers:
            self._conversion_replay_motion = deepcopy(motion)
        self._emit(motion)
        return True

    def _emit_model_switch_behavior(self) -> bool:
        payload = self._pending_model_switch
        if payload is None or not self._renderer_ids:
            return False
        if payload.get("initial_model") is True:
            # Master Pygame loaded its initial model without playing the
            # user-triggered change-character animation.
            self._pending_model_switch = None
            self._pending_model_switch_renderers.clear()
            return True
        model_key = str(payload.get("character_folder_name") or payload.get("character_folder") or "").lower()
        model_path = str(payload.get("model_json") or payload.get("model_url") or "").replace("\\", "/").lower()
        semantic = "serious" if model_key == "sakiko" and "costume" in model_path else "idle"
        scheduled = self._scheduler.request_motion("change_character", 3, "change_character")
        if scheduled is None:
            # The model-ready fact already committed the new catalog.  Missing
            # change-character motion must not keep later emotion decisions
            # blocked, just as the upstream loop simply skipped that motion.
            self._pending_model_switch = None
            self._pending_model_switch_renderers.clear()
            return True
        expression_id = self._scheduler.resolve_semantic_expression(semantic)
        if expression_id:
            scheduled = ScheduledMotion(
                scheduled.group, scheduled.index, scheduled.priority,
                scheduled.purpose, expression_id,
            )
        emitted = self._emit_scheduled(scheduled)
        if emitted:
            self._pending_model_switch = None
            self._pending_model_switch_renderers.clear()
        return emitted

    def _emit_model_switch(self, renderer_ids: set[str]) -> None:
        if self._pending_model_switch is None or not renderer_ids:
            return
        self._emit_model_switch_payload(self._pending_model_switch, renderer_ids)

    def _emit_model_switch_payload(self, source: Mapping[str, Any], renderer_ids: set[str]) -> None:
        model_token = str(source.get("model_token") or "")
        targets = {
            renderer_id for renderer_id in renderer_ids
            if self._model_switch_deliveries.get(self._renderer_instance_key(renderer_id)) != model_token
        }
        if not targets:
            return
        payload = deepcopy(dict(source))
        payload["target_renderer_ids"] = sorted(targets)
        for renderer_id in targets:
            self._model_switch_deliveries[self._renderer_instance_key(renderer_id)] = model_token
        self._emit({"type": "switch_live2d", "data": payload})

    def _emit_audio(self, command: StartAudio | None, segment) -> None:
        if isinstance(command, StartAudio) and segment is not None:
            payload = audio_command(command, segment)
            audio_path = str(payload.get("data", {}).get("path") or "")
            electron_audio_url = self._electron_audio_url(audio_path)
            if electron_audio_url:
                payload.setdefault("data", {})["electron_audio_url"] = electron_audio_url
            if self._renderer_ids:
                payload.setdefault("data", {})["target_renderer_ids"] = sorted(self._audio_renderer_ids())
                audio_owner = self._audio_owner_by_command.get(command.command_id) or self._audio_owner_renderer_id()
                if audio_owner:
                    payload.setdefault("data", {})["target_renderer_id"] = audio_owner
            self._audio_dispatched_tokens.add(command.command_id)
            self._emit(payload)

    @staticmethod
    def _electron_audio_url(audio_path: str) -> str:
        """Expose a local project audio path through Bridge's HTTP server."""
        normalized = str(audio_path or "").replace("\\", "/")
        if not normalized or normalized.startswith(("http://", "https://")):
            return normalized if normalized.startswith(("http://", "https://")) else ""
        marker = "reference_audio/"
        if marker in normalized:
            relative = normalized.split(marker, 1)[1]
            return f"http://127.0.0.1:9877/audio/reference_audio/{relative}"
        return ""

    def _audio_owner_renderer_id(self) -> str | None:
        """Select one runtime for audible playback while motions fan out."""
        # Preserve master Pygame backpressure whenever that runtime is alive.
        # Electron is an explicit fallback for Electron-only sessions; motion
        # fact arrival order must never select the audio owner.
        audio_renderers = self._audio_renderer_ids()
        for role in ("pygame", "electron"):
            candidates = sorted(
                renderer_id for renderer_id in audio_renderers
                if self._renderer_roles.get(renderer_id) == role
            )
            if candidates:
                return candidates[0]
        return sorted(audio_renderers)[0] if audio_renderers else None

    def _track_motion_command(self, token: str, expected: set[str] | None = None) -> None:
        expected = set(self._motion_renderer_ids() if expected is None else expected)
        if not expected and self._ready and not self._renderer_ids:
            expected = {"__anonymous__"}
        self._motion_expected[token] = expected
        self._motion_started[token] = set()
        self._motion_finished[token] = set()
        self._motion_terminal[token] = set()
        self._motion_launch_resolved.discard(token)

    def _clear_motion_tracking(self, token: str) -> None:
        self._motion_expected.pop(token, None)
        self._motion_started.pop(token, None)
        self._motion_finished.pop(token, None)
        self._motion_terminal.pop(token, None)
        self._motion_launch_resolved.discard(token)
        self._emotion_lifecycle_expected.pop(token, None)

    def _invalidate_conversion_commands(self) -> None:
        for token in list(self._conversion_commit_by_token):
            self._conversion_commit_by_token.pop(token, None)
            self._scheduled_tokens.pop(token, None)
            self._clear_motion_tracking(token)

    def _record_motion_fact(self, token: str, renderer_id: str, kind: str) -> bool:
        expected = self._motion_expected.get(token)
        if expected is None:
            return False
        if not expected:
            return True
        if renderer_id:
            # A fact from another renderer is not an acknowledgement for this
            # command. In particular, Electron must not satisfy the Pygame
            # audio owner's lifecycle edge merely by reporting first.
            if renderer_id not in expected:
                return False
            sources = {renderer_id}
        elif len(expected) <= 1:
            # Legacy facts without a source remain valid for single-runtime
            # callers and represent the complete fan-out for compatibility.
            sources = set(expected) if expected else {"__anonymous__"}
        else:
            # Pre-contract adapters emitted no renderer identity. Preserve
            # that historical API by treating their one callback as the
            # complete fan-out; current runtimes always source-qualify facts.
            sources = set(expected)
        if kind == "motion_started":
            self._motion_started[token].update(sources)
        elif kind == "motion_finished":
            # A completion callback is also proof that this runtime launched
            # the exact motion. Keep legacy single-fact adapters compatible.
            self._motion_started[token].update(sources)
            self._motion_finished[token].update(sources)
            self._motion_terminal[token].update(sources)
        else:
            self._motion_terminal[token].update(sources)
        if kind in {"motion_started", "motion_rejected"}:
            self._motion_terminal[token].update(sources)
        return True

    def _resolve_scheduled_motion(self, token: str, purpose: str, *, disconnected: bool = False) -> None:
        expected = self._motion_expected.get(token)
        if expected is None or not expected <= self._motion_terminal.get(token, set()):
            return
        started = self._motion_started.get(token, set())
        if token not in self._motion_launch_resolved:
            self._motion_launch_resolved.add(token)
        if started:
            self._scheduler.motion_started(purpose)
        if started and not started <= self._motion_finished.get(token, set()):
            return
        self._scheduled_tokens.pop(token, None)
        self._conversion_commit_by_token.pop(token, None)
        self._scheduler.motion_finished(purpose)
        self._clear_motion_tracking(token)
        if token == self._bye_token:
            self._bye_token = ""
            reason = "bye_renderer_disconnected" if disconnected else "bye_motion_finished"
            self._emit({"type": "close_renderer", "data": {"reason": reason}})

    def _resolve_emotion_motion(self, token: str, active) -> None:
        expected = self._emotion_lifecycle_expected.get(token, self._motion_expected.get(token))
        if expected is None or not expected <= self._motion_terminal.get(token, set()):
            return
        started = self._motion_started.get(token, set()) & expected
        if token == self._bye_token:
            if started and not started <= self._motion_finished.get(token, set()):
                return
            self._behavior.motion_finished(token)
            self._clear_motion_tracking(token)
            self._bye_token = ""
            self._emit({"type": "close_renderer", "data": {"reason": "bye_motion_finished"}})
            return
        if token not in self._motion_launch_resolved:
            self._motion_launch_resolved.add(token)
            self._audio_owner_by_command.setdefault(token, self._audio_owner_renderer_id() or "")
            if started:
                self._scheduler.motion_started("emotion")
                self._emit_audio(self._behavior.motion_started(token), active)
            else:
                self._scheduler.motion_finished("emotion")
                self._emit_audio(self._behavior.motion_rejected(token), active)
        if not started:
            self._scheduler.motion_rejected("emotion")
            self._clear_motion_tracking(token)
            return
        if self._motion_finished.get(token, set()) >= started:
            self._scheduler.motion_finished("emotion")
            self._behavior.motion_finished(token)
            self._clear_motion_tracking(token)

    @staticmethod
    def _normalize_capabilities(data: Mapping[str, Any]) -> dict[str, bool]:
        capabilities = data.get("capabilities")
        motion_files = data.get("motion_files_by_group")
        motion_groups = data.get("motion_groups")
        catalog_has_motion = False
        if isinstance(motion_files, Mapping):
            catalog_has_motion = any(bool(files) for files in motion_files.values())
        elif isinstance(motion_groups, Mapping):
            catalog_has_motion = any(int(count or 0) > 0 for count in motion_groups.values())
        if isinstance(capabilities, Mapping):
            return {
                "motion": capabilities.get("motion") is True,
                "audio": capabilities.get("audio", True) is True,
                "lipsync": capabilities.get("lipsync") is True,
            }
        if isinstance(capabilities, (list, tuple, set, frozenset)):
            names = {str(value) for value in capabilities}
            return {"motion": "motion" in names, "audio": "audio" in names, "lipsync": "lipsync" in names}
        return {"motion": catalog_has_motion, "audio": True, "lipsync": catalog_has_motion}

    def _motion_renderer_ids(self) -> set[str]:
        return {
            renderer_id for renderer_id in self._renderer_ids
            if self._renderer_capabilities.get(renderer_id, {}).get("motion", False)
        }

    def _audio_renderer_ids(self) -> set[str]:
        return {
            renderer_id for renderer_id in self._renderer_ids
            if self._renderer_capabilities.get(renderer_id, {}).get("audio", True)
        }

    def _project_legacy_motion_complete(self) -> None:
        if self._legacy_motion_complete_value is None:
            return
        self._legacy_motion_complete_value.value = self._behavior.legacy_motion_complete

    def _canonical_renderer_is_sakiko(self) -> bool:
        """Use one stable runtime role when multiple renderer facts disagree."""
        for role in ("pygame", "electron"):
            candidates = sorted(
                renderer_id for renderer_id in self._renderer_ids
                if self._renderer_roles.get(renderer_id) == role
            )
            if candidates:
                return self._renderer_model_keys.get(candidates[0], "").lower() == "sakiko"
        return self._renderer_is_sakiko

    def _canonical_renderer_id(self) -> str | None:
        motion_renderers = self._motion_renderer_ids()
        for role in ("pygame", "electron"):
            candidates = sorted(
                renderer_id for renderer_id in motion_renderers
                if self._renderer_roles.get(renderer_id) == role
            )
            if candidates:
                return candidates[0]
        return sorted(motion_renderers)[0] if motion_renderers else None

    def _canonical_runtime_id(self) -> str | None:
        for role in ("pygame", "electron"):
            candidates = sorted(
                renderer_id for renderer_id in self._renderer_ids
                if self._renderer_roles.get(renderer_id) == role
            )
            if candidates:
                return candidates[0]
        return sorted(self._renderer_ids)[0] if self._renderer_ids else None

    def _apply_canonical_catalog(self) -> None:
        renderer_id = self._canonical_renderer_id()
        if renderer_id is None:
            if self._renderer_ids:
                self._behavior.set_capabilities({})
                self._scheduler.set_catalog({})
            return
        catalog = self._renderer_catalogs.get(renderer_id)
        if catalog is None:
            return
        kind, values, expression_ids = catalog
        if kind == "files":
            self._behavior.set_model_catalog(values, expression_ids)
            self._scheduler.set_model_catalog(values, expression_ids)
        else:
            self._behavior.set_capabilities(values)
            self._scheduler.set_catalog(values)

    def _renderer_instance_key(self, renderer_id: str) -> str:
        return f"{renderer_id}:{self._renderer_instances.get(renderer_id, renderer_id)}"

    def _maybe_sync_noncanonical_renderer(self, renderer_id: str) -> None:
        """Bring a newly connected secondary runtime to the canonical model.

        Renderer ``ready`` facts are capabilities, not business decisions. If
        the canonical runtime already has a model and a secondary Electron
        runtime reports a different token/key, issue one exact switch command
        so both backends execute the same owner-selected model.
        """
        if self._current_model_switch is not None:
            expected_token = str(self._current_model_switch.get("model_token") or "")
            if self._renderer_model_token(renderer_id) != expected_token:
                self._emit_model_switch_payload(self._current_model_switch, {renderer_id})
            return

        canonical_id = self._canonical_renderer_id()
        if canonical_id is None or canonical_id == renderer_id:
            return
        canonical_key = self._renderer_model_keys.get(canonical_id, "")
        current_key = self._renderer_model_keys.get(renderer_id, "")
        canonical_token = self._renderer_model_token(canonical_id)
        current_token = self._renderer_model_token(renderer_id)
        if canonical_key == current_key and canonical_token == current_token:
            return
        urls = self._model_urls_by_renderer.get(canonical_id, {})
        model_url = str(urls.get("model_json") or urls.get(canonical_key) or "")
        if not model_url and canonical_key.lower() == "sakiko":
            model_url = str(urls.get("black") or urls.get("white") or "")
        if not model_url:
            return
        model_url = self._electron_model_url(model_url)
        payload: dict[str, Any] = {
            "model_url": model_url,
            "electron_model_url": model_url,
            "character_folder": canonical_key,
            "character_folder_name": canonical_key,
            "model_token": canonical_token,
        }
        self._emit_model_switch_payload(payload, {renderer_id})

    @staticmethod
    def _electron_model_url(model_url: str) -> str:
        """Translate a Pygame/local model path to the bridge HTTP endpoint."""
        normalized = str(model_url or "").replace("\\", "/")
        if normalized.startswith(("http://", "https://")):
            return normalized
        marker = "live2d_related"
        if marker in normalized:
            relative = normalized.split(marker, 1)[1].lstrip("/")
            return f"http://127.0.0.1:9877/model/{relative}"
        return normalized

    def _renderer_model_token(self, renderer_id: str) -> str:
        # The token is kept separately by ``handle_renderer_fact`` so this
        # helper remains independent from capability catalog representation.
        return self._renderer_tokens.get(renderer_id, "")

    def _finish_pending_conversion_if_ready(self) -> None:
        """Release a conversion barrier once every live renderer is settled."""
        if self._pending_conversion is None or self._pending_conversion_renderers:
            return
        pending, self._pending_conversion = self._pending_conversion, None
        self._pending_conversion_model_token = ""
        switch = dict(self._pending_conversion_switch or {})
        self._pending_conversion_switch = None
        ready_renderers = set(self._pending_conversion_ready_renderers)
        self._pending_conversion_ready_renderers.clear()
        if not ready_renderers:
            self._conversion_replay_switch = None
            self._conversion_replay_motion = None
            self._conversion_replay_renderers.clear()
            return
        if switch:
            self._current_model_switch = deepcopy(switch)
        self._sakiko_conversion.commit(pending)
        if self._conversion_state_callback is not None:
            try:
                self._conversion_state_callback(self._sakiko_conversion.is_black, self._sakiko_conversion.mask_on)
            except Exception:
                pass
        self._conversion_replay_switch = switch or None
        self._conversion_replay_renderers = {
            self._renderer_instance_key(current_id)
            for current_id in self._renderer_ids
        }
        # A disconnect/unavailable fact can settle the barrier with no
        # executable renderer left.  Treat that as a rejected conversion,
        # rather than emitting an orphan command that can never produce the
        # started/finished facts required for the state commit.
        if not self._motion_renderer_ids():
            self._conversion_replay_switch = None
            self._conversion_replay_motion = None
            self._conversion_replay_renderers.clear()
            return
        if not self._emit_conversion_motion(pending):
            self._conversion_replay_switch = None
            self._conversion_replay_motion = None
            self._conversion_replay_renderers.clear()


class SharedRendererService:
    """Queue adapter for bridge deployments; its policy remains in the host."""

    _UI_INTENTS = frozenset({
        "open_python_settings",
        "start_voice_input",
        "stop_voice_input",
    })

    def __init__(self, intent_queue, renderer_fact_queue, command_queue,
                 owner: AuthoritativeLive2DOwner, legacy_motion_complete_value=None,
                 trace: CommandEmitter | None = None,
                 ui_intent_queue=None, conversion_state_callback=None) -> None:
        self._intents = intent_queue
        self._facts = renderer_fact_queue
        self._commands = command_queue
        self._trace = trace
        # Electron controls that belong to the mature Qt UI are forwarded as
        # UI requests. They are deliberately kept outside the Live2D owner.
        self._ui_intent_queue = ui_intent_queue
        self._host = SharedRendererHost(
            self._emit_command, owner, legacy_motion_complete_value,
            conversion_state_callback=conversion_state_callback,
        )
        self._pending_intents = deque()
        self._bye_handled = Event()
        self._bye_completed = Event()

    def _record_trace(self, kind: str, message: Mapping[str, Any]) -> None:
        if self._trace is None:
            return
        try:
            self._trace({
                "timestamp": time.time(),
                "kind": kind,
                "message": deepcopy(dict(message)),
            })
        except Exception:
            # Diagnostics must not change renderer lifecycle behavior.
            return

    def _emit_command(self, command: dict[str, Any]) -> None:
        self._record_trace("command", command)
        self._commands.put(command)
        if command.get("type") == "close_renderer":
            self._bye_completed.set()

    def run_once(self) -> int:
        handled = 0
        while True:
            try:
                fact = self._facts.get_nowait()
            except Empty:
                break
            if isinstance(fact, Mapping):
                self._record_trace("fact", fact)
                data = fact.get("data", {})
                ui_intent = data.get("intent") if isinstance(data, Mapping) else None
                if (fact.get("type") == "renderer_intent"
                        and isinstance(ui_intent, str)
                        and ui_intent in self._UI_INTENTS):
                    if self._ui_intent_queue is not None:
                        self._ui_intent_queue.put({"type": ui_intent})
                    handled += 1
                    continue
            handled += int(isinstance(fact, Mapping) and self._host.handle_renderer_fact(fact))
        while True:
            try:
                intent = self._intents.get_nowait()
            except Empty:
                break
            self._pending_intents.append(intent)
        emotion_processed = False
        scheduler_phase_done = False
        while self._pending_intents:
            intent = self._pending_intents[0]
            if not isinstance(intent, Mapping):
                self._pending_intents.popleft()
                continue
            intent_type = intent.get("type")
            if not scheduler_phase_done and intent_type not in {"runtime_control", "thinking_changed"}:
                # Match the upstream frame: controls and thinking edges are
                # consumed first, then due scheduler work runs before
                # conversion/emotion input is consumed.
                handled += int(self._host.tick(include_long_audio=False))
                scheduler_phase_done = True
            if intent_type == "emotion_segment" and self._host.model_switch_pending:
                # A switch command is a renderer barrier.  Leave the segment
                # queued until the matching ready fact commits its catalog.
                break
            if intent_type == "emotion_segment" and emotion_processed:
                # Upstream consumes at most one emotion/audio pair per frame.
                # Keep later pairs queued for the next owner cycle.
                break
            # Emotion segments require a renderer catalog; lifecycle/control
            # intents must still be consumed when model loading failed.
            if intent_type == "emotion_segment" and not self._host.ready:
                if self._host.all_renderers_unavailable:
                    self._pending_intents.popleft()
                    data = intent.get("data", {})
                    if isinstance(data, Mapping):
                        handled += int(self._host.reject_emotion_segment(
                            turn_id=str(data.get("turn_id", "")),
                            segment_id=str(data.get("segment_id", "")),
                            emotion=str(data.get("emotion", "")),
                            audio_path=str(data.get("audio_path", "")),
                            audio_duration_seconds=float(data.get("audio_duration_seconds", 0.0) or 0.0),
                        ))
                    continue
                control_index = next(
                    (index for index, candidate in enumerate(self._pending_intents)
                     if isinstance(candidate, Mapping) and candidate.get("type") != "emotion_segment"),
                    None,
                )
                if control_index is None:
                    break
                intent = self._pending_intents[control_index]
                del self._pending_intents[control_index]
                intent_type = intent.get("type")
            else:
                self._pending_intents.popleft()
            if intent_type != "emotion_segment":
                if intent_type == "bye":
                    handled += int(self._host.start_bye())
                    self._bye_handled.set()
                elif intent_type == "thinking_changed":
                    data = intent.get("data", {})
                    handled += int(isinstance(data, Mapping) and self._host.set_thinking(data.get("active") is True))
                elif intent_type == "sakiko_conversion":
                    data = intent.get("data", {})
                    handled += int(isinstance(data, Mapping) and self._host.start_sakiko_conversion(data.get("value"), data.get("model_urls", {})))
                elif intent_type == "runtime_control":
                    data = intent.get("data", {})
                    if isinstance(data, Mapping) and str(data.get("type") or "") == "cancel_turn":
                        # Match master cancellation semantics: queued
                        # segments belong to the cancelled turn and must not
                        # be emitted after the reset command.
                        self._pending_intents = deque(
                            candidate for candidate in self._pending_intents
                            if not (isinstance(candidate, Mapping) and candidate.get("type") == "emotion_segment")
                        )
                        # Also discard emotion intents that arrived in the
                        # transport queue while cancellation was being routed.
                        retained = []
                        while True:
                            try:
                                queued = self._intents.get_nowait()
                            except Empty:
                                break
                            if not (isinstance(queued, Mapping) and queued.get("type") == "emotion_segment"):
                                retained.append(queued)
                        self._pending_intents.extend(retained)
                    handled += int(isinstance(data, Mapping) and self._host.handle_runtime_control(data))
                continue
            data = intent.get("data", {})
            if not isinstance(data, Mapping):
                continue
            handled += int(self._host.start_emotion_segment(
                turn_id=str(data.get("turn_id", "")), segment_id=str(data.get("segment_id", "")),
                emotion=str(data.get("emotion", "")), audio_path=str(data.get("audio_path", "")),
                audio_duration_seconds=float(data.get("audio_duration_seconds", 0.0) or 0.0),
            ))
            emotion_processed = True
        if not scheduler_phase_done:
            handled += int(self._host.tick(include_long_audio=False))
        # Upstream checks long-audio repeats after conversion and emotion.
        handled += int(self._host.tick_long_audio())
        return handled

    def wait_for_bye(self, timeout_seconds: float = 2.0) -> bool:
        """Wait until the queued bye intent has become an exact runtime command."""
        return self._bye_handled.wait(max(0.0, float(timeout_seconds)))

    def wait_for_bye_completion(self, timeout_seconds: float = 6.0) -> bool:
        """Wait until the exact bye lifecycle has emitted close_renderer."""
        return self._bye_completed.wait(max(0.0, float(timeout_seconds)))

    def run(self, stop_event, poll_interval_seconds: float = 0.02) -> None:
        """Run until the caller's lifecycle owner requests a clean stop."""
        drain_deadline = None
        while True:
            handled = self.run_once()
            if stop_event.is_set():
                if self._bye_handled.is_set():
                    break
                if drain_deadline is None:
                    drain_deadline = time.monotonic() + 2.0
                # A shutdown intent may have been queued immediately before
                # the stop event. Give the owner a bounded opportunity to
                # turn it into an exact close/bye command.
                if not self._pending_intents and (self._intents.empty() or not self._host.ready):
                    break
                if time.monotonic() >= drain_deadline:
                    break
            if handled == 0:
                time.sleep(poll_interval_seconds)
