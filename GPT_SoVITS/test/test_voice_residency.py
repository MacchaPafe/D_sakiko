"""验证语音模型空闲释放、静音持久化和 worker 恢复协议。"""

from __future__ import annotations

import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from PyQt5.QtCore import QLockFile
from audio_generator import AudioGenerate
from inference_cli import SharedTTSManager, synthesize
from qconfig import DSakikoConfig
from runtime.voice_residency import VoiceResidencyPolicy


class VoiceResidencyTests(TestCase):
    """以可控时钟验证驻留策略，不加载实际语音权重。"""

    def test_pet_idle_requires_two_minutes_and_resets_after_conversation(self) -> None:
        """普通形态不回收，进入桌宠和对话结束后各自重新计时。"""
        policy = VoiceResidencyPolicy(0.0)
        self.assertFalse(policy.should_unload(1000.0, True))
        policy.observe(1000.0, pet_mode=True, busy=False, voice_enabled=True)
        self.assertFalse(policy.should_unload(1119.0, True))
        self.assertTrue(policy.should_unload(1120.0, True))
        policy.observe(1301.0, pet_mode=True, busy=True, voice_enabled=True)
        self.assertFalse(policy.should_unload(2000.0, True))
        policy.observe(2000.0, pet_mode=True, busy=False, voice_enabled=True)
        self.assertFalse(policy.should_unload(2119.0, True))
        self.assertTrue(policy.should_unload(2120.0, True))
        self.assertFalse(policy.should_unload(2120.0, False))
        policy.observe(2121.0, pet_mode=False, busy=False, voice_enabled=True)
        self.assertFalse(policy.should_unload(9999.0, True))

    def test_silence_timeout_cancels_on_restore_and_allows_text_conversations(
        self,
    ) -> None:
        """恢复语音取消倒计时；纯文字对话仍可申请在 worker 空闲时释放。"""
        policy = VoiceResidencyPolicy(0.0)
        policy.observe(10.0, pet_mode=False, busy=False, voice_enabled=False)
        self.assertFalse(policy.should_unload(69.0, False))
        policy.observe(69.0, pet_mode=False, busy=False, voice_enabled=True)
        self.assertFalse(policy.should_unload(100.0, False))
        policy.observe(101.0, pet_mode=False, busy=True, voice_enabled=False)
        self.assertTrue(policy.should_unload(200.0, False))
        policy.observe(201.0, pet_mode=False, busy=False, voice_enabled=False)
        self.assertTrue(policy.should_unload(201.0, False))

    def test_preferences_round_trip_without_touching_user_config(self) -> None:
        """通过真实配置存储验证两个选项的默认值和重启读取。"""
        config = DSakikoConfig()
        self.assertTrue(config.unload_voice_models_when_pet_idle.defaultValue)
        self.assertTrue(config.voice_output_enabled.defaultValue)
        original_voice = config.voice_output_enabled.value
        original_idle = config.unload_voice_models_when_pet_idle.value
        try:
            with TemporaryDirectory() as directory:
                config.file = Path(directory) / "config.json"
                config.lock = QLockFile(str(Path(directory) / "config.lock"))
                config.set(config.voice_output_enabled, False)
                config.set(config.unload_voice_models_when_pet_idle, False)
                data = json.loads(config.file.read_text(encoding="utf-8"))
                self.assertFalse(data["audio_setting"]["voice_output_enabled"])
                restored = DSakikoConfig()
                restored.load(config.file)
                self.assertFalse(restored.voice_output_enabled.value)
                self.assertFalse(restored.unload_voice_models_when_pet_idle.value)
        finally:
            config.voice_output_enabled.value = original_voice
            config.unload_voice_models_when_pet_idle.value = original_idle


