"""反馈白名单、客户端重试、回执隐私和只读导出测试。"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chat.chat import Chat, Message, StaticPromptGenerator
from emotion_enum import EmotionEnum
from feedback.client import FeedbackClient, ReceiptStore
from feedback.protocol import convention_header, export_backup, freeze_payload


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


def sample_chat() -> Chat:
    """构造含本地音频路径的对话，用于检查字段泄漏。"""
    return Chat(name="反馈测试", prompt_generator=StaticPromptGenerator("original"), message_list=[
        Message("missing-character", "对话正文", "翻译", EmotionEnum.SADNESS, "/private/audio.wav")])


class FeedbackProtocolTests(unittest.TestCase):
    """检验真正的导出边界和跨语言协议兼容性。"""

    def test_frozen_payload_and_text_backup(self) -> None:
        """消息后续编辑不影响冻结字节，备份只有安全文本与 Static 提示词。"""
        chat = sample_chat()
        _, body = freeze_payload(app_version="test", rating="up", comment="", chat=chat, system_prompt="current", target=0)
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

    def test_diagnostic_whitelist_and_worker_compatibility(self) -> None:
        """真实客户端输出通过 Worker 校验，并且不泄漏任意字典、路径或秘密。"""
        chat = sample_chat()
        chat.meta.worldbook.enabled = True
        diagnostic: dict[str, object] = {"format_version": 2, "record_id": "record", "chat_id": chat.chat_id,
            "errors": ["/private/secret.log"], "direct_retrieval": {"query": "问题", "thought_candidates": [
                {"entry_id": "entry", "payload": {"thought_text": "已知内容", "api_key": "secret-key", "path": "/private/data"}, "score": 0.8}],
                "injected_context": {"thoughts": [{"character_name": "角色", "thought_text": "知识", "epistemic_status": "believes"}], "events": []}},
            "tool_calls": [{"tool_name": "search", "arguments": {"query": "检索", "api_key": "secret-key"}, "result": {"secret": "secret-key"}}]}
        request_id, body = freeze_payload(app_version="test", rating="down", comment="诊断", chat=chat, system_prompt="current", diagnostics=[diagnostic])
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
            freeze_payload(app_version="test", rating="up", comment="", chat=sample_chat(), target=4)
        with self.assertRaises(ValueError):
            freeze_payload(app_version="test", rating="up", comment="", chat=sample_chat(), system_prompt="x" * 1048577)

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


if __name__ == "__main__":
    unittest.main()
