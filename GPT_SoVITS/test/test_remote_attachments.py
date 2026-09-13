from __future__ import annotations

import base64
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from chat.chat import (
    AttachmentSerializationContext,
    Message,
    MessageAttachment,
    RemoteFileReference,
)
from chat.remote_attachments import (
    DEEPSEEK_FILES_MODEL,
    DeepSeekFileServiceConfig,
    RemoteAttachmentError,
    RemoteAttachmentManager,
    build_deepseek_file_service_config,
    normalize_deepseek_api_base,
)
from emotion_enum import EmotionEnum


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _UploadStatusError(RuntimeError):
    """模拟包含 HTTP 状态码的上传错误。"""

    def __init__(self, status_code: int) -> None:
        """保存测试指定的 HTTP 状态码。"""
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _reference(
        service: DeepSeekFileServiceConfig,
        file_id: str = "file-api-test",
        *,
        expires_in_seconds: int = 3600,
) -> RemoteFileReference:
    """构造测试使用的远端文件引用。"""
    now = datetime.now(timezone.utc)
    return RemoteFileReference(
        api_base=service.api_base,
        api_key_digest=service.api_key_digest,
        file_id=file_id,
        uploaded_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=expires_in_seconds)).isoformat(),
    )


class RemoteAttachmentManagerTestCase(unittest.TestCase):
    """验证 DeepSeek 远端附件管理器的持久化和作用域行为。"""

    def setUp(self) -> None:
        """创建隔离的上传日志和一张可读取的微型图片。"""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.image_path = self.root / "tiny.png"
        self.image_path.write_bytes(_TINY_PNG)
        self.manager = RemoteAttachmentManager(self.root / "drafts")
        self.service = DeepSeekFileServiceConfig(
            api_base="https://api.deepseek.com",
            api_key="sk-test-one",
        )

    def tearDown(self) -> None:
        """关闭 worker 并删除临时目录。"""
        self.manager.shutdown()
        self.temporary_directory.cleanup()

    def test_official_base_normalization_and_scope_gate(self) -> None:
        """官方版本路径应归一化，非官方 hostname 不得启用 Files 模式。"""
        self.assertEqual(
            normalize_deepseek_api_base("HTTPS://API.DEEPSEEK.COM/v1/"),
            "https://api.deepseek.com",
        )
        self.assertIsNone(normalize_deepseek_api_base("https://proxy.example/v1"))
        enabled = build_deepseek_file_service_config(
            provider_id="deepseek",
            model=f"deepseek/{DEEPSEEK_FILES_MODEL}",
            api_base="",
            api_key="sk-test",
        )
        self.assertIsNotNone(enabled)
        self.assertIsNone(build_deepseek_file_service_config(
            provider_id="custom",
            model=DEEPSEEK_FILES_MODEL,
            api_base="https://proxy.example/v1",
            api_key="sk-test",
        ))

    def test_stage_upload_and_reload_journal(self) -> None:
        """草稿上传成功后应保存 file ID，重新加载日志仍可读取。"""
        record = self.manager.stage_draft_image(str(self.image_path))
        uploaded_reference = _reference(self.service)
        with mock.patch.object(
            self.manager,
            "_upload_file_once",
            return_value=uploaded_reference,
        ):
            uploaded = self.manager.upload_draft_attachment(
                record.draft_attachment_id,
                self.service,
            )
        self.assertEqual(uploaded.upload_state, "ready")
        self.assertEqual(uploaded.remote_refs[0].file_id, "file-api-test")
        journal_text = self.manager.journal_path.read_text(encoding="utf-8")
        self.assertNotIn("sk-test-one", journal_text)
        self.assertIn(self.service.api_key_digest, journal_text)

        self.manager.shutdown()
        reloaded = RemoteAttachmentManager(self.root / "drafts")
        self.manager = reloaded
        restored = reloaded.get_draft(record.draft_attachment_id)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.remote_refs[0].file_id, "file-api-test")

    def test_same_attachment_keeps_multiple_service_scopes(self) -> None:
        """切换 API Key 后应新增作用域引用而不覆盖旧账号引用。"""
        attachment = MessageAttachment(
            type="image",
            path=str(self.image_path),
            mime_type="image/png",
            original_name="tiny.png",
        )
        second_service = DeepSeekFileServiceConfig(
            api_base=self.service.api_base,
            api_key="sk-test-two",
        )
        references = iter([
            _reference(self.service, "file-api-one"),
            _reference(second_service, "file-api-two"),
        ])
        with mock.patch.object(
            self.manager,
            "_upload_file_once",
            side_effect=lambda *_args: next(references),
        ):
            self.manager.ensure_remote_reference(attachment, self.service)
            self.manager.ensure_remote_reference(attachment, second_service)
        self.assertEqual(
            {reference.file_id for reference in attachment.remote_refs},
            {"file-api-one", "file-api-two"},
        )

    def test_upload_retries_once_for_server_error(self) -> None:
        """HTTP 5xx 应自动重试一次并在第二次成功后保存引用。"""
        record = self.manager.stage_draft_image(str(self.image_path))
        upload = mock.Mock(side_effect=[
            _UploadStatusError(503),
            _reference(self.service),
        ])
        with (
            mock.patch.object(self.manager, "_upload_file_once", upload),
            mock.patch("chat.remote_attachments.time.sleep"),
        ):
            result = self.manager.upload_draft_attachment(
                record.draft_attachment_id,
                self.service,
            )
        self.assertEqual(result.upload_state, "ready")
        self.assertEqual(upload.call_count, 2)

    def test_litellm_upload_passes_user_data_and_expiration(self) -> None:
        """Files 上传必须显式传递 user_data、30 天过期和 OpenAI provider。"""
        with mock.patch(
            "litellm.create_file",
            return_value=SimpleNamespace(id="file-api-test"),
        ) as create_file:
            reference = self.manager._upload_file_once(
                self.image_path,
                "tiny.png",
                "image/png",
                self.service,
            )
        self.assertEqual(reference.file_id, "file-api-test")
        request = create_file.call_args.kwargs
        self.assertEqual(request["purpose"], "user_data")
        self.assertEqual(request["custom_llm_provider"], "openai")
        self.assertEqual(
            request["expires_after"],
            {"anchor": "created_at", "seconds": 2592000},
        )

    def test_upload_does_not_retry_authentication_error(self) -> None:
        """HTTP 401 不得自动重试，并应保留可手动重试的失败草稿。"""
        record = self.manager.stage_draft_image(str(self.image_path))
        upload = mock.Mock(side_effect=_UploadStatusError(401))
        with mock.patch.object(self.manager, "_upload_file_once", upload):
            with self.assertRaises(RemoteAttachmentError):
                self.manager.upload_draft_attachment(
                    record.draft_attachment_id,
                    self.service,
                )
        failed = self.manager.get_draft(record.draft_attachment_id)
        self.assertIsNotNone(failed)
        assert failed is not None
        self.assertEqual(failed.upload_state, "failed")
        self.assertEqual(upload.call_count, 1)

    def test_deepseek_serialization_uses_top_level_file_part(self) -> None:
        """DeepSeek Files 上下文应只序列化顶层 file/file_id 内容块。"""
        attachment = MessageAttachment(
            type="image",
            path=str(self.image_path),
            mime_type="image/png",
            original_name="tiny.png",
            remote_refs=[_reference(self.service)],
        )
        message = Message(
            character_name="User",
            text="请看图片",
            translation="",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="",
            attachments=[attachment],
        )
        content = message.to_llm_content(
            "user",
            attachment_context=AttachmentSerializationContext(
                mode="deepseek_file",
                api_base=self.service.api_base,
                api_key_digest=self.service.api_key_digest,
            ),
        )
        self.assertIsInstance(content, list)
        assert isinstance(content, list)
        self.assertEqual(
            content[-1],
            {"type": "file", "file_id": "file-api-test"},
        )

    def test_unavailable_attachment_is_never_serialized(self) -> None:
        """用户确认忽略的缺失附件不得再次进入 LLM history。"""
        attachment = MessageAttachment(
            type="image",
            path="missing.png",
            availability="unavailable",
            unavailable_reason="missing",
            remote_refs=[_reference(self.service)],
        )
        message = Message(
            character_name="User",
            text="继续",
            translation="",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="",
            attachments=[attachment],
        )
        self.assertEqual(
            message.to_llm_content(
                "user",
                attachment_context=AttachmentSerializationContext(
                    mode="deepseek_file",
                    api_base=self.service.api_base,
                    api_key_digest=self.service.api_key_digest,
                ),
            ),
            "[User]: 继续",
        )

    def test_reachability_scan_keeps_reference_used_by_formal_message(self) -> None:
        """草稿删除后，仍被正式消息引用的远端文件不得被清理。"""
        record = self.manager.stage_draft_image(str(self.image_path))
        reference = _reference(self.service)
        with mock.patch.object(
            self.manager,
            "_upload_file_once",
            return_value=reference,
        ):
            self.manager.upload_draft_attachment(
                record.draft_attachment_id,
                self.service,
            )
        self.manager.cancel_draft_attachment(record.draft_attachment_id)
        formal_attachment = MessageAttachment(
            type="image",
            path=str(self.image_path),
            remote_refs=[reference],
        )
        with mock.patch.object(self.manager, "_delete_or_defer") as delete_remote:
            self.manager.reconcile_and_cleanup([formal_attachment], [self.service])
        delete_remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
