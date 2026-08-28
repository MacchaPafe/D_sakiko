"""Route legacy emotion/audio input into the single authoritative owner."""
from __future__ import annotations

from queue import Empty
import time

from live2d_support.audio_duration import read_audio_duration_seconds


class LegacyEmotionAudioFanout:
    """Consume each legacy pair once and publish one ordered owner intent.

    The optional Pygame queues remain only for callers that have not completed
    migration; the production startup disables that compatibility delivery.
    """

    def __init__(self, emotion_input, audio_input, pygame_emotions, pygame_audio, owner_intents,
                 *, deliver_pygame_baseline: bool = True) -> None:
        self._emotion_input = emotion_input
        self._audio_input = audio_input
        self._pygame_emotions = pygame_emotions
        self._pygame_audio = pygame_audio
        self._owner_intents = owner_intents
        self._deliver_pygame_baseline = deliver_pygame_baseline
        self._sequence = 0

    def run_once(self) -> bool:
        try:
            emotion = self._emotion_input.get_nowait()
        except Empty:
            return False
        if emotion == "bye":
            if self._deliver_pygame_baseline:
                self._pygame_emotions.put(emotion)
            self._owner_intents.put({"type": "bye", "data": {}})
            return True
        # This deliberate wait exactly preserves master Pygame pairing: after
        # consuming an emotion it waits for that segment's audio item.
        audio_path = self._audio_input.get()
        self._sequence += 1
        if self._deliver_pygame_baseline:
            self._pygame_audio.put(audio_path)
            self._pygame_emotions.put(emotion)
        self._owner_intents.put({
            "type": "emotion_segment",
            "data": {
                "turn_id": "legacy-ingress",
                "segment_id": str(self._sequence),
                "emotion": str(emotion),
                "audio_path": str(audio_path),
                "audio_duration_seconds": read_audio_duration_seconds(str(audio_path)),
            },
        })
        return True

    def discard_pending(self) -> None:
        """Drop raw segments that were queued before an upstream cancel.

        This is deliberately done at the legacy input boundary, before a
        segment can be converted into a new owner intent.  Preserve the old
        special bye marker if it was already queued.
        """
        saw_bye = False
        while True:
            try:
                emotion = self._emotion_input.get_nowait()
            except Empty:
                break
            saw_bye = saw_bye or emotion == "bye"
        while True:
            try:
                self._audio_input.get_nowait()
            except Empty:
                break
        if saw_bye:
            self._emotion_input.put("bye")

    def run(self, stop_event, poll_interval_seconds: float = 0.02) -> None:
        while not stop_event.is_set():
            if not self.run_once():
                time.sleep(poll_interval_seconds)


class OrderedLegacyOwnerIngress:
    """Serialize legacy owner ingress in the upstream Pygame order.

    The old loop handles controls/conversion before consuming one emotion and
    its paired audio item.  Keeping these consumers on one worker prevents
    independent fanout threads from reordering a cancel or conversion ahead of
    an already-visible Pygame event.
    """

    def __init__(self, emotion_fanout: LegacyEmotionAudioFanout, control_fanout,
                 thinking_intents=None) -> None:
        self._emotion = emotion_fanout
        self._control = control_fanout
        self._thinking = thinking_intents

    def run_once(self) -> int:
        # The legacy loop consumes control, then the thinking edge, then
        # conversion, and finally one emotion/audio pair per frame.
        handled = self._control.run_once(max_items=1, include_conversions=False)
        if self._thinking is not None:
            try:
                self._control.owner_intents.put(self._thinking.get_nowait())
                handled += 1
            except Empty:
                pass
        # Conversion is a distinct phase after thinking in the upstream loop.
        handled += self._control.run_once(max_items=1, include_controls=False, include_conversions=True)
        handled += int(self._emotion.run_once())
        return handled

    def run(self, stop_event, poll_interval_seconds: float = 0.02) -> None:
        while not stop_event.is_set():
            if self.run_once() == 0:
                time.sleep(poll_interval_seconds)
