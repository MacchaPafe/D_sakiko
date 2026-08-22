from __future__ import annotations

import dataclasses
import unittest
from types import SimpleNamespace
from unittest import mock

import dp_local2
from chat.chat import Chat, ChatManager, Message, MessageAttachment, RemoteFileReference
from emotion_enum import EmotionEnum


@dataclasses.dataclass
class _ConfigItem:
    """模拟 qconfig 配置项的 value 接口。"""

    value: object


class _DeepSeekConfig:
    """提供补全路由测试所需的最小 DeepSeek 配置。"""

    def __init__(self) -> None:
        """初始化官方 DeepSeek 视觉模型配置。"""
        self.use_default_deepseek_api = _ConfigItem(False)
        self.enable_custom_llm_api_provider = _ConfigItem(False)
        self.custom_llm_api_model = _ConfigItem("")
        self.custom_llm_api_url = _ConfigItem("")
        self.custom_llm_api_key = _ConfigItem("")
        self.llm_api_provider = _ConfigItem("deepseek")
        self.llm_api_model = _ConfigItem({
            "deepseek": "deepseek-v4-flash-vision-exp",
        })
        self.llm_api_key = _ConfigItem({"deepseek": "sk-test"})
        self.llm_api_base_url = _ConfigItem({})
        self.llm_temperature = _ConfigItem(0.7)
        self.llm_top_p = _ConfigItem(0.9)


class _InvalidFileError(RuntimeError):
    """模拟 DeepSeek 返回的无效 file ID 400 错误。"""

    status_code = 400


