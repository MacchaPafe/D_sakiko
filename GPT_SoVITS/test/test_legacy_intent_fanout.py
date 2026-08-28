from __future__ import annotations

import os
import sys
import unittest
from queue import Queue

root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root not in sys.path:
    sys.path.insert(0, root)

from live2d_support.legacy_intent_fanout import LegacyEmotionAudioFanout, OrderedLegacyOwnerIngress


class LegacyEmotionAudioFanoutTest(unittest.TestCase):
    def setUp(self):
        self.source_emotion, self.source_audio = Queue(), Queue()
        self.pygame_emotion, self.pygame_audio, self.intents = Queue(), Queue(), Queue()
        self.fanout = LegacyEmotionAudioFanout(
            self.source_emotion, self.source_audio, self.pygame_emotion, self.pygame_audio, self.intents,
        )

    def test_mirrors_one_ordered_emotion_audio_pair(self):
        self.source_audio.put("voice.wav")
        self.source_emotion.put("LABEL_0")
        self.assertTrue(self.fanout.run_once())
        self.assertEqual(self.pygame_emotion.get_nowait(), "LABEL_0")
        self.assertEqual(self.pygame_audio.get_nowait(), "voice.wav")
        intent = self.intents.get_nowait()
        self.assertEqual((intent["type"], intent["data"]["emotion"], intent["data"]["audio_path"]),
                         ("emotion_segment", "LABEL_0", "voice.wav"))

    def test_bye_has_no_audio_pair_on_either_path(self):
        self.source_audio.put("must-remain.wav")
        self.source_emotion.put("bye")
        self.assertTrue(self.fanout.run_once())
        self.assertEqual(self.pygame_emotion.get_nowait(), "bye")
        self.assertTrue(self.pygame_audio.empty())
        self.assertEqual(self.intents.get_nowait()["type"], "bye")
        self.assertEqual(self.source_audio.get_nowait(), "must-remain.wav")

    def test_authoritative_cutover_stops_delivering_pygame_baseline_inputs(self):
        fanout = LegacyEmotionAudioFanout(
            self.source_emotion, self.source_audio, self.pygame_emotion, self.pygame_audio, self.intents,
            deliver_pygame_baseline=False,
        )
        self.source_audio.put("voice.wav")
        self.source_emotion.put("LABEL_0")
        self.assertTrue(fanout.run_once())
        self.assertTrue(self.pygame_emotion.empty())
        self.assertTrue(self.pygame_audio.empty())
        self.assertEqual(self.intents.get_nowait()["type"], "emotion_segment")

    def test_ordered_ingress_processes_control_before_emotion(self):
        controls, conversions = Queue(), Queue()
        controls.put({"type": "cancel_turn"})
        self.source_audio.put("voice.wav")
        self.source_emotion.put("LABEL_0")
        from live2d_support.runtime_ingress import LegacyControlIntentFanout
        ordered = OrderedLegacyOwnerIngress(
            LegacyEmotionAudioFanout(self.source_emotion, self.source_audio,
                                     self.pygame_emotion, self.pygame_audio, self.intents),
            LegacyControlIntentFanout(controls, conversions, self.intents),
        )
        self.assertEqual(ordered.run_once(), 2)
        self.assertEqual(self.intents.get_nowait()["data"]["type"], "cancel_turn")
        self.assertEqual(self.intents.get_nowait()["type"], "emotion_segment")

    def test_ordered_ingress_preserves_upstream_control_conversion_thinking_emotion_order(self):
        controls, conversions, thinking = Queue(), Queue(), Queue()
        controls.put({"type": "cancel_turn"})
        conversions.put("maskoff")
        thinking.put({"type": "thinking_changed", "data": {"active": False}})
        self.source_audio.put("voice.wav")
        self.source_emotion.put("LABEL_0")
        from live2d_support.runtime_ingress import LegacyControlIntentFanout
        ordered = OrderedLegacyOwnerIngress(
            LegacyEmotionAudioFanout(self.source_emotion, self.source_audio,
                                     self.pygame_emotion, self.pygame_audio, self.intents),
            LegacyControlIntentFanout(controls, conversions, self.intents),
            thinking,
        )
        self.assertEqual(ordered.run_once(), 4)
        events = [self.intents.get_nowait(), self.intents.get_nowait(),
                  self.intents.get_nowait(), self.intents.get_nowait()]
        self.assertEqual(
            [event["type"] for event in events],
            ["runtime_control", "thinking_changed", "sakiko_conversion", "emotion_segment"],
        )

    def test_cancel_discards_three_raw_segments_before_owner_conversion(self):
        controls, conversions = Queue(), Queue()
        for index in range(3):
            self.source_emotion.put(f"LABEL_{index}")
            self.source_audio.put(f"voice-{index}.wav")
        controls.put({"type": "cancel_turn"})
        from live2d_support.runtime_ingress import LegacyControlIntentFanout
        control = LegacyControlIntentFanout(
            controls, conversions, self.intents,
            cancel_callback=self.fanout.discard_pending,
        )
        ordered = OrderedLegacyOwnerIngress(
            self.fanout, control,
        )
        self.assertEqual(ordered.run_once(), 1)
        self.assertTrue(self.source_emotion.empty())
        self.assertTrue(self.source_audio.empty())
        self.assertEqual(self.intents.get_nowait()["data"]["type"], "cancel_turn")
        self.assertFalse(self.intents.qsize())

    def test_cancel_preserves_queued_bye_marker(self):
        controls, conversions = Queue(), Queue()
        self.source_emotion.put("LABEL_0")
        self.source_audio.put("voice.wav")
        self.source_emotion.put("bye")
        controls.put({"type": "cancel_turn"})
        from live2d_support.runtime_ingress import LegacyControlIntentFanout
        ordered = OrderedLegacyOwnerIngress(
            self.fanout,
            LegacyControlIntentFanout(
                controls, conversions, self.intents,
                cancel_callback=self.fanout.discard_pending,
            ),
        )
        ordered.run_once()
        self.assertTrue(self.source_emotion.empty())
        self.assertEqual(self.intents.get_nowait()["type"], "runtime_control")
        self.assertEqual(self.intents.get_nowait()["type"], "bye")


if __name__ == "__main__":
    unittest.main()
