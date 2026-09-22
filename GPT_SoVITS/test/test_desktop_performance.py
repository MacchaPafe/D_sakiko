import unittest
from unittest.mock import Mock, patch
from runtime.single_character_performance import SingleCharacterPerformance


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.player = SingleCharacterPerformance()
        self.model = Mock()
        self.model.StartRandomMotion.return_value = False
        self.events = []
        self.player.on_event = self.events.append
        self.subtitles = []
        self.player.on_subtitle = self.subtitles.append

    def segment(self, number, turn="t", audio="NO_AUDIO"):
        return dict(
            type="play_segment",
            chat_id="a",
            turn_id=turn,
            segment_id=number,
            audio_path=audio,
            text=f"text{number}",
            translation="",
            emotion="LABEL_0",
        )

    def test_no_model_or_motion_still_completes_fifo_text(self):
        self.player.command(self.segment(1), self.model)
        self.player.command(self.segment(2), self.model)
        with patch.object(self.player, "audio_busy", return_value=False):
            self.player.update_playback(self.model)
            self.player.update_playback(self.model)
        self.assertEqual([e["segment_id"] for e in self.events], [1, 2])
        self.assertEqual(self.subtitles, ["text1", "text2"])
        self.assertFalse(self.player.busy)

    def test_delayed_motion_start_cannot_restart_cancelled_audio(self):
        callbacks = []

        def start(group, priority, on_start, on_finish, **kwargs):
            callbacks.append(on_start)
            return True

        self.model.StartRandomMotion.side_effect = start
        self.player.command(self.segment(1, audio="audio.wav"), self.model)
        with (
            patch.object(self.player, "audio_busy", return_value=False),
            patch(
                "runtime.single_character_performance.pygame.mixer.get_init",
                return_value=False,
            ),
            patch.object(self.player, "onStartCallback_emotion_version") as audio,
        ):
            self.player.update_playback(self.model)
            self.player.command(
                dict(type="cancel_turn", chat_id="a", turn_id="t"), self.model
            )
            callbacks[0]()
            audio.assert_not_called()
            self.player.command(self.segment(2), self.model)
            self.assertFalse(self.player.busy)

    def test_last_audio_retains_busy_until_device_finishes(self):
        self.player.command(self.segment(1, audio="valid.wav"), self.model)
        with (
            patch.object(self.player, "onStartCallback_emotion_version"),
            patch.object(self.player, "audio_busy", return_value=True),
            patch.object(self.player.wavHandler, "Update", return_value=False),
        ):
            self.player.update_playback(self.model)
            self.assertTrue(self.player.busy)
            self.assertEqual(self.events, [])
        with patch.object(self.player, "audio_busy", return_value=False):
            self.player.update_playback(self.model)
        self.assertFalse(self.player.busy)
        self.assertEqual(self.events[0]["type"], "playback_complete")


if __name__ == "__main__":
    unittest.main()
