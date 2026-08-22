from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path


_RUN_INTEGRATION = os.environ.get("RUN_DEEPSEEK_FILES_API_TESTS") == "1"
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@unittest.skipUnless(_RUN_INTEGRATION, "需要显式启用 DeepSeek Files API 集成测试")
class DeepSeekFilesIntegrationTestCase(unittest.TestCase):
    """使用独立环境变量验证真实 DeepSeek Files 与视觉补全。"""

    def test_upload_and_reference_tiny_image(self) -> None:
        """上传一小时过期的小图，并通过 file part 完成一次视觉请求。"""
        import litellm
        from openai import OpenAI

        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        api_base = os.environ.get(
            "DEEPSEEK_API_BASE",
            "https://api.deepseek.com",
        ).strip()
        if not api_key:
            self.skipTest("未设置 DEEPSEEK_API_KEY")

        file_id = ""
        with tempfile.TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "tiny.png"
            image_path.write_bytes(_TINY_PNG)
            with image_path.open("rb") as image_file:
                uploaded = litellm.create_file(
                    file=("tiny.png", image_file, "image/png"),
                    purpose="user_data",
                    expires_after={"anchor": "created_at", "seconds": 3600},
                    custom_llm_provider="openai",
                    api_base=api_base,
                    api_key=api_key,
                )
            file_id = str(getattr(uploaded, "id", "") or "")
            self.assertTrue(file_id.startswith("file-"))

            client = OpenAI(api_key=api_key, base_url=api_base, max_retries=0)
            response = client.chat.completions.create(
                model="deepseek-v4-flash-vision-exp",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "简短描述这张图片。"},
                        {"type": "file", "file_id": file_id},
                    ],
                }],
                stream=False,
            )
            self.assertTrue(response.choices)
            self.assertTrue(response.choices[0].message.content)

            try:
                client.files.delete(file_id)
            except Exception:
                # DeepSeek 若暂不支持删除，该测试文件会在一小时后自然过期。
                pass


if __name__ == "__main__":
    unittest.main()
