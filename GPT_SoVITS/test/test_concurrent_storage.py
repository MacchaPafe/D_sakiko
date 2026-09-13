from __future__ import annotations

import contextlib
import json
import multiprocessing
import tempfile
import unittest
import uuid
from multiprocessing.connection import Connection
from pathlib import Path
from unittest import mock

from chat import chat as chat_module
from chat.chat import Chat, ChatManager, ChatType, StaticPromptGenerator, get_chat_manager
from runtime import audio_files
from runtime.conversation_storage import conversation_lock, save_before_terminal_close
from runtime.runtime_lock import RuntimeLockBusy, acquire_runtime_lock


def make_chat(name: str, scope: ChatType) -> Chat:
    """构造不依赖角色文件的最小会话。"""
    return Chat(name=name, type_=scope, prompt_generator=StaticPromptGenerator("测试"))


def save_worker(target: str, scope_value: int, pipe: Connection) -> None:
    """独立进程先加载旧快照，再按父进程指令交错保存。"""
    try:
        scope = ChatType(scope_value)
        manager = ChatManager.load(target)
        manager.write_scope = scope
        pipe.send("ready")
        pipe.recv()
        manager.chat_list = [chat for chat in manager.chat_list if chat.type != scope]
        for index in range(8):
            manager.add_chat(make_chat(f"{scope_value}-{index}", scope))
            manager.save(target)
        pipe.send("saved")
    except BaseException as exc:
        pipe.send(repr(exc))
        raise
    finally:
        pipe.close()


def reserve_worker(directory: str, pipe: Connection) -> None:
    """独立进程批量预留音频路径。"""
    try:
        pipe.send([audio_files.allocate_output_wav_path(directory) for _ in range(40)])
    finally:
        pipe.close()


def startup_worker(root: str, mode: str, pipe: Connection) -> None:
    """两个模式同时启动，验证旧记录只迁移一次。"""
    lease = None
    try:
        pipe.recv()
        lease = acquire_runtime_lock(root, mode)
        scope = ChatType.SMALL_THEATER if mode == "theater" else ChatType.SINGLE_CHARACTER
        with contextlib.chdir(Path(root) / "GPT_SoVITS"):
            manager = get_chat_manager(scope)
        pipe.send([chat.name for chat in manager.chat_list])
    except BaseException as exc:
        pipe.send(repr(exc))
        raise
    finally:
        if lease is not None:
            lease.release()
        pipe.close()


