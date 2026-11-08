"""把订阅账户转换为一次生成所需的连接，不向业务层返回令牌。"""
from __future__ import annotations

import uuid
from dataclasses import replace

from ..errors import LLMError, normalize_error, redact_error
from ..types import LLMConfig, LLMRequest, LLMResult
from .session import ChatGPTSession, RESOURCE, session_lock, user_agent
from .storage import AccountLease


async def execute(request: LLMRequest, config: LLMConfig) -> LLMResult:
    """验证路由与账户目录，且只对明确令牌失效执行一次认证恢复。"""
    from ..adapters.responses import execute as response_execute, prepare_body
    from ..response_routes import validate_subscription_model
    if not config.account_id:
        raise LLMError("authentication", "请先在配置程序中选择 ChatGPT 账户。")
    if config.base_url.rstrip("/") != RESOURCE:
        raise LLMError("authentication", "ChatGPT 订阅只允许使用官方公共 Responses 地址。")
    session = ChatGPTSession()
    async with session_lock(session.store.session_lock(config.account_id)):
        lease = AccountLease(session.store, config.account_id, locked=True)
    secrets: list[str] = []
    try:
        account, tokens = await session.access(config.account_id)
        secrets.extend((tokens.access_token, tokens.refresh_token, tokens.id_token))
        models = session.cached_models(account.id) or await session.models(account.id)
        model = next((item for item in models if item.get("slug") == config.model), None)
        if model is None:
            raise LLMError("model_unavailable", "当前账户不支持所选 ChatGPT 模型，请在配置程序中手动调整。")
        connection = replace(config, api_key=tokens.access_token, connection_identity=account.identity)
        context = request.conversation_id or str(uuid.uuid4())
        headers = {"User-Agent": user_agent(), "session-id": context, "thread-id": context,
                   "x-client-request-id": str(uuid.uuid4())}
        prepared = replace(request, options={**request.options, "_response_headers": headers})
        body = prepare_body(prepared, connection)
        validate_subscription_model(body[0], model)
        try:
            return await response_execute(prepared, connection, prepared=body)
        except Exception as exc:
            error = normalize_error(exc)
            if error.status_code != 401 or error.provider_code not in {"invalid_token", "expired_token", "token_expired"}:
                raise error from None
            account, refreshed = await session.access(config.account_id, rejected_token=tokens.access_token)
            secrets.extend((refreshed.access_token, refreshed.refresh_token, refreshed.id_token))
            headers = {**headers, "x-client-request-id": str(uuid.uuid4())}
            return await response_execute(replace(prepared, options={**prepared.options, "_response_headers": headers}),
                replace(connection, api_key=refreshed.access_token, connection_identity=account.identity), prepared=body)
    except Exception as exc:
        raise redact_error(normalize_error(exc), secrets) from None
    finally:
        lease.close()
