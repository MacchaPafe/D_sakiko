"""订阅路由、原生 OAuth 和凭据生命周期的离线行为验证。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from llm.adapters.responses import prepare_body
from llm.chatgpt.session import ChatGPTSession, ISSUER, RESOURCE, TOKEN
from llm.chatgpt.storage import Account, AccountStore, Tokens
from llm.errors import LLMError, response_error
from llm.replay import origin
from llm.types import CancellationToken, LLMConfig, LLMRequest


class MemoryVault:
    """只保存在测试内存的系统凭据库替身。"""

    def __init__(self, *, fail: bool = False) -> None:
        """可模拟凭据库不可用，不读写实际钥匙串。"""
        self.values: dict[str, str] = {}
        self.fail = fail

    def get_password(self, service: str, username: str) -> str | None:
        """按注册读取内存秘密。"""
        return self.values.get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        """写入失败用来覆盖静默文件降级。"""
        if self.fail:
            raise RuntimeError("vault unavailable")
        self.values[username] = password

    def delete_password(self, service: str, username: str) -> None:
        """移除内存秘密。"""
        self.values.pop(username, None)


def account(store: AccountStore) -> Account:
    """创建具备订阅权限且没有真实凭据的测试连接。"""
    import uuid
    value = Account(uuid.uuid4().hex, client_id="oaiapp_test", email="same@example.invalid",
                    issuer=ISSUER, subject="subject", scopes=["chatgpt.tokens.use.direct"], status="connected")
    store.save_tokens(value, Tokens("access", "refresh", "identity", time.time() + 3600))
    return value


def test_subscription_policy_isolated_from_normal_responses() -> None:
    """同一请求的两条路由分别保留或省略偏好，功能要求保持完整。"""
    request = LLMRequest([{"role": "system", "content": "设定"}, {"role": "user", "content": "问题"}],
        tools=[{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
        options={"temperature": 0.8, "top_p": 0.9, "max_tokens": 100,
                 "response_format": {"type": "json_object"}})
    normal = LLMConfig("openai", "model", "key", RESOURCE, "responses")
    ordinary, _ = prepare_body(request, normal)
    sharing, diagnostics = prepare_body(request, replace(normal, route="chatgpt-subscription"))
    assert ordinary["temperature"] == 0.8 and ordinary["max_output_tokens"] == 100
    assert not {"temperature", "top_p", "max_output_tokens"} & sharing.keys()
    assert len(diagnostics) == 3 and request.options["temperature"] == 0.8
    assert sharing["tools"][0]["type"] == "namespace"
    assert sharing["tools"][0]["tools"][0]["name"] == "lookup"
    assert sharing["text"] == ordinary["text"] == {"format": {"type": "json_object"}}
    assert sharing["input"][0]["role"] == "developer"
    assert sharing["store"] is False and sharing["stream"] is True


def test_unsupported_functionality_not_silently_dropped() -> None:
    """功能要求不允许藏在 extra_body 中绕过路由校验。"""
    config = LLMConfig("chatgpt", "model", protocol="responses", route="chatgpt-subscription")
    with pytest.raises(LLMError, match="metadata"):
        prepare_body(LLMRequest([], options={"extra_body": {"metadata": {"needed": True}}}), config)
    with pytest.raises(LLMError, match="本地执行"):
        prepare_body(LLMRequest([], options={"tools": [{"type": "web_search"}]}), config)


def test_refresh_keeps_replay_origin_but_account_switch_does_not() -> None:
    """短期令牌轮换不破坏推理连续性，不同注册仍严格隔离。"""
    config = LLMConfig("chatgpt", "model", "old", RESOURCE, "responses", connection_identity="registration-A")
    assert origin(config) == origin(replace(config, api_key="new"))
    assert origin(config) != origin(replace(config, connection_identity="registration-B"))


def test_vault_fallback_and_restart_never_reads_stale_keyring(tmp_path: Path) -> None:
    """文件降级后固定权威位置，重启不会读到原钥匙串中的旧刷新令牌。"""
    vault = MemoryVault()
    store = AccountStore(tmp_path, vault)
    value = account(store)
    host = store.host_id()
    vault.fail = True
    store.save_tokens(value, Tokens("new-access", "new-refresh", "new-id", time.time() + 3600))
    assert value.credential_store == "file"
    restarted = AccountStore(tmp_path, vault)
    loaded = restarted.get(value.id)
    assert restarted.host_id() == host
    assert restarted.tokens(loaded).refresh_token == "new-refresh"
    assert "new-refresh" not in (tmp_path / "accounts.json").read_text()
    if os.name != "nt":
        assert (tmp_path / "credentials" / (value.id + ".json")).stat().st_mode & 0o777 == 0o600


def test_account_usage_lease_prevents_logout(tmp_path: Path) -> None:
    """仍在生成的注册不能被配置程序移除，结束后可以退出。"""
    store = AccountStore(tmp_path, MemoryVault())
    value = account(store)
    lease = store.lease(value.id)
    try:
        with pytest.raises(LLMError, match="仍有生成任务"):
            store.assert_idle(value.id)
    finally:
        lease.close()
    store.assert_idle(value.id)


@pytest.mark.parametrize("mode", ["success", "exchange-failure", "identity-only", "wrong-nonce",
                                 "returning", "wrong-account"])
def test_native_oauth_callback_pkce_and_signed_identity(tmp_path: Path, mode: str) -> None:
    """真实本机回调配合内存 HTTP 服务验证 OAuth，不访问 OpenAI 或浏览器。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_numbers()

    def encode(value: int) -> str:
        """编码测试签名公钥。"""
        return base64.urlsafe_b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")).decode().rstrip("=")

    state: dict[str, str] = {}
    store = AccountStore(tmp_path, MemoryVault())
    returning = mode in {"returning", "wrong-account"}
    saved = account(store) if returning else None
    if saved is not None:
        saved.subject = "user-one"
        store.save_account(saved)
        store.clear_tokens(saved)
    host_id = store.host_id()

    def handle(request: httpx.Request) -> httpx.Response:
        """仅接受测试预期的令牌和公开身份密钥请求。"""
        if str(request.url) == TOKEN:
            form = parse_qs(request.content.decode())
            assert form["client_id"] == ["oaiapp_test"]
            assert store.list_accounts()[0].client_id == "oaiapp_test"
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest()).decode().rstrip("=")
            assert challenge == state["code_challenge"]
            if mode == "exchange-failure":
                return httpx.Response(400, json={"error": "invalid_grant"})
            identity = jwt.encode({"iss": ISSUER, "sub": "another-user" if mode == "wrong-account" else "user-one", "aud": "oaiapp_test",
                "nonce": state["nonce"] if mode != "wrong-nonce" else "wrong", "email": "user@example.invalid", "exp": int(time.time()) + 3600},
                key, algorithm="RS256", headers={"kid": "test"})
            return httpx.Response(200, json={"access_token": "fake-access",
                "refresh_token": "fake-refresh" if mode != "identity-only" else "",
                "id_token": identity, "scope": "openid" if mode == "identity-only" else "openid chatgpt.tokens.use.direct", "expires_in": 3600})
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": ISSUER + "/keys"})
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": [{"kty": "RSA", "kid": "test", "alg": "RS256",
                "n": encode(public.n), "e": encode(public.e)}]})
        raise AssertionError(str(request.url))

    def browser(url: str) -> None:
        """模拟浏览器接收授权 URL，并发回同一次登录的回调。"""
        state.update({name: values[0] for name, values in parse_qs(urlsplit(url).query).items()})
        assert state["client_id"] == ("oaiapp_test" if returning else "dynamic_agent_client")
        if returning:
            assert "agent_name_hint" not in state and "id_token_hint" not in state and "prompt" not in state
            assert state["login_hint"] == "same@example.invalid"
        else:
            assert state["agent_name_hint"] == "数字小祥"
        assert state["resource"] == RESOURCE

        async def callback() -> None:
            """通过真实 loopback 连接模拟成功重定向。"""
            fields = {"state": state["state"], "code": "test-code"}
            if not returning:
                fields["client_id"] = "oaiapp_test"
            target = state["redirect_uri"] + "?" + urlencode(fields)
            async with httpx.AsyncClient(trust_env=False) as client:
                assert (await client.get(target)).status_code == 200
        asyncio.create_task(callback())

    session = ChatGPTSession(store, lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    if mode in {"exchange-failure", "wrong-nonce", "wrong-account"}:
        with pytest.raises(LLMError):
            asyncio.run(session.sign_in(saved.id if saved else None, browser=browser, timeout=2))
        pending = store.list_accounts()[0]
        assert pending.client_id == "oaiapp_test" and not pending.credential_store
        return
    result = asyncio.run(session.sign_in(saved.id if saved else None, browser=browser, timeout=2))
    assert result.sharing is (mode != "identity-only") and result.subject == "user-one"
    assert store.tokens(result).refresh_token == ("fake-refresh" if mode != "identity-only" else "")
    assert len(store.list_accounts()) == 1 and store.host_id() == host_id
    if saved is not None:
        assert result.id == saved.id
    assert "fake-access" not in json.dumps([vars(item) for item in store.list_accounts()])


def test_expiry_refresh_is_serialized_and_rotates_credentials(tmp_path: Path) -> None:
    """并发请求只刷新一次，之后读取最新完整令牌集合。"""
    store = AccountStore(tmp_path, MemoryVault())
    value = account(store)
    store.save_tokens(value, Tokens("expired", "refresh-old", "identity", time.time() - 1))
    calls: list[int] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        """延迟刷新响应，让第二个请求确实需要等待锁。"""
        calls.append(1)
        await asyncio.sleep(0.03)
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "refresh-new", "expires_in": 3600})

    session = ChatGPTSession(store, lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)))

    async def requests() -> list[tuple[Account, Tokens]]:
        """同一个会话同时进入两个完整请求。"""
        return list(await asyncio.gather(session.access(value.id), session.access(value.id)))
    results = asyncio.run(requests())
    assert len(calls) == 1
    assert all(tokens.refresh_token == "refresh-new" for _, tokens in results)


