from __future__ import annotations
import os, sys, unittest
from random import Random
script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); sys.path.insert(0, script_dir) if script_dir not in sys.path else None
from live2d_support.behavior_scheduler import SharedBehaviorScheduler

class Clock:
    value = 0.0
    def __call__(self): return self.value

class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(); self.s = SharedBehaviorScheduler(clock=self.clock, rng=Random(0))
        self.s.set_catalog({"text_generating": 1, "idle_motion": 1, "IDLE": 2, "happiness": 2})
    def test_thinking_first_then_repeat(self):
        self.s.set_thinking(True); self.clock.value = 0.9; self.assertIsNone(self.s.tick())
        self.clock.value = 1.0; self.assertEqual(self.s.tick().purpose, "thinking")
        self.s.motion_finished("thinking"); self.clock.value = 15.9; self.assertIsNone(self.s.tick())
        self.clock.value = 16.0; self.assertEqual(self.s.tick().purpose, "thinking")

    def test_thinking_deadline_survives_a_session_boundary(self):
        self.clock.value = 0.5
        self.s.set_thinking(True)
        self.clock.value = 1.1
        self.assertEqual(self.s.tick().purpose, "thinking")
        self.s.motion_finished("thinking")
        self.s.set_thinking(False)
        self.clock.value = 5.0
        self.s.set_thinking(True)
        self.clock.value = 15.9
        self.assertIsNone(self.s.tick())
        self.clock.value = 16.1
        self.assertEqual(self.s.tick().purpose, "thinking")
    def test_long_audio_is_fact_gated_and_bounded(self):
        self.s.start_segment("happiness", 6.0); self.s.set_audio_busy(True); self.s.motion_finished("emotion")
        self.clock.value = 2.5; self.assertEqual(self.s.tick().purpose, "long_audio_repeat")
        self.s.motion_finished("long_audio_repeat"); self.clock.value = 5.0; self.assertEqual(self.s.tick().purpose, "long_audio_repeat")
        self.s.motion_finished("long_audio_repeat"); self.clock.value = 7.5; self.assertIsNone(self.s.tick())
    def test_external_motion_finish_edge_starts_long_audio_delay(self):
        self.s.start_segment("happiness", 6.0); self.s.set_audio_busy(True); self.s.set_motion_over(False)
        self.s.set_motion_over(True); self.clock.value = 2.5; self.assertEqual(self.s.long_audio_due().purpose, "long_audio_repeat")
    def test_audio_started_after_motion_finish_still_starts_long_audio_delay(self):
        self.s.start_segment("happiness", 6.0); self.s.motion_finished("emotion"); self.s.set_audio_busy(True)
        self.clock.value = 2.5; self.assertEqual(self.s.long_audio_due().purpose, "long_audio_repeat")
    def test_explicit_intent_is_resolved_to_exact_motion(self):
        self.s.set_catalog({"bye": 2}); command = self.s.request_motion("bye", 3, "bye")
        self.assertEqual((command.group, command.priority, command.purpose), ("bye", 3, "bye"))
    def test_semantic_expression_is_resolved_centrally(self):
        self.s.set_model_catalog({}, ("exp_serious01",)); self.assertEqual(self.s.resolve_semantic_expression("serious"), "exp_serious01")
    def test_fixed_motion_is_validated_by_shared_catalog(self):
        self.s.set_catalog({"text_generating": 1}); self.assertEqual(self.s.request_fixed_motion("text_generating", 0, 3, "mask_white").index, 0)
        self.assertIsNone(self.s.request_fixed_motion("text_generating", 1, 3, "mask_white"))
    def test_idle_and_click_preserve_master_conditions(self):
        self.clock.value = 2.5; self.assertIsNone(self.s.tick())
        self.s.start_segment("happiness", 1.0); self.s.motion_finished("emotion")
        self.clock.value = 5.0; self.assertEqual(self.s.tick().purpose, "idle_recover")
        self.assertIsNone(self.s.click(is_sakiko=False)); self.assertEqual(self.s.click(is_sakiko=True).purpose, "click")
    def test_center_variant_is_resolved_before_executor(self):
        self.s.set_catalog({"IDLE": 1, "IDLE_C": 2})
        self.assertEqual(self.s.click(is_sakiko=True).group, "IDLE_C")
    def test_catalog_resolves_expression_before_executor(self):
        self.s.set_model_catalog({"IDLE": ("idle_smile.mtn",)}, ("exp_smile01",))
        self.assertEqual(self.s.click(is_sakiko=True).expression_id, "exp_smile01")
    def test_timed_idle_resets_deadline_even_when_audio_blocks_it(self):
        self.clock.value = 25.0; self.s.set_audio_busy(True); self.assertIsNone(self.s.timed_idle_due())
        self.s.set_audio_busy(False); self.clock.value = 49.9; self.assertIsNone(self.s.timed_idle_due())
        self.clock.value = 50.0; self.assertEqual(self.s.timed_idle_due().purpose, "timed_idle")
    def test_idle_recovery_uses_renderer_motion_fact(self):
        self.clock.value = 2.5; self.s.set_motion_over(False); self.assertIsNone(self.s.idle_recover_due())
        self.s.set_motion_over(True); self.assertIsNone(self.s.idle_recover_due())
        self.clock.value = 5.0; self.assertEqual(self.s.idle_recover_due().purpose, "idle_recover")

    def test_missing_idle_motion_does_not_block_timed_idle(self):
        self.s.set_catalog({"IDLE": 1})
        self.clock.value = 2.5
        self.assertIsNone(self.s.tick())
        self.clock.value = 25.0
        self.assertEqual(self.s.tick().purpose, "timed_idle")

    def test_idle_recovery_does_not_starve_next_timed_idle(self):
        self.s.start_segment("happiness", 1.0); self.s.motion_finished("emotion")
        self.clock.value = 2.5
        self.assertEqual(self.s.tick().purpose, "idle_recover")
        self.s.motion_finished("idle_recover")
        self.clock.value = 25.0
        self.assertEqual(self.s.tick().purpose, "timed_idle")

    def test_rejected_long_audio_motion_cannot_schedule_repeat(self):
        self.s.start_segment("happiness", 6.0)
        self.s.motion_rejected("emotion")
        self.s.set_audio_busy(True)
        self.clock.value = 2.5
        self.assertIsNone(self.s.tick())

    def test_timed_idle_deadline_advances_while_audio_or_thinking_is_busy(self):
        self.clock.value = 25.0
        self.s.set_audio_busy(True)
        self.assertIsNone(self.s.tick())
        self.s.set_audio_busy(False)
        self.clock.value = 49.9
        self.assertIsNone(self.s.tick())
        self.clock.value = 50.0
        self.assertEqual(self.s.tick().purpose, "timed_idle")

    def test_cancel_preserves_near_deadline_timed_idle(self):
        self.clock.value = 24.0
        self.s.reset_after_cancel()
        self.clock.value = 25.0
        self.assertEqual(self.s.tick().purpose, "timed_idle")

    def test_reset_long_audio_does_not_touch_idle_or_thinking_deadlines(self):
        self.s.set_thinking(True)
        self.s.start_segment("happiness", 6.0)
        self.s.set_audio_busy(True)
        self.s.reset_long_audio()
        self.clock.value = 2.5
        self.assertIsNone(self.s.tick_long_audio())
        self.assertTrue(self.s._thinking_due == 1.0)

if __name__ == '__main__': unittest.main()
