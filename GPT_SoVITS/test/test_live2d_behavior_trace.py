"""Golden traces for the first master-Pygame emotion/audio migration slice.

The fixture is intentionally renderer-free: it captures the observable order
in ``live2d_module.py`` before the new core becomes authoritative.  In
particular, an audio item is dequeued immediately after its emotion item, even
when the emotion has no supported motion group.
"""
from __future__ import annotations

import os
import sys
import unittest
from random import Random

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from live2d_support.motion_semantics import motion_group_for_emotion
from live2d_support.shared_behavior import SharedLive2DBehavior


def master_pygame_emotion_trace(emotion: str, audio_path: str, motion_accepts: bool):
    """Normalized trace of the master loop's emotion branch.

    This records business-visible operations only; mixer, lip-sync and Cubism
    calls remain renderer mechanics and are deliberately outside this oracle.
    """
    trace = [("emotion_dequeued", emotion), ("audio_dequeued", audio_path)]
    group = motion_group_for_emotion(emotion, default="")
    if not group:
        return trace + [("unknown_emotion", emotion)]
    trace.append(("random_motion_requested", group, 3, "C"))
    if motion_accepts:
        return trace + [("motion_started",), ("audio_start", audio_path)]
    return trace + [
        ("motion_rejected",),
        ("audio_start", audio_path),
        ("motion_is_over", True),
        ("long_audio_reset",),
    ]


class MasterPygameEmotionTraceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.behavior = SharedLive2DBehavior(rng=Random(0))
        # A one-item catalog makes the master random request comparable to an
        # exact command without asserting a private SDK RNG implementation.
        self.behavior.set_capabilities({"happiness": 1})

    def test_unknown_label_consumes_its_fifo_audio_before_being_skipped(self) -> None:
        expected = [
            ("emotion_dequeued", "not-a-label"),
            ("audio_dequeued", "paired.wav"),
            ("unknown_emotion", "not-a-label"),
        ]
        self.assertEqual(master_pygame_emotion_trace("not-a-label", "paired.wav", True), expected)
        self.assertIsNone(self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="segment", emotion="not-a-label", audio_path="paired.wav",
        ))

    def test_exact_command_and_rejected_motion_follow_master_fallback_trace(self) -> None:
        command = self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="segment", emotion="LABEL_0", audio_path="paired.wav",
        )
        assert command is not None
        self.assertEqual((command.motion.group, command.motion.index, command.motion.priority, command.motion.position),
                         ("happiness", 0, 3, "C"))
        self.assertEqual(
            master_pygame_emotion_trace("LABEL_0", "paired.wav", False)[2:],
            [
                ("random_motion_requested", "happiness", 3, "C"),
                ("motion_rejected",),
                ("audio_start", "paired.wav"),
                ("motion_is_over", True),
                ("long_audio_reset",),
            ],
        )
        self.assertTrue(self.behavior.motion_rejected(command.command_id))
        self.assertTrue(self.behavior.audio_started(command.command_id))
        self.assertFalse(self.behavior.legacy_motion_complete)


if __name__ == "__main__":
    unittest.main()