def test_error_shapes_and_usage_action_preserved() -> None:
    """准入 detail 和订阅额度错误不被统一误报为过期。"""
    error = response_error(503, {"detail": "routing unavailable"}, "req-1")
    assert error.request_id == "req-1" and "routing unavailable" in error.body
    quota = response_error(429, {"error": {"code": "subscription_sharing_usage_limit_exceeded"}})
    assert quota.code == "quota" and quota.action == "manage_usage"
    from llm.chatgpt.session import checked_json
    request = httpx.Request("POST", TOKEN, data={"refresh_token": "secret-refresh"})
    with pytest.raises(LLMError) as caught:
        checked_json(httpx.Response(400, request=request, json={"detail": "bad secret-refresh"}))
    assert "secret-refresh" not in str(caught.value) + caught.value.body


@pytest.mark.parametrize("terminal", [False, True])
def test_refresh_failure_only_discards_confirmed_unusable_tokens(tmp_path: Path, terminal: bool) -> None:
    """临时认证基础设施故障保留凭据，确认撤销才清理并保留注册身份。"""
    store = AccountStore(tmp_path, MemoryVault())
    value = account(store)
    store.save_tokens(value, Tokens("old-access", "old-refresh", "old-id", time.time() - 1))

    def handle(request: httpx.Request) -> httpx.Response:
        """模拟短暂服务错误和明确的不可用刷新令牌。"""
        return (httpx.Response(400, json={"error": "refresh_token_reused"}) if terminal
                else httpx.Response(503, json={"detail": "temporarily unavailable"}))

    session = ChatGPTSession(store, lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    with pytest.raises(LLMError):
        asyncio.run(session.access(value.id))
    saved = store.get(value.id)
    assert saved.client_id == value.client_id
    if terminal:
        assert saved.status == "needs-login" and not saved.credential_store
    else:
        assert saved.sharing and store.tokens(saved).refresh_token == "old-refresh"


def test_signout_disables_account_when_remote_and_vault_cleanup_fail(tmp_path: Path) -> None:
    """撤销失败和钥匙串不可写时仍停用本地账户，且明确报告清理结果。"""
    vault = MemoryVault()
    store = AccountStore(tmp_path, vault)
    value = account(store)
    session = ChatGPTSession(store, lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(503, json={"detail": "offline"}))))
    with patch.object(vault, "delete_password", side_effect=RuntimeError("locked")):
        warning = asyncio.run(session.sign_out(value.id))
    saved = store.get(value.id)
    assert saved.status == "signed-out" and not saved.credential_store
    assert "远端撤销" in warning and "凭据库" in warning
    with pytest.raises(LLMError):
        store.tokens(saved)


