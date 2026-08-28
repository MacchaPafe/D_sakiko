"""Ingress adapters that preserve legacy queue contracts while routing intents once."""
from __future__ import annotations

from queue import Empty
import time


class ThinkingStateQueue:
    """Compatibility queue that mirrors producer/consumer edges to the owner."""

    def __init__(self, queue, intent_queue, count=None, *, ingress_queue=None) -> None:
        self._queue = queue
        self._intents = intent_queue
        self._ingress = ingress_queue
        self._count = count

    def put(self, value, *args, **kwargs):
        result = self._queue.put(value, *args, **kwargs)
        if self._count is not None:
            self._change_count(1)
        else:
            self._emit_intent({"type": "thinking_changed", "data": {"active": True}})
        return result

    def get(self, *args, **kwargs):
        value = self._queue.get(*args, **kwargs)
        if self._count is not None:
            self._change_count(-1)
        else:
            self._mirror_idle_edge()
        return value

    def get_nowait(self):
        value = self._queue.get_nowait()
        if self._count is not None:
            self._change_count(-1)
        else:
            self._mirror_idle_edge()
        return value

    def empty(self):
        return self._queue.empty()

    def qsize(self):
        return self._queue.qsize()

    def _mirror_idle_edge(self) -> None:
        try:
            empty = self._queue.empty()
        except (AttributeError, NotImplementedError):
            empty = False
        if empty:
            self._emit_intent({"type": "thinking_changed", "data": {"active": False}})

    def _change_count(self, delta: int) -> None:
        lock = self._count.get_lock()
        with lock:
            before = self._count.value
            self._count.value = max(0, before + delta)
            after = self._count.value
        if before == 0 and after > 0:
            self._emit_intent({"type": "thinking_changed", "data": {"active": True}})
        elif before > 0 and after == 0:
            self._emit_intent({"type": "thinking_changed", "data": {"active": False}})

    def _emit_intent(self, intent) -> None:
        (self._ingress or self._intents).put(intent)


class FanoutQueue:
    """Queue-like command sink used to broadcast one owner decision to runtimes."""

    def __init__(self, *queues) -> None:
        self._queues = tuple(queue for queue in queues if queue is not None)

    def put(self, value, *args, **kwargs):
        for queue in self._queues:
            queue.put(value, *args, **kwargs)


class LegacyControlIntentFanout:
    """Move control/conversion inputs to the same owner ingress as segments."""

    def __init__(self, control_queue, conversion_queue, owner_intents, runtime_queue=None,
                 cancel_callback=None) -> None:
        self._control = control_queue
        self._conversion = conversion_queue
        self._intents = owner_intents
        self._runtime = runtime_queue
        self._cancel_callback = cancel_callback

    def run_once(self, *, max_items: int | None = None,
                 include_controls: bool = True,
                 include_conversions: bool = True) -> int:
        handled = 0
        while include_controls and (max_items is None or handled < max_items):
            try:
                command = self._control.get_nowait()
            except Empty:
                break
            if command == "exit":
                self._intents.put({"type": "bye", "data": {}})
            elif isinstance(command, dict):
                command_type = str(command.get("type") or "")
                if command_type == "cancel_turn" and self._cancel_callback is not None:
                    self._cancel_callback()
                if command_type in {"change_l2d_background", "switch_l2d_fps", "toggle_l2d_layout_edit"} and self._runtime is not None:
                    self._runtime.put(dict(command))
                else:
                    self._intents.put({"type": "runtime_control", "data": dict(command)})
            handled += 1
        conversion_handled = 0
        while max_items is None or conversion_handled < max_items:
            if not include_conversions:
                break
            try:
                conversion = self._conversion.get_nowait()
            except Empty:
                break
            self._intents.put({"type": "sakiko_conversion", "data": {"value": conversion}})
            handled += 1
            conversion_handled += 1
        return handled

    @property
    def owner_intents(self):
        return self._intents

    def run(self, stop_event, poll_interval_seconds: float = 0.02) -> None:
        while not stop_event.is_set():
            if self.run_once() == 0:
                time.sleep(poll_interval_seconds)