class ConcurrentStorageTest(unittest.TestCase):
    """覆盖跨进程合并、恢复、启动租约及音频分配行为。"""

    def setUp(self) -> None:
        """为每个测试隔离存档、运行锁与全局聊天管理器。"""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "GPT_SoVITS").mkdir()
        (self.root / "reference_audio").mkdir()
        self.target = self.root / "reference_audio" / "all_conversation.json"
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(chat_module, "_global_chat_manager", None).start()

    def test_two_processes_preserve_additions_and_deletions(self) -> None:
        """两个持有旧快照的进程保存后各自新增保留，旧会话不复活。"""
        ChatManager([make_chat("old-single", ChatType.SINGLE_CHARACTER),
                     make_chat("old-theater", ChatType.SMALL_THEATER)]).save(self.target)
        ctx = multiprocessing.get_context("spawn")
        workers = []
        for scope in (1, 2):
            parent, child = ctx.Pipe()
            process = ctx.Process(target=save_worker, args=(str(self.target), scope, child))
            process.start()
            child.close()
            workers.append((process, parent))
        try:
            for _, pipe in workers:
                self.assertTrue(pipe.poll(45))
                self.assertEqual(pipe.recv(), "ready")
            for _, pipe in workers:
                pipe.send("go")
            for process, pipe in workers:
                self.assertTrue(pipe.poll(45))
                self.assertEqual(pipe.recv(), "saved")
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            names = {chat.name for chat in ChatManager.load(self.target).chat_list}
            self.assertEqual(names, {f"{scope}-{index}" for scope in (1, 2) for index in range(8)})
        finally:
            for process, pipe in workers:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                pipe.close()

    def test_runtime_modes_and_stale_lock_file(self) -> None:
        """同类互斥、异类共存，残留的锁文件不会阻止重启。"""
        web = acquire_runtime_lock(self.root, "web")
        theater = acquire_runtime_lock(self.root, "theater")
        try:
            for mode in ("desktop", "web", "theater"):
                with self.assertRaises(RuntimeLockBusy):
                    acquire_runtime_lock(self.root, mode)
        finally:
            theater.release()
            web.release()
        acquire_runtime_lock(self.root, "desktop").release()

    def test_scoped_save_preserves_other_type_and_local_object(self) -> None:
        """只替换负责类型，保留另一模式的新名称和顶层扩展字段。"""
        single = make_chat("single", ChatType.SINGLE_CHARACTER)
        theater = make_chat("theater", ChatType.SMALL_THEATER)
        ChatManager([single, theater]).save(self.target)
        stale = ChatManager.load(self.target)
        stale.write_scope = ChatType.SMALL_THEATER
        latest = json.loads(self.target.read_text())
        latest["chat_list"][0]["name"] = "new-single"
        latest["future_metadata"] = "keep"
        self.target.write_text(json.dumps(latest))
        local = stale.chat_list[1]
        local.name = "new-theater"
        stale.save(self.target)
        result = json.loads(self.target.read_text())
        self.assertEqual(result["future_metadata"], "keep")
        self.assertEqual([item["name"] for item in result["chat_list"]], ["new-single", "new-theater"])
        self.assertIs(stale.chat_list[1], local)

    def test_running_recovery_and_backup_failure(self) -> None:
        """运行中恢复整个内存快照；备份失败时保持原文件与内存。"""
        manager = ChatManager([make_chat("mine", ChatType.SMALL_THEATER)], write_scope=ChatType.SMALL_THEATER)
        self.target.write_bytes(b"broken")
        with mock.patch.object(chat_module, "backup_corrupt", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                manager.save(self.target)
        self.assertEqual(self.target.read_bytes(), b"broken")
        manager.save(self.target)
        self.assertEqual(ChatManager.load(self.target).chat_list[0].name, "mine")
        self.assertEqual(next((self.target.parent / "backup").iterdir()).read_bytes(), b"broken")
        self.assertIn("另一类", manager.storage_notices[-1])

    def test_startup_corruption_with_and_without_other_mode(self) -> None:
        """另一模式运行时拒绝恢复；独占运行时备份并立即创建空存档。"""
        self.target.write_bytes(b"")
        other = acquire_runtime_lock(self.root, "web")
        try:
            with contextlib.chdir(self.root / "GPT_SoVITS"):
                with self.assertRaisesRegex(RuntimeError, "另一模式"):
                    get_chat_manager(ChatType.SMALL_THEATER)
            self.assertEqual(self.target.read_bytes(), b"")
            self.assertFalse((self.target.parent / "backup").exists())
        finally:
            other.release()
        with contextlib.chdir(self.root / "GPT_SoVITS"):
            manager = get_chat_manager(ChatType.SMALL_THEATER)
        self.assertEqual(manager.chat_list, [])
        self.assertEqual(json.loads(self.target.read_text()), {"chat_list": []})
        self.assertTrue(list((self.target.parent / "backup").iterdir()))

    def test_import_filters_before_restoring_resources(self) -> None:
        """混合备份仅导入本模式会话，同时明确报告跳过项。"""
        chats = [make_chat("single", ChatType.SINGLE_CHARACTER), make_chat("theater", ChatType.SMALL_THEATER)]
        backup = self.root / "backup.zip"
        ChatManager(chats).export_chats_to_backup([chat.chat_id for chat in chats], backup)
        manager = ChatManager(write_scope=ChatType.SINGLE_CHARACTER)
        result = manager.import_chats_from_backup(backup)
        self.assertEqual(len(result.imported_chats), 1)
        self.assertEqual(result.imported_chats[0].type, ChatType.SINGLE_CHARACTER)
        self.assertTrue(any("对应模式" in warning for warning in result.warnings))

    def test_audio_collision_retries_without_overwrite(self) -> None:
        """强制 UUID 碰撞时保留旧音频，并独占预留下一个文件名。"""
        first, second = uuid.uuid4(), uuid.uuid4()
        old = self.root / f"output_{first.hex}.wav"
        old.write_bytes(b"old-audio")
        with mock.patch.object(audio_files.uuid, "uuid4", side_effect=[first, second]):
            path = audio_files.allocate_output_wav_path(str(self.root))
        self.assertEqual(old.read_bytes(), b"old-audio")
        self.assertEqual(Path(path).name, f"output_{second.hex}.wav")
        self.assertTrue(Path(path).exists())

    def test_audio_paths_across_processes(self) -> None:
        """两个音频进程独立分配文件，结果互不覆盖。"""
        ctx = multiprocessing.get_context("spawn")
        workers = []
        for _ in range(2):
            parent, child = ctx.Pipe()
            process = ctx.Process(target=reserve_worker, args=(str(self.root), child))
            process.start()
            child.close()
            workers.append((process, parent))
        paths = []
        try:
            for process, pipe in workers:
                self.assertTrue(pipe.poll(45))
                paths.extend(pipe.recv())
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(len(set(paths)), 80)
            self.assertTrue(all(Path(path).exists() for path in paths))
        finally:
            for process, pipe in workers:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                pipe.close()

    def test_concurrent_startup_migrates_once(self) -> None:
        """两个模式同时启动时，迁移存档与归档旧文件不会重复执行。"""
        legacy = self.target.parent / "history_messages_dp.json"
        legacy.write_text(json.dumps([{"character": "migration-test", "history": []}]))
        (self.target.parent / "history_messages_qt.json").write_text("[]")
        ctx = multiprocessing.get_context("spawn")
        workers = []
        for mode in ("web", "theater"):
            parent, child = ctx.Pipe()
            process = ctx.Process(target=startup_worker, args=(str(self.root), mode, child))
            process.start()
            child.close()
            workers.append((process, parent))
        try:
            for _, pipe in workers:
                pipe.send("go")
            for process, pipe in workers:
                self.assertTrue(pipe.poll(45))
                self.assertEqual(pipe.recv(), ["migration-test"])
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(len(ChatManager.load(self.target).chat_list), 1)
            self.assertFalse(legacy.exists())
            self.assertTrue((self.target.parent / "old_history_messages" / legacy.name).exists())
        finally:
            for process, pipe in workers:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                pipe.close()

    def test_migration_archive_failure_does_not_duplicate_records(self) -> None:
        """迁移已提交但归档失败时，下一次启动利用标记避免重复导入。"""
        legacy = self.target.parent / "history_messages_dp.json"
        legacy.write_text(json.dumps([{"character": "migration-test", "history": []}]))
        (self.target.parent / "history_messages_qt.json").write_text("[]")
        with contextlib.chdir(self.root / "GPT_SoVITS"):
            with mock.patch.object(chat_module, "_move_legacy_files_to_backup"):
                manager = get_chat_manager(ChatType.SMALL_THEATER)
            self.assertEqual(len(manager.chat_list), 1)
            manager.save()
            chat_module._global_chat_manager = None
            manager = get_chat_manager(ChatType.SINGLE_CHARACTER)
        self.assertEqual(len(manager.chat_list), 1)
        self.assertFalse(legacy.exists())

    def test_save_timeout_preserves_memory_and_disk(self) -> None:
        """存档锁超时不会覆盖磁盘，并保留内存中尚未保存的修改。"""
        manager = ChatManager([make_chat("old", ChatType.SMALL_THEATER)], write_scope=ChatType.SMALL_THEATER)
        manager.save(self.target)
        original = self.target.read_bytes()
        manager.chat_list[0].name = "unsaved"
        from filelock import FileLock, Timeout
        with conversation_lock(self.target):
            with mock.patch.object(chat_module, "conversation_lock", return_value=FileLock(str(self.target) + ".lock", timeout=0.01)):
                with self.assertRaises(Timeout):
                    manager.save(self.target)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(manager.chat_list[0].name, "unsaved")
        manager.save(self.target)
        self.assertEqual(ChatManager.load(self.target).chat_list[0].name, "unsaved")

    def test_close_save_choices(self) -> None:
        """退出保存失败时可取消、重试或明确放弃，成功后才允许退出。"""
        from PyQt5.QtWidgets import QMessageBox
        from runtime.storage_ui import save_before_close
        manager = ChatManager()
        parent = mock.Mock()
        with mock.patch.object(manager, "save", side_effect=OSError("full")):
            with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Cancel):
                self.assertFalse(save_before_close(manager, parent))
            with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Discard):
                self.assertTrue(save_before_close(manager, parent))
        with mock.patch.object(manager, "save", side_effect=[OSError("full"), None]) as save:
            with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Retry):
                self.assertTrue(save_before_close(manager, parent))
            self.assertEqual(save.call_count, 2)

    def test_web_save_retry_and_recovery_notice(self) -> None:
        """网页可以重试存档写入，重连同步会补发尚未看到的恢复提示。"""
        from dsakiko_webui.backend.assets import AssetRegistry
        from dsakiko_webui.backend.protocol import ProtocolError
        from dsakiko_webui.backend.runtime import HeadlessRuntime
        runtime = HeadlessRuntime(AssetRegistry())
        self.addCleanup(runtime.uploads.close)
        runtime.status = "ready"
        runtime.chat_manager = ChatManager(write_scope=ChatType.SINGLE_CHARACTER)
        with mock.patch.object(runtime.chat_manager, "save", side_effect=OSError("full")):
            with self.assertRaises(ProtocolError) as raised:
                runtime.handle_command("save_chats", {})
        self.assertEqual(raised.exception.code, "CHAT_SAVE_FAILED")
        runtime._storage_notice("损坏存档已备份，已创建新的空白存档。")
        with mock.patch.object(runtime, "chat_list_snapshot", return_value={}):
            _, events = runtime.handle_command("sync", {})
        self.assertEqual(events[-1]["data"]["error"]["code"], "CHAT_STORAGE_RECOVERED")
        with mock.patch.object(runtime.chat_manager, "save") as save:
            result, _ = runtime.handle_command("save_chats", {})
        save.assert_called_once()
        self.assertEqual(result, {"saved": True})
        self.assertIsNone(runtime._last_storage_notice)

    def test_terminal_save_retry_and_discard(self) -> None:
        """终端保存失败时重试，只有显式放弃才忽略错误。"""
        save = mock.Mock(side_effect=[OSError("full"), None])
        with mock.patch("builtins.input", return_value="r"):
            save_before_terminal_close(save)
        self.assertEqual(save.call_count, 2)
        save = mock.Mock(side_effect=OSError("full"))
        with mock.patch("builtins.input", return_value="discard"):
            save_before_terminal_close(save)
        self.assertEqual(save.call_count, 1)


if __name__ == "__main__":
    unittest.main()