@pytest.mark.parametrize("failure", ["admission", "quota", "capability"])
def test_actual_sdk_preserves_admission_and_sse_errors(failure: str) -> None:
    """通过真实 SDK 和模拟 HTTP 验证错误形状、实际状态及请求标识。"""
    from openai import AsyncOpenAI
    from llm.adapters.responses import execute

    def handle(request: httpx.Request) -> httpx.Response:
        """额度故障可发生在流建立后，不能提前当成成功。"""
        if failure == "admission":
            return httpx.Response(503, json={"detail": "routing unavailable"}, headers={"x-request-id": "req-test"})
        event = ({"type": "response.failed", "sequence_number": 1,
                  "response": {"id": "r1", "status": "failed", "output": [],
                               "error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limited"}}}
                 if failure == "quota" else
                 {"type": "error", "sequence_number": 1, "code": "subscription_sharing_unsupported_capability",
                  "message": "unsupported", "param": "tools"})
        return httpx.Response(200, headers={"Content-Type": "text/event-stream", "x-request-id": "req-test"},
                              text="event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n")

    def client(**kwargs: object) -> AsyncOpenAI:
        """不访问真实服务，仍经过 SDK 的请求和事件解析。"""
        return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))

    config = LLMConfig("chatgpt", "model", "test-access", RESOURCE, "responses", route="chatgpt-subscription")
    with patch("openai.AsyncOpenAI", side_effect=client), pytest.raises(LLMError) as caught:
        asyncio.run(execute(LLMRequest([]), config))
    assert caught.value.status_code == (503 if failure == "admission" else 200)
    assert caught.value.request_id == "req-test"
    assert caught.value.action == ("retry_later" if failure == "admission" else "manage_usage" if failure == "quota" else "")
    if failure == "capability":
        assert caught.value.param == "tools"


