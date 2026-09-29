"""验证桌面普通窗口与桌宠共享的逐段同步、长语音动作和失败降级。"""

from itertools import permutations
from unittest import TestCase
from unittest.mock import Mock, patch

from runtime.single_character_performance import SingleCharacterPerformance


class SegmentSyncTests(TestCase):
    def setUp(self):
        """用可控的声音设备、动作回调和时钟模拟跨帧演出。"""
        self.player = SingleCharacterPerformance()
        self.model = Mock(version="v2")
        self.model.is_motion_finished.return_value = False
        self.motions, self.events, self.subtitles = [], [], []
        self.model.StartRandomMotion.side_effect = self.start_motion
        self.model.apply_performance.side_effect = self.apply_performance
        self.player.on_event = self.events.append
        self.player.on_subtitle = self.subtitles.append
        self.sound = False
        self.audio = Mock(side_effect=self.start_audio)
        for target, kwargs in (
            ("time.time", {"return_value": 100.0}),
            ("time.monotonic", {"return_value": 100.0}),
        ):
            context = patch("runtime.single_character_performance." + target, **kwargs)
            clock = context.start()
            self.addCleanup(context.stop)
            if target.endswith("monotonic"):
                self.clock = clock
            else:
                self.wall = clock
        for name, kwargs in (
            ("audio_busy", {"side_effect": lambda: self.sound}),
            ("onStartCallback_emotion_version", {"side_effect": self.audio}),
            ("_get_audio_duration_seconds", {"return_value": 0.0}),
        ):
            context = patch.object(self.player, name, **kwargs)
            context.start()
            self.addCleanup(context.stop)
        self.player.wavHandler.Update = Mock(return_value=False)

    def start_audio(self, path):
        """模拟音频开始后持续播放，直到测试主动停止。"""
        self.sound = True

    def start_motion(self, group, priority, on_start, on_finish, **kwargs):
        """存储原生动作回调，由测试模拟下一帧启动及结束。"""
        self.motions.append((on_start, on_finish))
        return True

    def apply_performance(self, selection, emotion, **kwargs):
        """V3 独立动作沿用同一可控回调，并验证每段允许重新播放。"""
        self.assertTrue(kwargs["restart_motion"])
        return self.start_motion("v3", 3, kwargs["on_start"], kwargs["on_finish"])

    def advance(self, value):
        """推进两种时钟并运行一帧。"""
        self.clock.return_value = self.wall.return_value = value
        self.player.update_playback(self.model)

    def segment(self, number, **kwargs):
        """构造包含真实段落身份的演出请求。"""
        return dict(type="play_segment", chat_id="chat", turn_id="turn", segment_id=number,
                    text=f"text{number}", translation="", emotion="LABEL_0",
                    audio_path="voice.wav", **kwargs)

    def test_start_and_finish_barriers_in_every_completion_order(self):
        """三项任意顺序完成均需等齐，下一段文字绝不因提前合成而出现。"""
        for version in ("v2", "v3"):
            for order in permutations(("audio", "motion", "text")):
                with self.subTest(version=version, order=order):
                    self.player.stop()
                    self.motions.clear()
                    self.events.clear()
                    self.subtitles.clear()
                    self.audio.reset_mock()
                    self.sound = False
                    self.model.version = version
                    first = self.segment(1, wait_for_text=True)
                    self.player.command(first, self.model)
                    self.player.command(self.segment(2, wait_for_text=True), self.model)
                    self.advance(100.0)
                    self.audio.assert_not_called()
                    self.assertEqual(self.subtitles, [])
                    self.assertEqual(self.events, [])
                    self.motions[0][0]()
                    self.assertTrue(self.sound)
                    self.assertEqual(self.subtitles, ["text1"])
                    self.assertEqual([e["type"] for e in self.events], ["playback_started"])
                    for index, channel in enumerate(order):
                        if channel == "audio":
                            self.sound = False
                        elif channel == "motion":
                            self.motions[0][1]()
                        else:
                            self.player.command(dict(first, type="segment_text_complete"), self.model)
                        self.advance(101.0)
                        self.assertEqual(len(self.motions), 1)
                        if index < 2:
                            self.assertEqual(self.player.active_segment["segment_id"], 1)
                    self.assertIsNone(self.player.active_segment)
                    self.advance(101.1)
                    self.assertEqual(len(self.motions), 2)
                    self.assertEqual(self.subtitles, ["text1"])
                    self.motions[1][0]()
                    self.assertEqual(self.subtitles, ["text1", "text2"])
                    self.motions[0][1]()
                    self.player.command(dict(first, type="segment_text_complete"), self.model)
                    self.assertFalse(self.player.segment_motion_complete)
                    self.assertFalse(self.player.segment_text_complete)

    def test_long_audio_repeats_twice_without_restarting_audio_or_text(self):
        """长语音保留原动作加两次追加，最后动作晚于声音结束时仍阻止下一段。"""
        for version in ("v2", "v3"):
            with self.subTest(version=version):
                self.player.stop()
                self.model.version = version
                self.motions.clear()
                self.events.clear()
                self.subtitles.clear()
                self.audio.reset_mock()
                self.sound = False
                self.player._get_audio_duration_seconds.return_value = 20.0
                self.player.command(self.segment(1), self.model)
                self.player.command(self.segment(2), self.model)
                self.advance(100.0)
                self.assertTrue(self.player.long_audio_motion_active)
                self.motions[0][0]()
                self.motions[0][1]()
                self.advance(101.0)
                self.advance(103.4)
                self.assertEqual(len(self.motions), 1)
                self.advance(103.5)
                self.assertEqual(len(self.motions), 2)
                self.motions[1][0]()
                self.motions[1][1]()
                self.advance(104.0)
                self.advance(106.5)
                self.assertEqual(len(self.motions), 3)
                self.motions[2][0]()
                self.audio.assert_called_once()
                self.assertEqual(self.subtitles, ["text1"])
                self.assertEqual([e["type"] for e in self.events], ["playback_started"])
                self.sound = False
                self.advance(107.0)
                self.assertEqual(self.player.active_segment["segment_id"], 1)
                self.motions[2][1]()
                self.advance(108.0)
                self.assertIsNone(self.player.active_segment)
                self.advance(108.1)
                self.assertEqual(self.player.active_segment["segment_id"], 2)

    def test_repeat_limit_and_cancel_ignore_late_callbacks(self):
        """两次追加后不再循环，取消后的旧动作不能重播声音或结束新段。"""
        self.player._get_audio_duration_seconds.return_value = 40.0
        self.player.command(self.segment(1), self.model)
        self.advance(100.0)
        for index, value in enumerate((101.0, 105.0, 109.0)):
            self.motions[index][0]()
            self.motions[index][1]()
            self.advance(value)
            self.advance(value + 2.5)
        self.assertEqual(len(self.motions), 3)
        self.advance(130.0)
        self.assertEqual(len(self.motions), 3)
        old = self.motions[-1]
        self.player.command(dict(type="cancel_turn", chat_id="chat", turn_id="turn"), self.model)
        self.sound = False
        self.player.command(dict(self.segment(1), turn_id="next"), self.model)
        self.advance(131.0)
        old[0]()
        old[1]()
        self.assertFalse(self.player.audio_started)
        self.assertFalse(self.player.segment_motion_complete)

    def test_missing_start_or_finish_callback_has_bounded_fallback(self):
        """动作漏发开始/结束回执仍能显示文字、播放声音并有界结束。"""
        self.player.command(self.segment(1), self.model)
        self.advance(100.0)
        self.advance(100.25)
        self.audio.assert_called_once()
        self.assertEqual(self.subtitles, ["text1"])
        self.sound = False
        self.advance(159.9)
        self.assertTrue(self.player.busy)
        self.advance(160.0)
        self.assertFalse(self.player.busy)
        self.assertEqual(self.events[-1]["type"], "playback_failed")

    def test_failed_audio_preserves_text_and_waits_for_motion(self):
        """声音失败留出阅读时间，动作仍未结束时不提前推进。"""
        self.audio.side_effect = lambda path: None
        self.player.command(self.segment(1), self.model)
        self.advance(100.0)
        self.motions[0][0]()
        self.assertTrue(self.player.audio_failed)
        self.advance(110.0)
        self.assertTrue(self.player.busy)
        self.motions[0][1]()
        self.advance(110.1)
        self.assertFalse(self.player.busy)
        self.assertEqual(self.events[-1]["type"], "playback_failed")
        self.assertEqual(self.subtitles, ["text1"])

    def test_model_replacement_releases_old_motion_but_preserves_audio(self):
        """模型失效/换装后旧动作等待被释放，当前声音继续完成。"""
        self.player.command(self.segment(1), self.model)
        self.advance(100.0)
        self.motions[0][0]()
        replacement = Mock(version="v2")
        self.player.update_playback(replacement)
        self.assertTrue(self.player.busy)
        self.assertTrue(self.player.segment_motion_complete)
        self.sound = False
        self.player.update_playback(replacement)
        self.assertFalse(self.player.busy)

    def test_silent_replay_waits_for_both_reading_and_motion(self):
        """无声回放在动作开始时显示，动作与阅读时间任一未完成都不能结束。"""
        for version in ("v2", "v3"):
            for motion_first in (True, False):
                with self.subTest(version=version, motion_first=motion_first):
                    self.player.stop()
                    self.model.version = version
                    self.motions.clear()
                    self.subtitles.clear()
                    self.events.clear()
                    self.player.command(dict(self.segment(1), audio_path="NO_AUDIO", wait_for_text=False), self.model)
                    self.advance(100.0)
                    self.assertEqual(self.subtitles, [])
                    self.motions[0][0]()
                    self.assertEqual(self.subtitles, ["text1"])
                    if motion_first:
                        self.motions[0][1]()
                        self.advance(105.9)
                    else:
                        self.advance(106.0)
                    self.assertTrue(self.player.busy)
                    if not motion_first:
                        self.motions[0][1]()
                    self.advance(106.0)
                    self.assertFalse(self.player.busy)
                    self.assertEqual([e["type"] for e in self.events], ["playback_started", "playback_complete"])
                    self.audio.assert_not_called()
