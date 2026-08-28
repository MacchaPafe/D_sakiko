from __future__ import annotations
import os, sys, unittest
from random import Random
from queue import Queue
from threading import Event, Thread
import time
root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); sys.path.insert(0, root) if root not in sys.path else None
from live2d_support.renderer_host import SharedRendererHost
from live2d_support.renderer_host import SharedRendererService
from live2d_support.shared_behavior import SharedLive2DBehavior
from live2d_support.behavior_scheduler import SharedBehaviorScheduler
from live2d_support.authoritative_owner import AuthoritativeLive2DOwner

class RendererHostTest(unittest.TestCase):
    def setUp(self):
        self.out = []; self.host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"motion_groups":{"happiness":1}}})
    def test_audio_emits_only_after_matching_motion_start(self):
        self.assertTrue(self.host.start_emotion_segment(turn_id="t",segment_id="s",emotion="LABEL_0",audio_path="a.wav"))
        self.assertEqual(self.out[0]["type"], "play_motion"); token = self.out[0]["data"]["token"]
        self.assertTrue(self.host.handle_renderer_fact({"type":"motion_started","data":{"token":token}}))
        self.assertEqual(self.out[1]["type"], "play_audio"); self.assertEqual(self.out[1]["data"]["path"], "a.wav")

    def test_ready_catalog_resolves_expression_outside_renderer(self):
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"motion_files_by_group":{"happiness":["happiness_smile.mtn"]},"expression_ids":["exp_smile01"]}})
        self.host.start_emotion_segment(turn_id="t",segment_id="s",emotion="LABEL_0",audio_path="a.wav")
        self.assertEqual(self.out[-1]["data"]["expression_id"], "exp_smile01")
    def test_command_failure_is_consumed(self):
        self.host.start_emotion_segment(turn_id="t",segment_id="s",emotion="LABEL_0",audio_path="a.wav")
        token = self.out[0]["data"]["token"]
        self.assertTrue(self.host.handle_renderer_fact({"type":"command_failed","data":{"token":token,"phase":"audio_start"}}))

    def test_raw_motion_start_failure_still_runs_master_audio_fallback(self):
        self.host.start_emotion_segment(turn_id="t",segment_id="s",emotion="LABEL_0",audio_path="a.wav")
        token = self.out[0]["data"]["token"]
        self.assertTrue(self.host.handle_renderer_fact({"type":"command_failed","data":{"token":token,"phase":"motion_start"}}))
        self.assertEqual(self.out[-1]["type"], "play_audio")
        self.assertEqual(self.out[-1]["data"]["path"], "a.wav")

    def test_service_turns_queue_intent_and_fact_into_renderer_commands(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        facts.put({"type":"renderer_ready","data":{"motion_groups":{"happiness":1}}})
        intents.put({"type":"emotion_segment","data":{"turn_id":"t","segment_id":"s","emotion":"LABEL_0","audio_path":"a.wav"}})
        self.assertEqual(service.run_once(), 2)
        motion = commands.get_nowait(); self.assertEqual(motion["type"], "play_motion")
        facts.put({"type":"motion_started","data":{"token":motion["data"]["token"]}})
        self.assertEqual(service.run_once(), 1); self.assertEqual(commands.get_nowait()["type"], "play_audio")

    def test_service_trace_records_exact_command_and_raw_runtime_facts(self):
        intents, facts, commands, trace = Queue(), Queue(), Queue(), []
        service = SharedRendererService(
            intents, facts, commands, AuthoritativeLive2DOwner(), trace=trace.append,
        )
        facts.put({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "motion_groups": {"happiness": 1},
        }})
        intents.put({"type": "emotion_segment", "data": {
            "turn_id": "t", "segment_id": "s", "emotion": "LABEL_0", "audio_path": "a.wav",
        }})
        self.assertEqual(service.run_once(), 2)
        motion = commands.get_nowait()
        token = motion["data"]["token"]
        facts.put({"type": "motion_started", "data": {
            "renderer_id": "pygame", "token": token,
        }})
        service.run_once()

        command_events = [event for event in trace if event["kind"] == "command"]
        fact_events = [event for event in trace if event["kind"] == "fact"]
        self.assertEqual(command_events[0]["message"], motion)
        self.assertEqual(fact_events[-1]["message"]["data"]["token"], token)
        self.assertEqual(fact_events[-1]["message"]["data"]["renderer_id"], "pygame")

    def test_service_defers_legacy_intent_until_renderer_capabilities_arrive(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        intents.put({"type":"emotion_segment","data":{"turn_id":"t","segment_id":"s","emotion":"LABEL_0","audio_path":"a.wav"}})
        self.assertEqual(service.run_once(), 0)
        self.assertTrue(commands.empty())
        facts.put({"type":"renderer_ready","data":{"motion_groups":{"happiness":1}}})
        self.assertEqual(service.run_once(), 2)
        self.assertEqual(commands.get_nowait()["type"], "play_motion")

    def test_service_rejects_pending_segment_after_only_renderer_becomes_unavailable(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        owner = AuthoritativeLive2DOwner()
        service = SharedRendererService(intents, facts, commands, owner)
        facts.put({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one",
            "renderer_role": "pygame", "motion_groups": {"happiness": 1},
        }})
        facts.put({"type": "renderer_unavailable", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one",
            "reason": "live2d_model_unavailable",
        }})
        intents.put({"type": "emotion_segment", "data": {
            "turn_id": "t", "segment_id": "s", "emotion": "LABEL_0", "audio_path": "a.wav",
        }})
        self.assertEqual(service.run_once(), 3)
        self.assertIsNone(owner.behavior.active_command)
        self.assertTrue(commands.empty())

    def test_empty_motion_catalog_still_plays_audio_without_local_fallback_decision(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame",
            "motion_files_by_group": {}, "capabilities": {"motion": False, "audio": True},
        }})
        self.assertTrue(host.start_emotion_segment(
            turn_id="t", segment_id="s", emotion="LABEL_0", audio_path="a.wav",
        ))
        self.assertEqual(self.out[-1]["type"], "play_audio")
        self.assertEqual(self.out[-1]["data"]["target_renderer_id"], "pygame")

    def test_service_worker_stops_under_caller_lifecycle_control(self):
        service = SharedRendererService(Queue(), Queue(), Queue(), AuthoritativeLive2DOwner()); stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True); worker.start()
        time.sleep(0.03); stop.set(); worker.join(0.5)
        self.assertFalse(worker.is_alive())

    def test_service_drains_shutdown_bye_before_stopping(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        facts.put({"type":"renderer_ready","data":{"motion_groups":{"bye":1}}})
        intents.put({"type":"bye","data":{}})
        stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True)
        worker.start(); time.sleep(0.03); stop.set(); worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(commands.get_nowait()["type"], "play_motion")

    def test_service_acknowledges_bye_before_stop_without_sleep(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        facts.put({"type":"renderer_ready","data":{"motion_groups":{"bye":1}}})
        stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True)
        worker.start()
        intents.put({"type":"bye","data":{}})
        self.assertTrue(service.wait_for_bye(0.5))
        stop.set(); worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(commands.get_nowait()["type"], "play_motion")

    def test_service_keeps_bye_motion_alive_until_completion(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        facts.put({"type": "renderer_ready", "data": {"motion_groups": {"bye": 1}}})
        intents.put({"type": "bye", "data": {}})
        stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True)
        worker.start()
        self.assertTrue(service.wait_for_bye(0.5))
        bye = commands.get(timeout=0.5)
        self.assertEqual(bye["type"], "play_motion")
        token = bye["data"]["token"]
        self.assertFalse(service.wait_for_bye_completion(0.01))
        facts.put({"type": "motion_finished", "data": {"token": token}})
        self.assertTrue(service.wait_for_bye_completion(0.5))
        stop.set()
        worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(commands.get(timeout=0.5)["type"], "close_renderer")
        self.assertTrue(service.wait_for_bye_completion(0.1))

    def test_service_consumes_bye_when_renderer_never_becomes_ready(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True)
        worker.start()
        intents.put({"type":"bye","data":{}})
        self.assertTrue(service.wait_for_bye(0.5))
        stop.set(); worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(commands.get_nowait(), {"type": "close_renderer", "data": {"reason": "bye_motion_unavailable"}})

    def test_shutdown_control_overtakes_deferred_emotion_when_not_ready(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True)
        worker.start()
        intents.put({"type":"emotion_segment","data":{"turn_id":"t","segment_id":"s","emotion":"LABEL_0","audio_path":"a.wav"}})
        intents.put({"type":"bye","data":{}})
        self.assertTrue(service.wait_for_bye(0.5))
        stop.set(); worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(commands.get_nowait()["type"], "close_renderer")

    def test_service_processes_only_one_emotion_per_owner_cycle(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner(rng=Random(0)))
        facts.put({"type": "renderer_ready", "data": {"motion_groups": {"happiness": 1}}})
        for segment in ("one", "two"):
            intents.put({"type": "emotion_segment", "data": {
                "turn_id": "turn", "segment_id": segment, "emotion": "LABEL_0", "audio_path": f"{segment}.wav",
            }})
        service.run_once()
        self.assertEqual(commands.get_nowait()["type"], "play_motion")
        self.assertTrue(service._pending_intents)
        service.run_once()
        self.assertEqual(commands.get_nowait()["type"], "play_motion")

    def test_service_defers_emotion_until_switch_ready_commits_new_catalog(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner(rng=Random(0)))
        facts.put({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one", "renderer_role": "pygame",
            "model_token": "old", "motion_groups": {"sadness": 1},
        }})
        service.run_once()
        intents.put({"type": "runtime_control", "data": {
            "type": "switch_live2d", "character_name": "爱音",
            "character_folder_name": "anon", "model_json": "anon.model.json",
            "model_token": "new",
        }})
        intents.put({"type": "emotion_segment", "data": {
            "emotion": "LABEL_0", "audio_path": "voice.wav",
        }})
        service.run_once()
        self.assertEqual(commands.get_nowait()["type"], "switch_live2d")
        self.assertTrue(service._pending_intents)
        facts.put({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one", "renderer_role": "pygame",
            "model_token": "new", "motion_groups": {"happiness": 1},
        }})
        service.run_once()
        self.assertEqual(commands.get_nowait()["type"], "play_motion")

    def test_switch_resets_long_audio_before_new_model_ready(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        owner = AuthoritativeLive2DOwner(rng=Random(0))
        service = SharedRendererService(intents, facts, commands, owner)
        facts.put({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one",
            "renderer_role": "pygame", "model_key": "sakiko", "runtime_version": "v2", "model_token": "old",
            "motion_groups": {"happiness": 1, "change_character": 1},
        }})
        service.run_once()
        intents.put({"type": "emotion_segment", "data": {
            "emotion": "LABEL_0", "audio_path": "long.wav",
            "audio_duration_seconds": 6.0,
        }})
        service.run_once()
        motion = commands.get_nowait()
        facts.put({"type": "motion_finished", "data": {"renderer_id": "pygame", "token": motion["data"]["token"]}})
        facts.put({"type": "audio_started", "data": {"renderer_id": "pygame", "token": motion["data"]["token"]}})
        service.run_once()
        intents.put({"type": "runtime_control", "data": {
            "type": "switch_live2d", "character_folder_name": "anon",
            "model_json": "anon.model.json", "model_token": "new",
        }})
        service.run_once()
        self.assertEqual(commands.get_nowait()["type"], "play_audio")
        self.assertEqual(commands.get_nowait()["type"], "switch_live2d")
        facts.put({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one",
            "renderer_role": "pygame", "model_token": "new",
            "motion_groups": {"happiness": 1, "change_character": 1},
        }})
        service.run_once()
        self.assertFalse(any(c.get("data", {}).get("purpose") == "long_audio_repeat" for c in list(commands.queue)))

    def test_conversion_resets_long_audio_and_rejects_null_runtime(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "motion_groups": {"happiness": 1},
        }})
        host.start_emotion_segment(turn_id="t", segment_id="s", emotion="LABEL_0", audio_path="long.wav", audio_duration_seconds=6.0)
        host.handle_runtime_control({"type": "switch_live2d", "character_folder_name": "anon", "model_json": "anon.model.json"})
        self.assertIsNone(host._scheduler.long_audio_due())
        null_host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        null_host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "capabilities": {"motion": False, "audio": True},
        }})
        self.assertFalse(null_host.start_sakiko_conversion(True, {"black": "black.model.json"}))

    def test_conversion_state_commits_at_model_barrier_before_motion(self):
        committed = []
        host = SharedRendererHost(
            self.out.append,
            AuthoritativeLive2DOwner(rng=Random(0)),
            conversion_state_callback=lambda black, mask: committed.append((black, mask)),
        )
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "motion_groups": {"change_character": 1},
        }})
        self.assertTrue(host.start_sakiko_conversion(False, {"white": "white.model.json"}))
        self.assertTrue(host._sakiko_conversion.is_black)
        switch = self.out[-1]
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "model_token": switch["data"]["model_token"],
            "motion_groups": {"change_character": 1, "change_character_maskoff": 1},
        }})
        self.assertFalse(host._sakiko_conversion.is_black)
        self.assertEqual(committed, [(False, True)])
        motion = self.out[-1]
        host.handle_renderer_fact({"type": "motion_rejected", "data": {"renderer_id": "pygame", "token": motion["data"]["token"]}})
        self.assertEqual(committed, [(False, True)])

    def test_stale_ready_does_not_replace_pending_conversion_catalog(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "model_token": "old", "motion_groups": {"change_character": 1},
        }})
        self.assertTrue(host.start_sakiko_conversion(False, {"white": "white.model.json"}))
        token = host._pending_conversion_model_token
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "model_token": "stale", "motion_groups": {"happiness": 9},
        }})
        self.assertEqual(host._renderer_catalogs["pygame"][1], {"change_character": 1})
        self.assertEqual(host._pending_conversion_model_token, token)

    def test_upstream_phase_trace_timed_idle_then_conversion(self):
        clock = type("Clock", (), {"value": 0.0, "__call__": lambda self: self.value})()
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(
            intents, facts, commands,
            AuthoritativeLive2DOwner(clock=clock, rng=Random(0)),
        )
        facts.put({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "model_token": "old",
            "model_urls": {"white": "white.model.json"},
            "motion_groups": {"IDLE": 1, "change_character": 1},
        }})
        service.run_once()
        clock.value = 25.0
        intents.put({"type": "sakiko_conversion", "data": {"value": False, "model_urls": {"white": "white.model.json"}}})
        service.run_once()
        self.assertEqual(
            [commands.get_nowait()["type"], commands.get_nowait()["type"]],
            ["play_motion", "switch_live2d"],
        )

    def test_upstream_phase_trace_thinking_due_before_conversion(self):
        clock = type("Clock", (), {"value": 0.0, "__call__": lambda self: self.value})()
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(
            intents, facts, commands,
            AuthoritativeLive2DOwner(clock=clock, rng=Random(0)),
        )
        facts.put({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "model_token": "old",
            "model_urls": {"white": "white.model.json"},
            "motion_groups": {"text_generating": 1, "change_character": 1},
        }})
        service.run_once()
        intents.put({"type": "thinking_changed", "data": {"active": True}})
        service.run_once()
        self.assertEqual(commands.get_nowait()["type"], "thinking_changed")
        clock.value = 1.0
        intents.put({"type": "sakiko_conversion", "data": {"value": False, "model_urls": {"white": "white.model.json"}}})
        service.run_once()
        self.assertEqual(
            [commands.get_nowait()["type"], commands.get_nowait()["type"]],
            ["play_motion", "switch_live2d"],
        )

    def test_service_preserves_owner_queue_order_without_global_overtaking(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner(rng=Random(0)))
        facts.put({"type": "renderer_ready", "data": {"motion_groups": {"happiness": 1}}})
        intents.put({"type": "emotion_segment", "data": {"emotion": "LABEL_0", "audio_path": "old.wav"}})
        intents.put({"type": "runtime_control", "data": {"type": "cancel_turn"}})
        service.run_once()
        self.assertEqual(commands.get_nowait()["type"], "play_motion")
        service.run_once()
        self.assertTrue(any(command.get("type") == "stop_motion" for command in list(commands.queue)))

    def test_cancel_drops_emotion_backlog_already_in_owner_queue(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner(rng=Random(0)))
        facts.put({"type": "renderer_ready", "data": {"motion_groups": {"happiness": 1}}})
        intents.put({"type": "emotion_segment", "data": {"emotion": "LABEL_0", "audio_path": "old.wav"}})
        intents.put({"type": "runtime_control", "data": {"type": "cancel_turn"}})
        service.run_once()
        self.assertEqual(commands.get_nowait()["type"], "play_motion")

    def test_no_model_ready_fact_preserves_audio_fallback(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "anon",
            "motion_files_by_group": {}, "capabilities": {"motion": False, "audio": True},
        }})
        self.assertTrue(host.ready)
        self.assertTrue(host.start_emotion_segment(turn_id="t", segment_id="s", emotion="LABEL_0", audio_path="a.wav"))
        self.assertEqual(self.out[-1]["type"], "play_audio")

    def test_sakiko_conversion_requires_sakiko_v2_runtime(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "anon",
            "runtime_version": "v2", "motion_groups": {"change_character": 1},
        }})
        self.assertFalse(host.start_sakiko_conversion(True, {"black": "black.model.json"}))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v3", "motion_groups": {"change_character": 1},
        }})
        self.assertFalse(host.start_sakiko_conversion(True, {"black": "black.model.json"}))

    def test_sakiko_conversion_gate_applies_to_audio_only_runtime(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "anon",
            "motion_files_by_group": {}, "capabilities": {"motion": False, "audio": True},
        }})
        self.assertFalse(host.start_sakiko_conversion(True, {"black": "black.model.json"}))

    def test_newer_conversion_wins_over_late_old_ready(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "model_token": "old",
            "motion_groups": {"change_character": 1},
        }})
        self.assertTrue(host.start_sakiko_conversion(False, {"white": "white.model.json"}))
        old_token = host._pending_conversion_model_token
        self.assertTrue(host.start_sakiko_conversion(True, {"black": "black.model.json"}))
        new_token = host._pending_conversion_model_token
        self.assertNotEqual(old_token, new_token)
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "model_token": old_token,
            "motion_groups": {"change_character": 1},
        }})
        self.assertEqual(host._pending_conversion_model_token, new_token)
        self.assertTrue(host._pending_conversion is not None)

    def test_conversion_toggle_uses_pending_authoritative_target(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "model_token": "old",
            "motion_groups": {"change_character": 1},
        }})
        self.assertTrue(host.start_sakiko_conversion("toggle", {"white": "white.model.json"}))
        first_token = host._pending_conversion_model_token
        self.assertFalse(host._pending_conversion.resulting_is_black)
        self.assertTrue(host.start_sakiko_conversion("toggle", {"black": "black.model.json"}))
        self.assertNotEqual(first_token, host._pending_conversion_model_token)
        self.assertTrue(host._pending_conversion.resulting_is_black)
        self.assertEqual(host._pending_conversion_switch["model_url"], "black.model.json")

    def test_conversion_commits_when_presentation_motion_is_missing(self):
        committed = []
        host = SharedRendererHost(
            self.out.append, AuthoritativeLive2DOwner(rng=Random(0)),
            conversion_state_callback=lambda black, mask: committed.append((black, mask)),
        )
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "model_token": "old", "motion_groups": {"change_character": 1},
        }})
        self.assertTrue(host.start_sakiko_conversion(False, {"white": "white.model.json"}))
        switch_token = host._pending_conversion_model_token
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "runtime_version": "v2", "model_token": switch_token, "motion_groups": {},
            "capabilities": {"motion": False, "audio": True},
        }})
        self.assertEqual(committed, [(False, True)])
        self.assertFalse(any(m.get("type") == "play_motion" for m in self.out))

    def test_bye_closes_only_after_matching_motion_finished(self):
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"motion_groups":{"bye":1}}})
        self.assertTrue(self.host.start_bye()); token=self.out[-1]["data"]["token"]
        self.host.handle_renderer_fact({"type":"motion_finished","data":{"token":"stale"}}); self.assertEqual(self.out[-1]["type"],"play_motion")
        self.host.handle_renderer_fact({"type":"motion_finished","data":{"token":token}}); self.assertEqual(self.out[-1]["type"],"close_renderer")
    def test_thinking_fact_is_displayed_but_timer_stays_in_shared_scheduler(self):
        clock = type("Clock", (), {"value": 0.0, "__call__": lambda self: self.value})()
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(clock=clock, rng=Random(0)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"motion_groups":{"text_generating":1}}})
        self.assertTrue(host.set_thinking(True)); self.assertEqual(self.out[-1], {"type":"thinking_changed","data":{"active":True}})
        clock.value = 1.0; self.assertTrue(host.tick()); self.assertEqual(self.out[-1]["data"]["group"], "text_generating")
    def test_conversion_waits_for_reloaded_renderer_catalog_before_exact_motion(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(clock=lambda: 0.0, rng=Random(1)))
        self.assertTrue(host.start_sakiko_conversion(False, {"white":"white.model.json"}))
        self.assertEqual(self.out, [])
        host.handle_renderer_fact({"type":"renderer_hello","data":{"renderer_id":"sakiko"}})
        switch = self.out[-1]
        self.assertEqual(switch["type"], "switch_live2d")
        self.assertEqual(switch["data"]["character_folder_name"], "sakiko")
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"sakiko","model_token":switch["data"]["model_token"],"motion_groups":{"change_character":1}}})
        self.assertEqual((self.out[-1]["type"],self.out[-1]["data"]["group"],self.out[-1]["data"]["priority"]), ("play_motion","change_character",2))

    def test_reconnect_ready_does_not_reset_an_active_segment(self):
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","motion_groups":{"happiness":1}}})
        self.assertTrue(self.host.start_emotion_segment(turn_id="t", segment_id="s", emotion="LABEL_0", audio_path="a.wav"))
        token = self.out[-1]["data"]["token"]
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","motion_groups":{"happiness":1}}})
        self.host.handle_renderer_fact({"type":"motion_started","data":{"renderer_id":"pygame","token":token}})
        self.assertEqual(self.out[-1]["type"], "play_audio")

    def test_counts_only_renderer_catalog_drops_old_expression_mapping(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(2)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame",
            "motion_files_by_group": {"happiness": ["happy_smile.mtn"]},
            "expression_ids": ["exp_smile01"],
        }})
        self.assertTrue(host.start_emotion_segment(
            turn_id="turn", segment_id="detailed", emotion="LABEL_0", audio_path="answer.wav",
        ))
        self.assertEqual(self.out[-1]["data"]["expression_id"], "exp_smile01")

        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame",
            "motion_groups": {"happiness": 1},
        }})
        self.assertTrue(host.start_emotion_segment(
            turn_id="turn", segment_id="counts-only", emotion="LABEL_0", audio_path="answer.wav",
        ))
        self.assertIsNone(self.out[-1]["data"]["expression_id"])

    def test_all_unavailable_renderers_reject_and_remove_queued_emotions(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        owner = AuthoritativeLive2DOwner()
        service = SharedRendererService(intents, facts, commands, owner)
        facts.put({"type": "renderer_hello", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one", "renderer_role": "pygame",
        }})
        intents.put({"type": "emotion_segment", "data": {
            "turn_id": "t", "segment_id": "s", "emotion": "LABEL_0", "audio_path": "a.wav",
        }})
        self.assertEqual(service.run_once(), 1)
        facts.put({"type": "renderer_unavailable", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one", "renderer_role": "pygame",
            "reason": "model_load_failed",
        }})
        self.assertEqual(service.run_once(), 2)
        self.assertEqual(len(service._pending_intents), 0)
        self.assertIsNone(owner.behavior.active_command)
        self.assertTrue(commands.empty())

    def test_active_segment_ends_when_its_only_renderer_disconnects(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one", "renderer_role": "pygame",
            "motion_groups": {"happiness": 1},
        }})
        host.start_emotion_segment(turn_id="t", segment_id="s", emotion="LABEL_0", audio_path="a.wav")
        host.handle_renderer_fact({"type": "renderer_unavailable", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one",
        }})
        self.assertIsNone(host._behavior.active_command)
        self.assertFalse(host._audio_owner_by_command)

    def test_model_switch_replays_master_expression_and_change_character(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_token": "old",
            "model_key": "anon", "motion_files_by_group": {
                "change_character": ["change.mtn"],
            }, "expression_ids": ["exp_idle01"],
        }})
        self.assertTrue(host.handle_runtime_control({
            "type": "switch_live2d", "character_folder_name": "anon",
            "character_name": "初华", "model_json": "anon.model.json",
        }))
        switch = self.out[-1]
        self.assertEqual(switch["type"], "switch_live2d")
        token = switch["data"]["model_token"]
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_token": token,
            "model_key": "anon", "motion_files_by_group": {
                "change_character": ["change.mtn"],
            }, "expression_ids": ["exp_idle01"],
        }})
        self.assertEqual(self.out[-1]["type"], "play_motion")
        self.assertEqual(self.out[-1]["data"]["group"], "change_character")
        self.assertEqual(self.out[-1]["data"]["expression_id"], "exp_idle01")

    def test_normal_switch_discards_completed_sakiko_conversion_replay(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko", "runtime_version": "v2", "model_token": "old",
            "model_urls": {"black": "black.model.json", "white": "white.model.json"},
            "motion_groups": {"change_character": 1, "change_character_maskoff": 1},
        }})
        self.assertTrue(host.start_sakiko_conversion(True, {"black": "black.model.json"}))
        conversion_token = self.out[-1]["data"]["model_token"]
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_token": conversion_token,
            "motion_groups": {"change_character": 1, "change_character_maskoff": 1},
        }})
        self.assertIsNotNone(host._conversion_replay_switch)
        self.out.clear()
        self.assertTrue(host.handle_runtime_control({
            "type": "switch_live2d", "character_folder_name": "anon",
            "character_name": "初华", "model_json": "anon.model.json",
        }))
        ordinary_token = self.out[-1]["data"]["model_token"]
        self.assertIsNone(host._conversion_replay_switch)
        self.assertIsNone(host._conversion_replay_motion)
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_token": ordinary_token,
            "motion_groups": {"change_character": 1},
        }})
        self.assertFalse(any(message["type"] == "switch_live2d" for message in self.out[1:]))

    def test_new_sakiko_conversion_supersedes_pending_normal_switch(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko", "runtime_version": "v2", "model_token": "old",
            "model_urls": {"black": "black.model.json", "white": "white.model.json"},
            "motion_groups": {"change_character": 1},
        }})
        self.assertTrue(host.handle_runtime_control({
            "type": "switch_live2d", "character_folder_name": "anon",
            "character_name": "初华", "model_json": "anon.model.json",
        }))
        self.assertIsNotNone(host._pending_model_switch)
        self.assertTrue(host.start_sakiko_conversion(True, {"black": "black.model.json"}))
        self.assertIsNone(host._pending_model_switch)
        self.assertIsNotNone(host._pending_conversion)
        self.assertEqual(self.out[-1]["type"], "switch_live2d")
        self.assertEqual(self.out[-1]["data"]["character_folder_name"], "sakiko")

    def test_disconnected_renderer_does_not_block_conversion_barrier(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(1)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko", "runtime_version": "v2", "model_token": "old",
            "model_urls": {"white": "white.model.json"},
            "motion_groups": {"change_character": 1},
        }})
        self.assertTrue(host.start_sakiko_conversion(False, {"white": "white.model.json"}))
        host.handle_renderer_fact({"type": "renderer_disconnected", "data": {"renderer_id": "pygame"}})
        self.assertIsNone(host._pending_conversion)
        self.assertIsNone(host._conversion_replay_motion)
        self.assertFalse(any(message["type"] == "play_motion" for message in self.out))

    def test_unavailable_renderer_is_removed_from_execution_targets(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one",
            "renderer_role": "pygame", "motion_groups": {"happiness": 1},
        }})
        self.assertTrue(host.handle_renderer_fact({"type": "renderer_unavailable", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one",
            "reason": "live2d_model_unavailable",
        }}))
        self.assertFalse(host.handle_renderer_fact({"type": "motion_started", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one", "token": "stale",
        }}))


    def test_switch_preserves_webui_turn_metadata(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner())
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one",
            "motion_groups": {}, "model_token": "old",
        }})
        self.assertTrue(host.handle_runtime_control({
            "type": "switch_live2d", "chat_id": "chat-1", "turn_id": "turn-2",
            "character_folder_name": "anon", "character_name": "爱音",
            "model_json": "anon.model.json",
        }))
        switch = self.out[-1]
        self.assertEqual(switch["type"], "switch_live2d")
        self.assertEqual(switch["data"]["chat_id"], "chat-1")
        self.assertEqual(switch["data"]["turn_id"], "turn-2")
        self.assertEqual(switch["data"]["character_folder_name"], "anon")

    def test_black_sakiko_switch_keeps_selected_model_path(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner())
        host._sakiko_conversion.is_black = True
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one",
            "model_key": "sakiko", "runtime_version": "v2", "model_token": "old",
            "motion_groups": {},
        }})
        self.assertTrue(host.handle_runtime_control({
            "type": "switch_live2d", "character_folder_name": "sakiko",
            "character_name": "祥子", "model_json": "custom.model.json",
        }))
        self.assertEqual(self.out[-1]["data"]["model_json"], "custom.model.json")

    def test_bye_terminal_ignores_regular_business_intents(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "motion_groups": {"bye": 1, "happiness": 1, "text_generating": 1},
        }})
        self.assertTrue(host.start_bye())
        command_count = len(self.out)
        self.assertFalse(host.set_thinking(True))
        self.assertFalse(host.start_emotion_segment(
            turn_id="t", segment_id="s", emotion="LABEL_0", audio_path="a.wav",
        ))
        self.assertFalse(host.tick())
        self.assertFalse(host.tick_long_audio())
        self.assertFalse(host.handle_runtime_control({"type": "start_talking"}))
        self.assertEqual(len(self.out), command_count)

    def test_sakiko_conversion_requires_v2_even_for_non_pygame_runtime(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner())
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "electron", "renderer_role": "electron", "model_key": "sakiko",
            "runtime_version": "", "motion_groups": {"change_character": 1},
        }})
        self.assertFalse(host.start_sakiko_conversion(True, {"black": "black.model.json"}))

if __name__ == "__main__":
    unittest.main()