class DeepSeekMultimodalTransportTestCase(unittest.TestCase):
    """验证统一 completion 入口只为目标多模态请求切换 SDK。"""

    def setUp(self) -> None:
        """创建不执行完整初始化的补全主体。"""
        self.subject = dp_local2.DSLocalAndVoiceGen.__new__(
            dp_local2.DSLocalAndVoiceGen
        )
        self.subject.d_sakiko_config = _DeepSeekConfig()

    def test_file_content_routes_to_openai_sdk_adapter(self) -> None:
        """DeepSeek 官方视觉模型的 file part 应走 SDK 薄适配器。"""
        sentinel = object()
        with (
            mock.patch.object(
                self.subject,
                "_execute_deepseek_multimodal_completion",
                return_value=sentinel,
            ) as direct_completion,
            mock.patch.object(
                self.subject,
                "_execute_completion_with_provider_policy",
            ) as litellm_completion,
        ):
            result = self.subject._completion_with_current_config(
                model="test",
                messages=[{
                    "role": "user",
                    "content": [{"type": "file", "file_id": "file-api-test"}],
                }],
                _reasoning_snapshot_locked=True,
            )
        self.assertIs(result, sentinel)
        direct_completion.assert_called_once()
        litellm_completion.assert_not_called()

    def test_text_only_deepseek_request_stays_on_litellm(self) -> None:
        """DeepSeek 文本请求不得因为模型名称而绕过 LiteLLM。"""
        sentinel = object()
        with (
            mock.patch.object(
                self.subject,
                "_execute_deepseek_multimodal_completion",
            ) as direct_completion,
            mock.patch.object(
                self.subject,
                "_execute_completion_with_provider_policy",
                return_value=sentinel,
            ) as litellm_completion,
        ):
            result = self.subject._completion_with_current_config(
                model="test",
                messages=[{"role": "user", "content": "hello"}],
                _reasoning_snapshot_locked=True,
            )
        self.assertIs(result, sentinel)
        direct_completion.assert_not_called()
        litellm_completion.assert_called_once()

    def test_non_official_custom_endpoint_stays_on_litellm(self) -> None:
        """同名模型位于非官方自定义端点时仍应走 LiteLLM。"""
        self.subject.d_sakiko_config.enable_custom_llm_api_provider.value = True
        self.subject.d_sakiko_config.custom_llm_api_model.value = (
            "deepseek/deepseek-v4-flash-vision-exp"
        )
        self.subject.d_sakiko_config.custom_llm_api_url.value = "https://proxy.example/v1"
        self.subject.d_sakiko_config.custom_llm_api_key.value = "sk-test"
        with (
            mock.patch.object(
                self.subject,
                "_execute_deepseek_multimodal_completion",
            ) as direct_completion,
            mock.patch.object(
                self.subject,
                "_execute_completion_with_provider_policy",
                return_value="litellm",
            ),
        ):
            result = self.subject._completion_with_current_config(
                model="test",
                messages=[{
                    "role": "user",
                    "content": [{"type": "file", "file_id": "file-api-test"}],
                }],
                _reasoning_snapshot_locked=True,
            )
        self.assertEqual(result, "litellm")
        direct_completion.assert_not_called()

    def test_invalid_file_error_classifier_is_conservative(self) -> None:
        """只有明确包含 file ID 语义的 HTTP 400 才应触发修复。"""
        self.assertTrue(self.subject._is_invalid_deepseek_file_error(
            _InvalidFileError("file_ids do not exist or are not created under your account")
        ))
        self.assertFalse(self.subject._is_invalid_deepseek_file_error(
            _InvalidFileError("response_format is unsupported")
        ))

    def test_sdk_adapter_strips_litellm_fields_and_maps_reasoning(self) -> None:
        """SDK 适配器不得泄漏 LiteLLM 私有字段，并应映射 DeepSeek 推理参数。"""
        service = self.subject._current_deepseek_file_service()
        self.assertIsNotNone(service)
        assert service is not None
        fake_response = SimpleNamespace(choices=[])
        fake_client = mock.Mock()
        fake_client.chat.completions.create.return_value = fake_response
        with (
            mock.patch("openai.OpenAI", return_value=fake_client) as openai_client,
            mock.patch.object(dp_local2, "log_prompt_cache_usage"),
        ):
            result = self.subject._openai_deepseek_chat_completion(
                {
                    "model": "deepseek/deepseek-v4-flash-vision-exp",
                    "messages": [{"role": "user", "content": "hello"}],
                    "api_key": "hidden",
                    "base_url": "https://api.deepseek.com",
                    "timeout": 15,
                    "thinking": {"type": "enabled"},
                    "reasoning_effort": "xhigh",
                    dp_local2.CACHE_DEBUG_PHASE_KEY: "test",
                },
                service,
            )
        self.assertIs(result, fake_response)
        openai_client.assert_called_once_with(
            api_key="sk-test",
            base_url="https://api.deepseek.com",
            timeout=15,
            max_retries=0,
        )
        request = fake_client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "deepseek-v4-flash-vision-exp")
        self.assertEqual(request["extra_body"]["thinking"], {"type": "enabled"})
        self.assertEqual(request["extra_body"]["reasoning_effort"], "max")
        self.assertNotIn("api_key", request)
        self.assertNotIn(dp_local2.CACHE_DEBUG_PHASE_KEY, request)

    def test_repair_replaces_file_id_in_whole_request(self) -> None:
        """服务端拒绝 file ID 后应重新上传并改写待重试的完整 messages。"""
        service = self.subject._current_deepseek_file_service()
        self.assertIsNotNone(service)
        assert service is not None
        old_reference = RemoteFileReference(
            api_base=service.api_base,
            api_key_digest=service.api_key_digest,
            file_id="file-api-old",
            uploaded_at="2026-01-01T00:00:00+00:00",
            expires_at="2026-09-01T00:00:00+00:00",
        )
        new_reference = dataclasses.replace(old_reference, file_id="file-api-new")
        attachment = MessageAttachment(
            type="image",
            path="missing.png",
            remote_refs=[old_reference],
        )
        chat = Chat(message_list=[Message(
            character_name="User",
            text="看图",
            translation="",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="",
            attachments=[attachment],
        )])
        self.subject.chat_manager = ChatManager([chat])
        self.subject.current_chat_id = chat.chat_id
        attachment_manager = mock.Mock()
        attachment_manager.ensure_remote_reference.return_value = new_reference
        self.subject.remote_attachment_manager = attachment_manager
        messages: list[object] = [{
            "role": "user",
            "content": [{"type": "file", "file_id": "file-api-old"}],
        }]
        with mock.patch.object(self.subject.chat_manager, "save"):
            repaired = self.subject._repair_deepseek_file_references(
                messages,
                service,
            )
        content = repaired[0]["content"]
        self.assertIsInstance(content, list)
        assert isinstance(content, list)
        self.assertEqual(content[0]["file_id"], "file-api-new")
        attachment_manager.invalidate_remote_reference.assert_called_once_with(
            attachment,
            service,
        )
        attachment_manager.submit_reconcile_and_cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
