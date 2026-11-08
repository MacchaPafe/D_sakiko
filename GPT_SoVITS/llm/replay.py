"""与 Electron 共用的供应商协议材料及来源校验。"""
from __future__ import annotations

import hashlib
import json
from .types import LLMConfig


def fingerprint(value: object) -> str:
    """生成稳定内容摘要，不依赖字典键顺序。"""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def origin(config: LLMConfig) -> dict[str, object]:
    """保存连接作用域摘要，不保存地址凭据或 API Key。"""
    return {"connectionId": fingerprint(config.connection_identity) if config.connection_identity else fingerprint(config.api_key), "modelId": config.model,
            "provider": config.provider, "protocol": config.protocol,
            "endpoint": fingerprint(config.base_url)}


def history_fingerprint(messages: list[dict[str, object]]) -> str:
    """计算不含协议副本的请求历史摘要，避免副本形成递归。"""
    return fingerprint([{key: value for key, value in message.items()
                         if not key.startswith("_") and key not in {"reasoning_content", "reasoning_details"}
                         and not (message.get("role") == "tool" and key == "name")}
                        for message in messages])


def make_replay(config: LLMConfig, messages: list[dict[str, object]],
                content: list[dict[str, object]], options: dict[str, object] | None = None) -> dict[str, object]:
    """只保存完整响应的白名单协议内容，供两种请求后端使用。"""
    return {"version": 1, "origin": origin(config), "content": content,
            "providerOptions": options or {}, "projectionFingerprint": "",
            "contextFingerprint": history_fingerprint(messages)}


def compatible(replay: object, config: LLMConfig, prefix: list[dict[str, object]]) -> bool:
    """仅在连接和此前输入一致时复用协议内容。"""
    return (isinstance(replay, dict) and replay.get("origin") == origin(config)
            and replay.get("contextFingerprint") == history_fingerprint(prefix)
            and replay.get("invalidated") is not True)
