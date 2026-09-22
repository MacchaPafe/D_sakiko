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
