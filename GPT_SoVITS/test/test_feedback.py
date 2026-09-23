"""反馈白名单、客户端重试、回执隐私和只读导出测试。"""

from __future__ import annotations

import json
import copy
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from character import CharacterAttributes
from chat.chat import Chat, ChatManager, Message, StaticPromptGenerator
from chat.system_prompt import compose_system_prompt
from emotion_enum import EmotionEnum
from feedback.client import FeedbackClient, ReceiptStore
from feedback.protocol import convention_header, export_backup, freeze_payload, validate_payload
from feedback.importing import find_imported_chat, import_feedback, source_of, source_status_text


class MemorySecrets:
    """测试专用凭据库，不访问开发者的真实钥匙串。"""

    def __init__(self) -> None:
        """初始化测试键值。"""
        self.values: dict[str, str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        """记录秘密。"""
        self.values[username] = password

    def get_password(self, service: str, username: str) -> str | None:
        """读取秘密。"""
        return self.values.get(username)

    def delete_password(self, service: str, username: str) -> None:
        """删除秘密。"""
        self.values.pop(username, None)


def sample_character() -> CharacterAttributes:
    character = CharacterAttributes()
    character.character_folder_name = "missing-character"
    character.character_name = "missing-character"
    return character


def sample_chat() -> Chat:
    """构造含本地音频路径的对话，用于检查字段泄漏。"""
    return Chat(name="反馈测试", prompt_generator=StaticPromptGenerator("original"), message_list=[
        Message("missing-character", "对话正文", "翻译", EmotionEnum.SADNESS, "/private/audio.wav")])


class FeedbackProtocolTests(unittest.TestCase):
    """检验真正的导出边界和跨语言协议兼容性。"""

    def test_frozen_payload_and_text_backup(self) -> None:
        """消息后续编辑不影响冻结字节，备份只有安全文本与 Static 提示词。"""
        chat = sample_chat()
        _, body = freeze_payload(app_version="test", rating="up", comment="", chat=chat, character=sample_character(),
                                 base_system_prompt="current", runtime_system_prompt="runtime-only", target=0)
        chat.message_list[0].text = "edited"
        payload = json.loads(body)
        self.assertNotIn(b"audio.wav", body)
        self.assertEqual(payload["conversation"]["messages"][0]["text"], "对话正文")
        self.assertEqual(chat.prompt_generator.generate("ignored"), "original")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.zip"
            export_backup(payload, path)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), ["manifest.json"])
                manifest = json.loads(archive.read("manifest.json"))
            restored = Chat.from_dict(manifest["chats"][0]["chat"])
            self.assertEqual(restored.message_list[0].emotion, EmotionEnum.SADNESS)
            self.assertEqual(restored.prompt_generator.generate("missing-character"), "current")
            self.assertEqual(restored.message_list[0].audio_path, "")
            self.assertNotIn("runtime-only", restored.prompt_generator.generate("missing-character"))
            self.assertEqual(restored.get_character_name(), "missing-character")

    def test_v2_semantics_match_worker(self) -> None:
        """Python 与预编译 Worker 对角色、目标、提示词配对和旧版本给出一致结论。"""
        chat = sample_chat()
        chat.message_list.insert(0, Message("User", "hello", "", EmotionEnum.HAPPINESS, ""))
        _, body = freeze_payload(app_version="test", rating="up", comment="", chat=chat,
                                 character=sample_character(), base_system_prompt="  base\n", runtime_system_prompt="\nruntime  ", target=1)
        valid = json.loads(body)
        cases = [valid]
        changes = [lambda p: p.update(schema_version=1), lambda p: p.update(target=0),
                   lambda p: p.update(target=0.0), lambda p: p.update(target=2.0),
                   lambda p: p.update(target=2), lambda p: p.update(prompt_context=None),
                   lambda p: p.update(conversation=None),
                   lambda p: p["conversation"]["messages"][1].update(role="system"),
                   lambda p: p["conversation"].update(prompt_config={"type": "static", "content": "duplicate"}),
                   lambda p: p["conversation"]["character"].update(id=" "),
                   lambda p: p["prompt_context"].pop("runtime_system_prompt")]
        for change in changes:
            item = copy.deepcopy(valid)
            change(item)
            cases.append(item)
        outcomes = []
        for item in cases:
            try:
                validate_payload(item)
                outcomes.append(True)
            except ValueError:
                outcomes.append(False)
        self.assertEqual(outcomes, [True] + [False] * len(changes))
        program = "import {validPayload} from './src/protocol.js'; let b=''; for await(const c of process.stdin)b+=c; console.log(JSON.stringify(JSON.parse(b).map(validPayload)));"
        result = subprocess.run(["node", "--input-type=module", "-e", program], input=json.dumps(cases).encode(),
                                cwd=Path(__file__).resolve().parents[2] / "services/feedback", capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), outcomes)
        self.assertEqual(compose_system_prompt("  base\n", "\nruntime  "), "  base\n\n\nruntime  ")
        self.assertEqual(compose_system_prompt("  base\n", ""), "  base\n")

    def test_whole_payload_limit_even_when_individual_fields_fit(self) -> None:
        chat = sample_chat()
        chat.message_list *= 6
        for message in chat.message_list:
            message.text = "x" * 200000
        with self.assertRaisesRegex(ValueError, "1 MiB"):
            freeze_payload(app_version="test", rating="up", comment="", chat=chat, character=sample_character())

    def test_diagnostic_whitelist_and_worker_compatibility(self) -> None:
        """真实客户端输出通过 Worker 校验，并且不泄漏任意字典、路径或秘密。"""
        chat = sample_chat()
        chat.meta.worldbook.enabled = True
        diagnostic: dict[str, object] = {"format_version": 2, "record_id": "record", "chat_id": chat.chat_id,
            "errors": ["/private/secret.log"], "direct_retrieval": {"query": "问题", "thought_candidates": [
                {"entry_id": "entry", "payload": {"thought_text": "已知内容", "api_key": "secret-key", "path": "/private/data"}, "score": 0.8}],
                "injected_context": {"thoughts": [{"character_name": "角色", "thought_text": "知识", "epistemic_status": "believes"}], "events": []}},
            "tool_calls": [{"tool_name": "search", "arguments": {"query": "检索", "api_key": "secret-key"}, "result": {"secret": "secret-key"}}]}
        request_id, body = freeze_payload(app_version="test", rating="down", comment="诊断", chat=chat, character=sample_character(), base_system_prompt="current", diagnostics=[diagnostic])
        self.assertNotIn(b"secret-key", body)
        self.assertNotIn(b"/private/", body)
        self.assertIn("已知内容", body.decode())
        root = Path(__file__).resolve().parents[2]
        program = "import {validPayload,headerFor} from './src/protocol.js'; let b=''; for await(const c of process.stdin)b+=c; const p=JSON.parse(b); console.log(JSON.stringify({valid:validPayload(p),header:await headerFor(p.request_id)}));"
        result = subprocess.run(["node", "--input-type=module", "-e", program], input=body, cwd=root / "services/feedback", capture_output=True, check=True)
        answer = json.loads(result.stdout)
        self.assertTrue(answer["valid"])
        self.assertEqual(answer["header"], convention_header(request_id))

    def test_reject_invalid_target_empty_or_oversized(self) -> None:
        """不可为空、错位或超限时明确失败，不截断成另一份对话。"""
        with self.assertRaises(ValueError):
            freeze_payload(app_version="test", rating="none", comment="  ")
        with self.assertRaises(ValueError):
            freeze_payload(app_version="test", rating="up", comment="", chat=sample_chat(), character=sample_character(), target=4)
        with self.assertRaises(ValueError):
            freeze_payload(app_version="test", rating="up", comment="", chat=sample_chat(), character=sample_character(), base_system_prompt="x" * 1048577)

    def test_receipts_survive_restart_without_content_and_withdraw_by_request_id(self) -> None:
        """响应丢失时凭据已落盘，重启只恢复最小记录并可撤回。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.db"
            backend = MemorySecrets()
            store = ReceiptStore(path, backend)
            request_id, body = freeze_payload(app_version="test", rating="up", comment="private-feedback")
            receipt = store.add(request_id, "对话名称", "https://example.test")
            with patch.object(FeedbackClient, "_request", side_effect=ValueError("timeout")):
                with self.assertRaises(ValueError):
                    FeedbackClient(store).submit(receipt, body)
            reopened = ReceiptStore(path, backend)
            self.assertEqual(reopened.all()[0].status, "pending")
            self.assertEqual(reopened.token(request_id), store.token(request_id))
            disk = path.read_bytes()
            self.assertNotIn(body, disk)
            self.assertNotIn(b"private-feedback", disk)
            self.assertNotIn(store.token(request_id).encode(), disk)
            with patch.object(FeedbackClient, "_request", return_value={"ok": True, "request_id": request_id}) as send:
                FeedbackClient(reopened).withdraw(receipt)
                self.assertEqual(send.call_args.args[1], "DELETE")
            self.assertFalse(reopened.all())
            self.assertFalse(backend.values)

    def test_transport_uses_exact_bytes_headers_and_no_redirects(self) -> None:
        """传输保持冻结字节，携带控制秘密且不跟随可能泄漏正文的跳转。"""
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory) / "receipts", MemorySecrets())
            request_id, body = freeze_payload(app_version="test", rating="up", comment="")
            receipt = store.add(request_id, "标题", "https://feedback.example")
            response = MagicMock()
            response.__enter__.return_value = response
            response.status_code = 200
            response.iter_content.return_value = [json.dumps({"ok": True, "request_id": request_id, "feedback_id": "receipt-id"}).encode()]
            with patch("feedback.client.requests.request", return_value=response) as send:
                FeedbackClient(store).submit(receipt, body)
                arguments = send.call_args.kwargs
                self.assertIs(arguments["data"], body)
                self.assertFalse(arguments["allow_redirects"])
                self.assertEqual(arguments["headers"]["X-DSakiko-Feedback"], convention_header(request_id))
                self.assertEqual(arguments["headers"]["X-DSakiko-Control"], store.token(request_id))
                self.assertEqual(store.all()[0].feedback_id, "receipt-id")
                response.status_code = 429
                with self.assertRaisesRegex(ValueError, "稍后重试"):
                    FeedbackClient(store).submit(receipt, body)
                self.assertEqual(send.call_count, 2)


class FeedbackImportTests(unittest.TestCase):
    """验证导入副本的角色映射、持久化、去重与失败回滚。"""

    def detail(self, empty: bool = False) -> dict:
        chat = sample_chat()
        chat.meta.worldbook.enabled = True
        if empty:
            chat.message_list.clear()
        else:
            chat.message_list.insert(0, Message("User", "question", "", EmotionEnum.HAPPINESS, ""))
        _, body = freeze_payload(app_version="test", rating="up", comment="comment", chat=chat,
                                 character=sample_character(), base_system_prompt="original-name\nbase",
                                 runtime_system_prompt="do not import", target=None if empty else 1)
        return {"feedback_id": str(uuid.uuid4()), "expires_at": int(time.time()) + 3600,
                "processed": False, "payload": json.loads(body)}

    def test_import_maps_speakers_preserves_payload_and_survives_reload(self) -> None:
        detail = self.detail()
        original = copy.deepcopy(detail)
        manager = ChatManager()
        selected = sample_character()
        selected.character_name = "local-character"
        selected.character_folder_name = "local"
        with patch.object(manager, "save") as save:
            chat = import_feedback(manager, detail, "https://admin.example", selected)
            save.assert_called_once()
            self.assertIs(import_feedback(manager, detail, "https://admin.example", selected), chat)
            save.assert_called_once()
        self.assertEqual(detail, original)
        self.assertEqual([m.character_name for m in chat.message_list], ["User", "local-character"])
        self.assertFalse(chat.meta.worldbook.enabled)
        restored = Chat.from_dict(chat.to_dict())
        self.assertEqual(restored.get_character_name(), "local-character")
        self.assertEqual(restored.prompt_generator.generate("local-character"), "original-name\nbase")
        messages = restored.build_llm_query("local-character")
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant"])
        self.assertIs(find_imported_chat(ChatManager([restored]), "https://admin.example", detail["feedback_id"]), restored)
        self.assertIsNone(find_imported_chat(manager, "https://different.example", detail["feedback_id"]))
        clone = manager.clone_chat(chat.chat_id)
        manager.delete_chat(chat.chat_id)
        self.assertIsNone(find_imported_chat(manager, "https://admin.example", detail["feedback_id"]))
        with patch.object(manager, "save"):
            reimported = import_feedback(manager, detail, "https://admin.example", selected)
        self.assertNotEqual(reimported.chat_id, clone.chat_id)
        self.assertNotEqual(reimported.chat_id, chat.chat_id)

    def test_empty_chat_and_zip_keep_explicit_static_character(self) -> None:
        detail = self.detail(empty=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.zip"
            export_backup(detail["payload"], path)
            manager = ChatManager()
            result = manager.import_chats_from_backup(path)
        self.assertEqual(result.imported_chats[0].get_character_name(), "missing-character")
        self.assertEqual(result.imported_chats[0].message_list, [])
        self.assertEqual(StaticPromptGenerator.from_dict({"type": "static", "content": "legacy"}).to_dict(),
                         {"type": "static", "content": "legacy"})

    def test_save_failure_or_expired_feedback_does_not_leave_import(self) -> None:
        manager = ChatManager()
        detail = self.detail()
        with patch.object(manager, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                import_feedback(manager, detail, "https://admin.example", sample_character())
        self.assertEqual(manager.chat_list, [])
        detail["expires_at"] = 1
        with self.assertRaisesRegex(ValueError, "到期"):
            import_feedback(manager, detail, "https://admin.example", sample_character())
        self.assertEqual(manager.chat_list, [])

    def test_source_failure_and_unavailable_are_distinct(self) -> None:
        source = {"expires_at": int(time.time()) + 3600, "status": "unknown", "checked_at": int(time.time())}
        self.assertIn("暂时无法检查", source_status_text(source))
        source["status"] = "unavailable"
        self.assertIn("本地对话已保留", source_status_text(source))


if __name__ == "__main__":
    unittest.main()
