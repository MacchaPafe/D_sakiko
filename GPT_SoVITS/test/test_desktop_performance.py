from __future__ import annotations

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

    def test_no_model_or_motion_still_completes_fifo_text(self) -> None:
        """没有模型或动作时，各段仍保留阅读时间并按顺序确认完成。"""
        self.player.command(self.segment(1), self.model)
        self.player.command(self.segment(2), self.model)
        with (patch.object(self.player, "audio_busy", return_value=False),
              patch("runtime.single_character_performance.time.monotonic", return_value=0.0) as clock):
            self.player.update_playback(self.model)
            self.assertEqual(self.events, [])
            clock.return_value = 5.9
            self.player.update_playback(self.model)
            self.assertEqual(self.subtitles, ["text1"])
            clock.return_value = 6.0
            self.player.update_playback(self.model)
            self.assertEqual([e["segment_id"] for e in self.events], [1])
            self.player.update_playback(self.model)
            clock.return_value = 12.0
            self.player.update_playback(self.model)
        self.assertEqual([e["segment_id"] for e in self.events], [1, 2])
        self.assertEqual(self.subtitles, ["text1", "text2"])
        self.assertFalse(self.player.busy)

    def test_farewell_waits_for_motion_callback_and_ignores_late_segments(self) -> None:
        """退出期间只播放告别动作，迟到的对话和思考不能覆盖它。"""
        self.model.StartRandomMotion.return_value = True
        self.player.command(self.segment(1), self.model)
        self.player.command({"type": "farewell"}, self.model)
        self.assertFalse(self.player.busy)
        self.assertEqual(self.events, [])
        call = self.model.StartRandomMotion.call_args
        self.assertEqual(call.args[:2], ("bye", 3))
        self.player.command(self.segment(2), self.model)
        self.player.command({"type": "thinking"}, self.model)
        self.assertFalse(self.player.busy)
        self.assertFalse(self.player.thinking)
        call.args[3]()
        call.args[3]()
        self.assertEqual(self.events, [{"type": "farewell_complete"}])

    def test_missing_farewell_motion_completes_immediately(self) -> None:
        """无告别动作或无模型时正常结束，不依赖不存在的回调。"""
        self.player.command({"type": "farewell"}, self.model)
        self.assertEqual(self.events, [{"type": "farewell_complete"}])

    def test_silent_placeholder_is_read_as_text_without_starting_audio(self) -> None:
        """没有语音模型时的静音占位文件也须保留阅读时间，且不驱动口型。"""
        self.player.command(self.segment(1, audio="../reference_audio/silent_audio/silence.wav"), self.model)
        with (patch.object(self.player, "onStartCallback_emotion_version") as audio,
              patch.object(self.player, "audio_busy", return_value=False)):
            self.player.update_playback(self.model)
        audio.assert_not_called()
        self.assertTrue(self.player.busy)
        self.model.set_parameter_value.assert_called_with("mouth_open_y", 0.0)

    def test_farewell_has_timeout_when_motion_never_finishes(self) -> None:
        """动作未回调时有超时回执，防止退出一直等待。"""
        self.model.StartRandomMotion.return_value = True
        with patch("runtime.single_character_performance.time.monotonic", return_value=10.0) as clock:
            self.player.command({"type": "farewell"}, self.model)
            clock.return_value = 17.9
            self.player.update_playback(self.model)
            self.assertEqual(self.events, [])
            clock.return_value = 18.0
            self.player.update_playback(self.model)
            self.assertEqual(self.events, [{"type": "farewell_complete"}])

    def test_cancel_text_only_segment_immediately_releases_playback(self) -> None:
        """阅读等待可取消，取消后不得补发完成事件。"""
        self.player.command(self.segment(1), self.model)
        self.player.update_playback(self.model)
        self.assertTrue(self.player.busy)
        self.player.command(dict(type="cancel_turn", chat_id="a", turn_id="t"), self.model)
        self.assertFalse(self.player.busy)
        self.player.update_playback(self.model)
        self.assertEqual(self.events, [])

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
