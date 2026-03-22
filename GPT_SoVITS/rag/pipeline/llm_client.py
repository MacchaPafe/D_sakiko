"""第一阶段 LLM 调用封装。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(slots=True)
class LiteLLMConfig:
    """调用 litellm 所需的最小配置。"""

    model: str
    temperature: float = 0.1
    max_tokens: int | None = None
    retries: int = 2
    extra_completion_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LiteLLMJsonResponse:
    """表示一次 litellm JSON 调用结果。"""

    raw_content: str
    parsed_payload: dict[str, Any]


class LiteLLMJsonClient:
    """对 litellm 的最小 JSON 调用封装。"""

    def __init__(self, config: LiteLLMConfig) -> None:
        self.config = config

    def complete_json(
        self,
        prompt: str,
        stream: bool = False,
        status_callback: Callable[[str], None] | None = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> LiteLLMJsonResponse:
        """发送 prompt 并解析为 JSON。"""

        from litellm import completion

        last_error: Exception | None = None
        for attempt_index in range(self.config.retries + 1):
            try:
                if status_callback is not None:
                    status_callback(
                        "正在向 LLM 发送请求（attempt {}/{}）".format(
                            attempt_index + 1, self.config.retries + 1
                        )
                    )
                if stream:
                    response = completion(
                        model=self.config.model,
                        temperature=self.config.temperature,
                        max_tokens=self.config.max_tokens,
                        messages=[{"role": "user", "content": prompt}],
                        stream=True,
                        **self.config.extra_completion_kwargs,
                    )
                    content_parts: list[str] = []
                    for chunk in response:
                        delta_text = _extract_chunk_text(chunk)
                        if not delta_text:
                            continue
                        content_parts.append(delta_text)
                        if stream_callback is not None:
                            stream_callback(delta_text)
                    content = "".join(content_parts)
                else:
                    response = completion(
                        model=self.config.model,
                        temperature=self.config.temperature,
                        max_tokens=self.config.max_tokens,
                        messages=[{"role": "user", "content": prompt}],
                        **self.config.extra_completion_kwargs,
                    )
                    content = _extract_completion_content(response)

                if status_callback is not None:
                    status_callback("LLM 响应接收完成，开始解析 JSON")
                return LiteLLMJsonResponse(raw_content=content, parsed_payload=_parse_json_payload(content))
            except Exception as exc:  # pragma: no cover - 真实 API 调用路径
                last_error = exc
                if status_callback is not None:
                    status_callback("本次请求失败：{}: {}".format(type(exc).__name__, exc))
        assert last_error is not None
        raise last_error


def _parse_json_payload(content: str) -> dict[str, Any]:
    """从 LLM 输出中解析 JSON。"""

    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return json.loads(stripped)


def _extract_completion_content(response: Any) -> str:
    """从非流式 litellm 响应中提取完整文本。"""

    try:
        content = response.choices[0].message.content
    except Exception as exc:  # pragma: no cover - 防御式逻辑
        raise TypeError("无法从 LLM 响应中读取 message.content") from exc
    if not isinstance(content, str):
        raise TypeError("LLM 返回的 content 不是字符串")
    return content


def _extract_chunk_text(chunk: Any) -> str:
    """从流式 chunk 中尽量鲁棒地提取增量文本。"""

    if chunk is None:
        return ""

    if isinstance(chunk, dict):
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        return content if isinstance(content, str) else ""

    choices = getattr(chunk, "choices", None)
    if not choices:
        return ""

    first_choice = choices[0]
    delta = getattr(first_choice, "delta", None)
    if delta is None and isinstance(first_choice, dict):
        delta = first_choice.get("delta")

    if delta is None:
        return ""

    if isinstance(delta, dict):
        content = delta.get("content")
        return content if isinstance(content, str) else ""

    content = getattr(delta, "content", None)
    return content if isinstance(content, str) else ""