class VoiceDispatchTests(TestCase):
    """验证主进程调度状态和 worker 管理协议的衔接。"""

    def setUp(self) -> None:
        """构造无进程和无模型的调度器，配置只替换模块内引用。"""
        self.config = SimpleNamespace(
            enable_voice_model_preload=SimpleNamespace(value=True),
            voice_output_enabled=SimpleNamespace(value=True),
            unload_voice_models_when_pet_idle=SimpleNamespace(value=True),
        )
        config_patch = patch("audio_generator.d_sakiko_config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.audio = AudioGenerate.__new__(AudioGenerate)
        self.audio.if_small_theater_mode = False
        self.audio.voice_schedule_lock = threading.Lock()
        self.audio.voice_residency = VoiceResidencyPolicy(0.0, pet_mode=True)
        self.audio.models_may_be_loaded = True
        self.audio.next_unload_attempt = 0.0
        self.audio.loaded_voice_key = "角色"
        self.audio.loading_voice_key = None
        self.audio.pending_preload_key = "另一个角色"
        self.audio.pending_preload_command = {
            "type": "load_model",
            "character_name": "另一个角色",
        }
        self.audio.pending_preload_not_before = 0.0
        self.audio.command_handles_lock = threading.Lock()
        self.audio.command_handles = {}

    def test_idle_release_clears_residency_and_does_not_reload_until_activity(
        self,
    ) -> None:
        """释放后禁止预加载反复唤醒，新合成仍可重新占用模型。"""
        with patch("audio_generator.time.monotonic", return_value=300.0):
            item = self.audio._take_idle_unload_command()
            self.assertIsNotNone(item)
            command, handle = item
            self.assertEqual(command["type"], "unload_all")
            self.audio._mark_worker_command_finished(
                command, {"type": "ack", "ok": True}
            )
            self.audio._set_handle_result(handle, {"type": "ack", "ok": True})
            self.assertIsNone(self.audio.loaded_voice_key)
            self.assertIsNone(self.audio.pending_preload_command)
            self.assertIsNone(self.audio._take_idle_unload_command())
            self.audio.request_preload_character(Mock())
            self.assertIsNone(self.audio.pending_preload_command)
            self.audio._mark_worker_command_started(
                {"type": "synthesize", "character_name": "角色"}
            )
            self.assertTrue(self.audio.models_may_be_loaded)
            self.audio._mark_worker_command_finished(
                {"type": "synthesize", "character_name": "角色"},
                {"type": "synthesize_result"},
            )
            self.assertEqual(self.audio.loaded_voice_key, "角色")
            self.assertFalse(self.audio.voice_residency.should_unload(301.0, True))

    def test_silent_start_skips_both_preload_entry_points(self) -> None:
        """静音启动既不登记角色预加载，也不执行之前待处理的预加载。"""
        self.config.voice_output_enabled.value = False
        character = Mock()
        self.audio.request_preload_character(character)
        character.has_valid_voice_model.assert_not_called()
        self.assertIsNone(self.audio.pending_preload_command)
        self.audio.pending_preload_command = {"type": "load_model"}
        self.assertIsNone(self.audio._take_pending_preload_command())

    def test_failed_unload_preserves_state_and_backs_off(self) -> None:
        """失败时不能误报已释放，且避免每帧重复提交卸载。"""
        with patch("audio_generator.time.monotonic", return_value=300.0):
            command, _ = self.audio._take_idle_unload_command()
            self.audio._mark_worker_command_finished(command, {"type": "error"})
            self.assertTrue(self.audio.models_may_be_loaded)
            self.assertEqual(self.audio.loaded_voice_key, "角色")
            self.assertIsNone(self.audio._take_idle_unload_command())

    def test_single_chat_silence_does_not_unload_theater_models(self) -> None:
        """普通对话静音不能改变小剧场的加载策略。"""
        self.config.voice_output_enabled.value = False
        self.audio.if_small_theater_mode = True
        self.audio.voice_residency.pet_mode = False
        with patch("audio_generator.time.monotonic", return_value=1000.0):
            self.assertIsNone(self.audio._take_idle_unload_command())
            self.assertIsNotNone(self.audio._take_pending_preload_command())

    def test_unload_all_releases_shared_caches_and_every_character(self) -> None:
        """所有角色壳、共享前端、CPU 权重和参考音频快照均须释放。"""
        manager = SharedTTSManager.__new__(SharedTTSManager)
        bundles = [Mock(frontend_key=None), Mock(frontend_key=None)]
        manager.bundles_by_signature = {"a": bundles[0], "b": bundles[1]}
        manager.weight_cache = Mock()
        manager.frontend_cache = Mock()
        runtimes = [Mock(), Mock()]
        manager.runtime_index = {"a": runtimes[0], "b": runtimes[1]}
        manager.unload_all()
        for bundle in bundles:
            bundle.tts.unload.assert_called_once()
        for runtime in runtimes:
            runtime.clear_prompt_cache_snapshot.assert_called_once()
            self.assertIsNone(runtime.last_bound_signature)
            self.assertIsNone(runtime.last_bound_bundle_key)
        manager.weight_cache.unload_all.assert_called_once()
        manager.frontend_cache.unload_all.assert_called_once()
        self.assertEqual(manager.runtime_index, {})
        self.assertEqual(manager.bundles_by_signature, {})

    def test_worker_accepts_load_again_after_unloading(self) -> None:
        """通过真实 worker 循环验证卸载有回执且后续加载仍能执行。"""
        commands: Queue[dict[str, object]] = Queue()
        results: Queue[dict[str, object]] = Queue()
        progress: Queue[dict[str, object]] = Queue()
        for command in (
            {"type": "load_model", "request_id": "1", "character_name": "角色"},
            {"type": "unload_all", "request_id": "2"},
            {"type": "load_model", "request_id": "3", "character_name": "角色"},
            {"type": "shutdown"},
        ):
            commands.put(command)
        runtime = Mock()
        manager = Mock()
        with (
            patch("inference_cli.SharedTTSManager", return_value=manager),
            patch("inference_cli.get_or_create_runtime", return_value=runtime),
            patch("inference_cli.signal.signal"),
            patch.dict("sys.modules", {"torch": None}),
        ):
            synthesize(commands, results, progress)
        self.assertEqual(
            [results.get_nowait()["request_id"] for _ in range(3)], ["1", "2", "3"]
        )
        self.assertEqual(runtime.load_now.call_count, 2)
        self.assertEqual(manager.unload_all.call_count, 2)
