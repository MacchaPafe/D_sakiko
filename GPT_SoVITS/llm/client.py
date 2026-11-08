"""只返回完整结果的统一请求入口。"""
from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Awaitable, Callable

from .errors import LLMError, normalize_error
from .types import CancellationToken, LLMConfig, LLMRequest, LLMResult


async def _request(request: LLMRequest, config: LLMConfig) -> LLMResult:
    """按协议选择内部实现，不负责工具执行或对话编排。"""
    from .capabilities import prepare_request
    request, diagnostics = prepare_request(request, config)
    if config.route == "chatgpt-subscription":
        from .chatgpt.runtime import execute
    elif config.protocol == "responses":
        from .adapters.responses import execute
    elif config.protocol == "chat-completions":
        from .adapters.chat_completions import execute
    else:
        raise LLMError("unsupported_protocol", "未知生成协议：" + config.protocol)
    result = await execute(request, config)
    result.diagnostics.extend(diagnostics)
    return result


async def _run(request: LLMRequest, config: LLMConfig, cancel: CancellationToken,
               transport: Callable[[LLMRequest, LLMConfig], Awaitable[LLMResult]]) -> LLMResult:
    """在完整请求之间执行现有魔搭重试策略，并及时结束取消等待。"""
    attempt = 0
    while True:
        if cancel.cancelled:
            raise LLMError("cancelled", "生成已停止。")
        task = asyncio.ensure_future(transport(request, config))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.05)
                if cancel.cancelled and not task.done():
                    raise LLMError("cancelled", "生成已停止。")
            return task.result()
        except Exception as exc:
            error = normalize_error(exc)
            if config.api_key:
                error.args = (str(error).replace(config.api_key, "[已隐藏凭据]"),)
            if config.provider == "modelscope" and error.code == "rate_limit":
                from llm_provider.modelscope import (classify_rate_limit, extract_error_headers,
                    parse_quota_headers, parse_retry_after_seconds, ModelScopeRateLimitKind,
                    ModelScopeQuotaExceededError)
                headers = extract_error_headers(exc)
                quota = parse_quota_headers(headers)
                kind = classify_rate_limit(quota)
                if kind != ModelScopeRateLimitKind.TEMPORARY_RATE_LIMIT:
                    raise ModelScopeQuotaExceededError(kind, quota, exc) from exc
                if attempt < 2:
                    delay = min(5.0, max(0.0, parse_retry_after_seconds(headers) or float(2 ** attempt)))
                    attempt += 1
                    deadline = asyncio.get_running_loop().time() + delay
                    while asyncio.get_running_loop().time() < deadline:
                        if cancel.cancelled:
                            raise LLMError("cancelled", "生成已停止。")
                        await asyncio.sleep(0.05)
                    continue
            raise error from exc
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def generate(request: LLMRequest, config: LLMConfig,
             cancel: CancellationToken | None = None, *,
             transport: Callable[[LLMRequest, LLMConfig], Awaitable[LLMResult]] | None = None) -> LLMResult:
    """生成一份完整结果；可注入完整请求实现以离线验证取消和错误。"""
    token = cancel or CancellationToken()
    operation = transport or _request
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run(request, config, token, operation))
    # 界面/服务调用者可能已经拥有事件循环，不能在同一线程嵌套运行它。
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_run(request, config, token, operation))).result()