@pytest.mark.parametrize('failure', ['expired', 'quota'])
def test_subscription_runtime_recovery_and_headers(tmp_path: Path, failure: str) -> None:
    """仅令牌失效可重试一次；使用自己的关联头且保持稳定连接身份。"""
    from unittest.mock import AsyncMock
    from llm.chatgpt.runtime import execute
    from llm.types import LLMResult
    store = AccountStore(tmp_path, MemoryVault())
    value = account(store)

    def handle(request: httpx.Request) -> httpx.Response:
        """模拟刷新，不涉及真实账户。"""
        return httpx.Response(200, json={'access_token': 'renewed', 'refresh_token': 'renewed-refresh', 'expires_in': 3600})

    session = ChatGPTSession(store, lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    session.cached_models = lambda identifier: [{'slug': 'model'}]
    error = (LLMError('authentication', 'expired', status_code=401, provider_code='invalid_token')
             if failure == 'expired' else response_error(429, {'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}))
    transport = AsyncMock(side_effect=[error, LLMResult(text='完成')])
    config = LLMConfig('chatgpt', 'model', base_url=RESOURCE, protocol='responses',
                       route='chatgpt-subscription', account_id=value.id)
    with patch('llm.chatgpt.runtime.ChatGPTSession', return_value=session), patch('llm.adapters.responses.execute', transport):
        if failure == 'quota':
            with pytest.raises(LLMError) as caught:
                asyncio.run(execute(LLMRequest([], conversation_id='chat-one'), config))
            assert caught.value.action == 'manage_usage'
            assert transport.await_count == 1
        else:
            assert asyncio.run(execute(LLMRequest([], conversation_id='chat-one'), config)).text == '完成'
            assert transport.await_count == 2
            first_request, first_config = transport.call_args_list[0].args
            second_request, second_config = transport.call_args_list[1].args
            first = first_request.options['_response_headers']
            second = second_request.options['_response_headers']
            assert first['session-id'] == second['session-id'] == 'chat-one'
            assert first['User-Agent'].startswith('D_sakiko/')
            assert first['x-client-request-id'] != second['x-client-request-id']
            assert first_config.api_key == 'access' and second_config.api_key == 'renewed'
            assert origin(first_config) == origin(second_config)
    store.assert_idle(value.id)
