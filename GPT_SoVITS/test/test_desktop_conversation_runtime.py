from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading
import unittest
from PyQt5.QtWidgets import QApplication
from runtime.conversation import ConversationRuntime
from runtime.drafts import DraftStore


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.chat = SimpleNamespace(
            message_list=[SimpleNamespace(audio_path="", translation="")]
        )
        self.manager = Mock()
        self.manager.get_chat_by_id.return_value = self.chat
        self.engine = SimpleNamespace(
            chat_manager=self.manager,
            request_cancel_turn=Mock(),
            if_generate_audio=True,
            sakiko_state=False,
            audio_language_choice="中文",
        )
        self.audio = Mock()
        self.audio.generate_audio_for_character_sync.return_value = "voice.wav"
        self.events, self.commands, self.presentation = Queue(), Queue(), Queue()
        self.runtime = ConversationRuntime(
            self.engine,
            self.audio,
            [SimpleNamespace(character_name="爱音")],
            self.commands,
            self.events,
            self.presentation,
        )

    def payload(self, turn, count=1):
        return dict(
            chat_id="a",
            turn_id=turn,
            character_name="爱音",
            turn_complete=True,
            segments=[
                dict(
                    text=f"回复 {i}", translation="", emotion="LABEL_0", message_index=0
                )
                for i in range(count)
            ],
        )

    def test_late_commit_is_forwarded_without_unlocking_new_turn(self):
        previous = self.runtime.submit("a", "previous")
        self.runtime.cancel()
        current = self.runtime.submit("a", "current")
        event = dict(type="user_message_committed", chat_id="a", turn_id=previous)
        self.runtime.accept_event(event)
        self.assertEqual(self.events.get_nowait(), event)
        self.assertEqual(self.runtime.active, ("a", current))
        self.assertFalse(self.runtime._committed)

    def test_internal_reminder_waits_for_audio_and_original_chat(self):
        self.engine._normalize_input_command = lambda text: dict(
            type="send_message", chat_id="a"
        )
        self.runtime.input_queue.put("reminder")
        turn = self.runtime.submit("a", "message")
        self.runtime.process_response(self.payload(turn))
        self.assertIsNone(self.runtime.take_internal("a"))
        self.runtime.playback_event(
            dict(type="playback_complete", chat_id="a", turn_id=turn, segment_id=1)
        )
        self.assertIsNone(self.runtime.take_internal("b"))
        self.assertEqual(self.runtime.take_internal("a"), "reminder")
        self.assertIsNone(self.runtime.take_internal("a"))

    def test_headless_round_waits_for_all_playback_and_freezes_snapshot(self):
        snapshot = {"nested": {"value": 1}}
        turn = self.runtime.submit("a", "hi", worldbook_snapshot=snapshot)
        snapshot["nested"]["value"] = 2
        self.assertEqual(
            self.commands.get()["worldbook_snapshot"]["nested"]["value"], 1
        )
        self.runtime.process_response(self.payload(turn, 2))
        self.assertTrue(self.runtime.busy)
        with self.assertRaises(RuntimeError):
            self.runtime.submit("b", "wrong")
        with self.assertRaises(RuntimeError):
            self.runtime.switch_chat("b")
        self.runtime.playback_event(
            dict(chat_id="a", turn_id=turn, segment_id=2, type="playback_complete")
        )
        self.assertTrue(self.runtime.busy)
        self.runtime.playback_event(
            dict(chat_id="a", turn_id=turn, segment_id=1, type="playback_complete")
        )
        self.assertFalse(self.runtime.busy)
        self.manager.save.assert_called_once()

    def test_cancel_discards_late_tts_and_acknowledgement(self):
        turn = self.runtime.submit("a", "first")
        entered, resume = threading.Event(), threading.Event()

        def generate(*args, **kwargs):
            entered.set()
            resume.wait(3)
            return "late.wav"

        self.audio.generate_audio_for_character_sync.side_effect = generate
        thread = threading.Thread(
            target=self.runtime.process_response, args=(self.payload(turn),)
        )
        thread.start()
        self.assertTrue(entered.wait(2))
        self.runtime.cancel()
        next_turn = self.runtime.submit("a", "next")
        resume.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.runtime.playback_event(
            dict(chat_id="a", turn_id=turn, segment_id=1, type="playback_complete")
        )
        self.assertEqual(self.runtime.active, ("a", next_turn))
        self.assertNotEqual(self.chat.message_list[0].audio_path, "late.wav")
        self.assertFalse(
            any(e.get("type") == "play_segment" for e in list(self.presentation.queue))
        )

    def test_two_concurrent_submissions_accept_only_one(self):
        barrier = threading.Barrier(3)
        accepted = []

        def submit():
            barrier.wait()
            try:
                accepted.append(self.runtime.submit("a", "hi"))
            except RuntimeError:
                pass

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for t in threads:
            t.start()
        barrier.wait()
        for t in threads:
            t.join()
        self.assertEqual(len(accepted), 1)

    def test_tts_failure_degrades_to_text_and_save_failure_reported(self):
        self.audio.generate_audio_for_character_sync.side_effect = RuntimeError("tts")
        self.manager.save.side_effect = OSError("disk")
        turn = self.runtime.submit("a", "hi")
        self.runtime.process_response(self.payload(turn))
        segments = [e for e in self.presentation.queue if e["type"] == "play_segment"]
        self.assertEqual(segments[0]["audio_path"], "NO_AUDIO")
        self.runtime.playback_event(
            dict(chat_id="a", turn_id=turn, segment_id=1, type="playback_complete")
        )
        self.assertFalse(self.runtime.busy)
        self.assertTrue(any(e.get("status") == "error" for e in self.events.queue))

    def test_replay_replacement_ignores_stale_completion(self) -> None:
        """连续切换和原句重播均替换旧轮次，迟到回执不能结束最新回放。"""
        first = self.runtime.replay("a", "first.wav", "LABEL_0")
        second = self.runtime.replay("a", "second.wav", "LABEL_1")
        latest = self.runtime.replay("a", "second.wav", "LABEL_1")
        self.assertEqual(len({first, second, latest}), 3)
        self.assertTrue(self.runtime.is_replaying)
        self.assertEqual(
            [event["type"] for event in self.presentation.queue],
            ["play_segment", "cancel_turn", "play_segment", "cancel_turn", "play_segment"],
        )
        for turn in (first, second):
            self.runtime.playback_event(dict(
                type="playback_complete", chat_id="a", turn_id=turn, segment_id=1,
            ))
        self.assertEqual(self.runtime.active, ("a", latest))
        self.manager.save.assert_not_called()
        self.runtime.playback_event(dict(
            type="playback_complete", chat_id="a", turn_id=latest, segment_id=1,
        ))
        self.assertFalse(self.runtime.busy)
        self.assertFalse(self.runtime.is_replaying)
        self.manager.save.assert_called_once()

    def test_replay_cannot_replace_generation_or_its_pending_playback(self) -> None:
        """生成期间和生成已结束但仍待播时，都不能用历史回放打断新回复。"""
        self.runtime.replay("a", "old.wav", "LABEL_0")
        self.runtime.cancel()
        turn = self.runtime.submit("a", "新回复")
        self.assertFalse(self.runtime.is_replaying)
        with self.assertRaises(RuntimeError):
            self.runtime.replay("a", "old.wav", "LABEL_0")
        self.runtime.process_response(self.payload(turn))
        with self.assertRaises(RuntimeError):
            self.runtime.replay("a", "old.wav", "LABEL_0")
        self.assertEqual(self.runtime.active, ("a", turn))

    def test_switch_chat_stops_voiced_and_silent_replay_and_ignores_old_events(self) -> None:
        """切换会停止声音或无声阅读、清空字幕，旧回执不能结束新对话轮次。"""
        from runtime.single_character_performance import SingleCharacterPerformance

        for audio_path in ("voice.wav", "NO_AUDIO"):
            with self.subTest(audio_path=audio_path):
                player = SingleCharacterPerformance()
                model = Mock()
                model.StartRandomMotion.return_value = False
                subtitle = Mock()
                player.on_subtitle = subtitle
                player.on_event = self.runtime.playback_event
                old_turn = self.runtime.replay("a", audio_path, "LABEL_0", "旧字幕")
                player.command(self.presentation.get_nowait(), model)
                with patch.object(player, "audio_busy", return_value=audio_path != "NO_AUDIO"), patch.object(
                    player, "onStartCallback_emotion_version"
                ), patch.object(player.wavHandler, "Update", return_value=False), patch(
                    "runtime.single_character_performance.pygame.mixer.get_init", return_value=True
                ), patch("runtime.single_character_performance.pygame.mixer.music.stop") as stop_audio:
                    player.update_playback(model)
                    self.assertTrue(player.busy)
                    self.runtime.switch_chat("b")
                    self.assertFalse(self.runtime.busy)
                    player.command(self.presentation.get_nowait(), model)
                    self.assertFalse(player.busy)
                    subtitle.assert_called_with("")
                    stop_audio.assert_called_once()
                self.assertEqual(self.commands.get_nowait(), dict(type="switch_chat", chat_id="b"))
                new_turn = self.runtime.submit("b", "新对话")
                for event_type in ("playback_complete", "playback_failed"):
                    self.runtime.playback_event(dict(
                        type=event_type, chat_id="a", turn_id=old_turn, segment_id=1,
                    ))
                self.runtime.accept_event(dict(
                    type="assistant_turn_complete", chat_id="a", turn_id=old_turn,
                ))
                self.assertEqual(self.runtime.active, ("b", new_turn))
                self.assertTrue(self.events.empty())
                self.runtime.cancel()
                while not self.presentation.empty():
                    self.presentation.get_nowait()
                while not self.commands.empty():
                    self.commands.get_nowait()

    def test_switch_chat_rejects_generation_and_missing_target_without_cancelling(self) -> None:
        """不存在的目标不取消历史回放，生成中的新回复仍禁止切换。"""
        turn = self.runtime.replay("a", "NO_AUDIO", "LABEL_0")
        self.manager.get_chat_by_id.return_value = None
        with self.assertRaises(ValueError):
            self.runtime.switch_chat("missing")
        self.assertEqual(self.runtime.active, ("a", turn))
        self.engine.request_cancel_turn.assert_not_called()
        self.manager.get_chat_by_id.return_value = self.chat
        self.runtime.cancel()
        turn = self.runtime.submit("a", "生成中")
        with self.assertRaises(RuntimeError):
            self.runtime.switch_chat("b")
        self.assertEqual(self.runtime.active, ("a", turn))

    def test_qt_chat_switch_validates_target_then_cancels_replay_before_model_switch(self) -> None:
        """界面保留无效目标下的回放，成功切换先取消演出并清除旧轮次状态。"""
        from qtUI import ChatGUI

        host = Mock()
        host.current_chat_id = "a"
        host.conversation_runtime = self.runtime
        host.chat_manager = self.manager
        host.character_by_name = {"爱音": Mock()}
        host.current_character.character_name = "爱音"
        host.desktop_controller = None
        host.is_chat_busy.side_effect = lambda: self.runtime.busy
        host.sync_current_chat_to_backends.side_effect = lambda: ChatGUI.sync_current_chat_to_backends(host)
        host._clear_active_turn.side_effect = lambda: ChatGUI._clear_active_turn(host)
        host._send_live2d_switch.side_effect = lambda name, meta: self.presentation.put(
            dict(type="switch_live2d", character_name=name)
        )
        turn = self.runtime.replay("a", "NO_AUDIO", "LABEL_0")
        host.active_chat_id, host.active_turn_id = "a", turn
        host.active_turn_phase = "rendering"
        target = Mock(chat_id="b")
        target.get_character_name.return_value = "爱音"
        with patch("qtUI.QMessageBox.information") as notice:
            ChatGUI.switch_chat_by_id(host, "a")
            self.assertEqual(self.runtime.active, ("a", turn))
            self.manager.get_chat_by_id.return_value = None
            ChatGUI.switch_chat_by_id(host, "missing")
            self.assertEqual(self.runtime.active, ("a", turn))
            self.manager.get_chat_by_id.return_value = target
            target.get_character_name.return_value = "已删除的角色"
            ChatGUI.switch_chat_by_id(host, "b")
            self.assertEqual(self.runtime.active, ("a", turn))
            self.assertEqual(host.current_chat_id, "a")
            target.get_character_name.return_value = "爱音"
            notice.reset_mock()
            ChatGUI.switch_chat_by_id(host, "b")
            notice.assert_not_called()
            self.assertEqual(host.current_chat_id, "b")
            self.assertFalse(self.runtime.busy)
            self.assertIsNone(host.active_turn_id)
            self.assertIsNone(host.active_chat_id)
            host.draft_binding.switch.assert_called_once_with("b")
            host.apply_current_chat_ui_state.assert_called_once()
            self.assertEqual([event["type"] for event in self.presentation.queue],
                             ["play_segment", "cancel_turn", "switch_live2d"])
            self.assertEqual(self.commands.get_nowait(), dict(type="switch_chat", chat_id="b"))
            new_turn = self.runtime.submit("b", "新回复")
            ChatGUI.switch_chat_by_id(host, "a")
            self.assertEqual(host.current_chat_id, "b")
            self.assertEqual(self.runtime.active, ("b", new_turn))
            notice.assert_called_once()

    def test_closed_runtime_rejects_replay(self) -> None:
        """关闭运行时会取消回放并拒绝新的播放请求。"""
        self.runtime.replay("a", "old.wav", "LABEL_0")
        self.runtime.close()
        self.assertFalse(self.runtime.is_replaying)
        with self.assertRaises(RuntimeError):
            self.runtime.replay("a", "next.wav", "LABEL_0")

    def test_replay_replacement_stops_audio_and_clears_queued_segments(self) -> None:
        """真实演出队列在切换时停掉旧声音，只启动最后选中的历史音频。"""
        from runtime.single_character_performance import SingleCharacterPerformance

        player = SingleCharacterPerformance()
        model = Mock()
        model.StartRandomMotion.return_value = False
        player.on_event = self.runtime.playback_event
        with patch.object(player, "audio_busy", return_value=True), patch.object(
            player, "onStartCallback_emotion_version"
        ) as start_audio, patch(
            "runtime.single_character_performance.pygame.mixer.get_init", return_value=True
        ), patch("runtime.single_character_performance.pygame.mixer.music.stop") as stop_audio:
            self.runtime.replay("a", "first.wav", "LABEL_0")
            player.command(self.presentation.get_nowait(), model)
            player.update_playback(model)
            self.runtime.replay("a", "second.wav", "LABEL_1")
            latest = self.runtime.replay("a", "third.wav", "LABEL_2")
            while not self.presentation.empty():
                player.command(self.presentation.get_nowait(), model)
            player.update_playback(model)
        stop_audio.assert_called_once()
        self.assertEqual(
            [call.args[0] for call in start_audio.call_args_list],
            ["first.wav", "third.wav"],
        )
        self.assertEqual(player.active_segment["turn_id"], latest)
        self.assertFalse(player.pending)

    def test_history_click_replaces_busy_replay_but_invalid_message_keeps_it(self) -> None:
        """界面允许忙碌回放直接切换，无效链接或文件丢失不应停止旧回放。"""
        from PyQt5.QtCore import QUrl
        from qtUI import ChatGUI

        host = Mock()
        host.conversation_runtime = self.runtime
        host.current_chat_id = "a"
        host.current_chat = self.chat
        host.motion_complete_value.value = False
        host.is_response_active.side_effect = lambda: self.runtime.busy
        self.chat.message_list[0].text = "历史正文"
        self.chat.message_list[0].character_name = "爱音"
        self.chat.message_list[0].performance = None
        first = self.runtime.replay("a", "first.wav", "LABEL_0")
        with patch("qtUI.os.path.isfile", return_value=False):
            for link in ("no-audio:", "no-audio:?msg=-1", "no-audio:?msg=99", "no-audio:?msg=bad", "user:?msg=0", "missing.wav[LABEL_0]?msg=0"):
                ChatGUI.play_history_audio(host, QUrl(link))
                self.assertEqual(self.runtime.active, ("a", first))
        with patch("qtUI.os.path.isfile", return_value=True):
            ChatGUI.play_history_audio(host, QUrl("second.wav[LABEL_1]?msg=0"))
        self.assertNotEqual(self.runtime.active, ("a", first))
        host._start_active_turn.assert_called_once_with("a", self.runtime.active[1], "rendering")
        self.assertEqual(list(self.presentation.queue)[-1]["audio_path"], "second.wav")
        self.runtime.cancel()
        turn = self.runtime.submit("a", "新的回复")
        host.motion_complete_value.value = True
        ChatGUI.play_history_audio(host, QUrl("second.wav[LABEL_1]?msg=0"))
        self.assertEqual(self.runtime.active, ("a", turn))
        host.setWindowTitle.assert_called_with("请等待当前过程完成后重试...")

    def test_silent_history_replays_selected_message_and_finishes_without_audio(self) -> None:
        """空路径、无音频标记和静音占位均按消息索引重播文字及演出并正常结束。"""
        from PyQt5.QtCore import QUrl
        from chat.chat import Message
        from qtUI import ChatGUI
        from runtime.single_character_performance import SingleCharacterPerformance
        from ui_main.components.chat_display import ChatDisplay

        for path in ("", "NO_AUDIO", "/missing/silent_audio/silence.wav"):
            with self.subTest(path=path):
                self.chat.message_list = [Message.from_dict({
                    "character_name": "爱音", "text": text, "translation": "translation",
                    "audio_path": path, "emotion": "like",
                    "performance": {"motion": "nod", "expression": "smile"},
                }) for text in ("不要选中前一条", "选中的无声消息")]
                host = Mock()
                host.conversation_runtime = self.runtime
                host.current_chat_id = "a"
                host.current_chat = self.chat
                host.motion_complete_value.value = True
                host.is_response_active.side_effect = lambda: self.runtime.busy
                display = Mock()
                display._is_user_message.side_effect = ChatDisplay._is_user_message
                link = ChatDisplay._message_anchor_href(display, self.chat.message_list[1], 1)
                ChatGUI.play_history_audio(host, QUrl(link))
                segment = self.presentation.get_nowait()
                self.assertEqual(segment["audio_path"], "NO_AUDIO")
                self.assertEqual(segment["text"], "选中的无声消息")
                self.assertEqual(segment["translation"], "translation")
                self.assertEqual(segment["emotion"], "LABEL_4")
                self.assertEqual(segment["performance"], {"motion": "nod", "expression": "smile"})

                player = SingleCharacterPerformance()
                player.on_event = self.runtime.playback_event
                subtitle = Mock()
                player.on_subtitle = subtitle
                model = Mock()
                model.version = "v3"
                player.command(segment, model)
                with patch.object(player, "audio_busy", return_value=False), patch.object(
                    player, "onStartCallback_emotion_version"
                ) as audio, patch(
                    "runtime.single_character_performance.time.monotonic", return_value=0.0
                ) as clock:
                    player.update_playback(model)
                    subtitle.assert_called_with("选中的无声消息\ntranslation")
                    self.assertTrue(self.runtime.is_replaying)
                    model.apply_performance.assert_called_once()
                    # V3 无声演出保留表情自身的嘴形，不驱动语音口型。
                    model.set_parameter_value.assert_not_called()
                    clock.return_value = 6.0
                    player.update_playback(model)
                    audio.assert_not_called()
                self.assertFalse(self.runtime.busy)
                self.assertEqual(self.presentation.get_nowait()["type"], "generation_finished")
        self.audio.generate_audio_for_character_sync.assert_not_called()
        self.assertTrue(self.commands.empty())

    def test_silent_and_voiced_history_can_replace_each_other(self) -> None:
        """无声回放支持原句重播及与有声消息双向切换，仍保护正在生成的新回复。"""
        from PyQt5.QtCore import QUrl
        from chat.chat import Message
        from qtUI import ChatGUI

        self.chat.message_list = [Message.from_dict({
            "character_name": "爱音", "text": "无声消息", "audio_path": "NO_AUDIO",
        })]
        host = Mock()
        host.conversation_runtime = self.runtime
        host.current_chat_id = "a"
        host.current_chat = self.chat
        host.motion_complete_value.value = False
        host.is_response_active.side_effect = lambda: self.runtime.busy
        turn = self.runtime.replay("a", "voice.wav", "LABEL_0")
        with patch("qtUI.os.path.isfile", return_value=True):
            for link, path in (("no-audio:?msg=0", "NO_AUDIO"),
                               ("no-audio:?msg=0", "NO_AUDIO"),
                               ("voice.wav[LABEL_0]?msg=0", "voice.wav")):
                ChatGUI.play_history_audio(host, QUrl(link))
                self.assertNotEqual(self.runtime.active[1], turn)
                turn = self.runtime.active[1]
                self.assertEqual(list(self.presentation.queue)[-1]["audio_path"], path)
        self.runtime.cancel()
        turn = self.runtime.submit("a", "新回复")
        host.motion_complete_value.value = True
        ChatGUI.play_history_audio(host, QUrl("no-audio:?msg=0"))
        self.assertEqual(self.runtime.active, ("a", turn))
        host.setWindowTitle.assert_called_with("请等待当前过程完成后重试...")


class DraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_commit_does_not_erase_later_edit_or_new_attachment(self):
        store = DraftStore()
        store.set("a", "first", [{"draft_attachment_id": "one"}])
        store.submitted("a", "turn")
        store.set(
            "a",
            "next",
            [{"draft_attachment_id": "one"}, {"draft_attachment_id": "two"}],
        )
        store.committed("turn")
        self.assertEqual(store.get("a").text, "next")
        self.assertEqual(store.get("a").images, [{"draft_attachment_id": "two"}])

    def test_asr_stays_in_original_chat_and_deleted_chat_not_restored(self):
        store = DraftStore()
        store.set("a", "one", [])
        rev = store.get("a").revision
        store.set("a", "edited", [])
        store.set("b", "other", [])
        store.insert_recognition("a", rev, 0, " voice")
        self.assertEqual(store.get("a").text, "edited voice")
        self.assertEqual(store.get("b").text, "other")
        store.delete("a")
        store.insert_recognition("a", rev, 0, "late")
        self.assertEqual(store.get("a").text, "")

    def test_removed_attachment_ignores_upload_completion(self):
        store = DraftStore()
        store.set("a", "text", [{"draft_attachment_id": "one"}])
        store.set("a", "text", [])
        store.update_attachment({"draft_attachment_id": "one", "upload_state": "ready"})
        self.assertEqual(store.get("a").images, [])


if __name__ == "__main__":
    unittest.main()
