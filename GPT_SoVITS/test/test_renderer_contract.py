from __future__ import annotations

import os
import sys
import unittest

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from live2d_support.renderer_contract import audio_command, motion_command, normalize_renderer_fact
from live2d_support.shared_behavior import ExactMotion, PlaySegment, StartAudio


class RendererContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.segment = PlaySegment("cmd", "turn", "segment", ExactMotion("happiness_C", 2, expression_id="exp_smile01"), "answer.wav", 1.0)

    def test_exact_command_contains_no_renderer_choice(self) -> None:
        command = motion_command(self.segment)
        assert command is not None
        self.assertEqual(command["data"], {
            "token": "cmd", "turn_id": "turn", "segment_id": "segment",
            "group": "happiness_C", "index": 2, "priority": 3,
            "position": "C", "expression_id": "exp_smile01",
        })
        self.assertEqual(audio_command(StartAudio("cmd", "answer.wav"), self.segment)["data"]["path"], "answer.wav")

    def test_command_failed_is_not_dropped(self) -> None:
        self.assertEqual(normalize_renderer_fact({"type": "command_failed", "data": {"token": "cmd", "reason": "motion_not_started"}}), ("motion_rejected", "cmd"))
        self.assertEqual(normalize_renderer_fact({"type": "command_failed", "data": {"token": "cmd", "phase": "audio_start"}}), ("command_failed", "cmd"))


if __name__ == "__main__":
    unittest.main()
