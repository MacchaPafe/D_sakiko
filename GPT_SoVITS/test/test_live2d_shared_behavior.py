from __future__ import annotations

import os
import sys
import unittest
from random import Random

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from live2d_support.shared_behavior import SharedLive2DBehavior
from live2d_support.behavior_scheduler import SharedBehaviorScheduler


class SharedLive2DBehaviorTraceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.behavior = SharedLive2DBehavior(rng=Random(7))
        self.behavior.set_capabilities({"happiness": 3, "sadness": 1})

    def test_emotion_decision_is_exact_and_audio_projection_follows_facts(self) -> None:
        command = self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="segment", emotion="LABEL_0",
            audio_path="answer.wav", audio_duration_seconds=2.0,
        )
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.motion.group, "happiness")
        self.assertIn(command.motion.index, range(3))
        self.assertEqual((command.motion.priority, command.motion.position), (3, "C"))
        self.assertTrue(self.behavior.legacy_motion_complete)
        audio_command = self.behavior.motion_started(command.command_id)
        self.assertIsNotNone(audio_command)
        assert audio_command is not None
        self.assertEqual(audio_command.audio_path, "answer.wav")
        self.assertTrue(self.behavior.audio_started(command.command_id))
        self.assertFalse(self.behavior.legacy_motion_complete)
        self.assertTrue(self.behavior.motion_finished(command.command_id))
        self.assertFalse(self.behavior.legacy_motion_complete)
        self.assertTrue(self.behavior.audio_ended(command.command_id))
        self.assertTrue(self.behavior.legacy_motion_complete)

    def test_motion_rejection_preserves_pygame_audio_fallback(self) -> None:
        command = self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="segment", emotion="LABEL_0", audio_path="answer.wav",
        )
        assert command is not None
        self.assertIsNotNone(self.behavior.motion_rejected(command.command_id))
        self.assertIsNone(self.behavior.motion_rejected(command.command_id))
        self.assertTrue(self.behavior.audio_started(command.command_id))
        self.assertFalse(self.behavior.legacy_motion_complete)
        self.assertTrue(self.behavior.audio_ended(command.command_id))
        self.assertTrue(self.behavior.legacy_motion_complete)

    def test_unknown_emotion_has_no_command(self) -> None:
        self.assertIsNone(self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="segment", emotion="unknown", audio_path="left-queued.wav",
        ))

    def test_known_emotion_without_motion_capability_uses_audio_only_fallback(self) -> None:
        self.behavior.set_capabilities({})
        command = self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="segment", emotion="LABEL_0", audio_path="answer.wav",
        )
        self.assertIsNotNone(command)
        assert command is not None
        self.assertIsNone(command.motion)

    def test_catalog_resolves_center_group_and_expression_before_adapter_execution(self) -> None:
        self.behavior.set_model_catalog(
            {
                "happiness": ("happy_base.mtn",),
                "happiness_C": ("happy_smile.mtn", "happy_other.mtn"),
            },
            expression_ids=("exp_smile01",),
        )
        command = self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="segment", emotion="LABEL_0", audio_path="answer.wav",
        )
        assert command is not None
        self.assertEqual(command.motion.group, "happiness_C")
        self.assertIn(command.motion.index, range(2))
        self.assertEqual(command.motion.expression_id, "exp_smile01")

    def test_counts_only_catalog_clears_previous_motion_and_expression_facts(self) -> None:
        self.behavior.set_model_catalog(
            {"happiness": ("happy_smile.mtn",)},
            expression_ids=("exp_smile01",),
        )
        detailed = self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="detailed", emotion="LABEL_0", audio_path="answer.wav",
        )
        assert detailed is not None and detailed.motion is not None
        self.assertEqual(detailed.motion.expression_id, "exp_smile01")

        # A renderer may only report group counts after a model reload.  The
        # old file-name/expression facts must not leak into the next decision.
        self.behavior.set_capabilities({"happiness": 1})
        counts_only = self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="counts-only", emotion="LABEL_0", audio_path="answer.wav",
        )
        assert counts_only is not None and counts_only.motion is not None
        self.assertIsNone(counts_only.motion.expression_id)

    def test_scheduler_counts_only_catalog_clears_previous_expression_facts(self) -> None:
        scheduler = SharedBehaviorScheduler(clock=lambda: 0.0, rng=Random(1))
        scheduler.set_model_catalog({"idle_motion": ("idle_smile.mtn",)}, ("exp_idle01",))
        detailed = scheduler.request_motion("idle_motion", 1, "idle")
        assert detailed is not None
        self.assertEqual(detailed.expression_id, "exp_idle01")
        scheduler.set_catalog({"idle_motion": 1})
        counts_only = scheduler.request_motion("idle_motion", 1, "idle")
        assert counts_only is not None
        self.assertIsNone(counts_only.expression_id)

    def test_failure_and_stale_facts_cannot_leave_segment_busy(self) -> None:
        command = self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="segment", emotion="LABEL_0", audio_path="answer.wav",
        )
        assert command is not None
        self.assertFalse(self.behavior.audio_started("stale"))
        self.assertTrue(self.behavior.command_failed(command.command_id, "audio_start"))
        self.assertTrue(self.behavior.legacy_motion_complete)
        self.assertIsNone(self.behavior.active_command)

    def test_motion_start_failure_issues_one_audio_fallback_command(self) -> None:
        command = self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="segment", emotion="LABEL_0", audio_path="answer.wav",
        )
        assert command is not None
        fallback = self.behavior.command_failed(command.command_id, "motion_start")
        self.assertIsNotNone(fallback)
        self.assertIsNone(self.behavior.motion_started(command.command_id))


if __name__ == "__main__":
    unittest.main()
