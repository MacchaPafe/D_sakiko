from __future__ import annotations

import unittest
from queue import Queue

from live2d_support.runtime_ingress import FanoutQueue, LegacyControlIntentFanout, ThinkingStateQueue


class RuntimeIngressTest(unittest.TestCase):
    def test_control_and_conversion_are_forwarded_once(self):
        controls, conversions, intents, runtime = Queue(), Queue(), Queue(), Queue()
        controls.put({"type": "cancel_turn"})
        conversions.put("maskoff")
        fanout = LegacyControlIntentFanout(controls, conversions, intents, runtime)
        self.assertEqual(fanout.run_once(), 2)
        self.assertEqual(intents.get_nowait()["data"]["type"], "cancel_turn")
        self.assertEqual(intents.get_nowait()["data"]["value"], "maskoff")

    def test_thinking_compatibility_queue_mirrors_edges(self):
        source, intents = Queue(), Queue()
        queue = ThinkingStateQueue(source, intents)
        queue.put("no_complete")
        self.assertTrue(intents.get_nowait()["data"]["active"])
        self.assertEqual(queue.get_nowait(), "no_complete")
        self.assertFalse(intents.get_nowait()["data"]["active"])

    def test_fanout_does_not_redecide_command(self):
        first, second = Queue(), Queue()
        command = {"type": "play_motion", "data": {"group": "IDLE", "index": 1}}
        FanoutQueue(first, second).put(command)
        self.assertEqual(first.get_nowait(), command)
        self.assertEqual(second.get_nowait(), command)


if __name__ == "__main__":
    unittest.main()
