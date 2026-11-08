"""浏览器 OAuth、身份验证、受保护刷新及账户模型目录。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
import webbrowser
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from filelock import FileLock, Timeout

from ..errors import LLMError, response_error, redact_error
from ..types import CancellationToken
from .storage import Account, AccountStore, Tokens, write_private_json

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
USAGE_URL = "https://chatgpt.com/settings/usage"


def user_agent() -> str:
    """如实标识数字小祥，不冒用其他客户端名称。"""
    try:
        current = version("d-sakiko")
    except PackageNotFoundError:
        current = "development"
    return "D_sakiko/" + current


@asynccontextmanager
async def session_lock(lock: FileLock) -> AsyncIterator[None]:
    """等待跨进程锁时仍可取消，不在事件循环中阻塞数十秒。"""
    deadline = time.monotonic() + 35
    while True:
        try:
            lock.acquire(timeout=0)
            break
        except Timeout:
            if time.monotonic() >= deadline:
                raise LLMError("authentication", "另一个进程正在更新登录状态，请稍后重试。")
            await asyncio.sleep(0.05)
    try:
        yield
    finally:
        lock.release()


def checked_json(response: httpx.Response) -> dict[str, object]:
    """认证及目录错误保留原状态和诊断，不记录请求凭据。"""
    try:
        data = response.json()
    except ValueError:
        data = {"detail": response.text[:4000]}
    if not response.is_success:
        error = response_error(response.status_code, data, response.headers.get("x-request-id", ""))
        request = response.request
        secrets = [request.headers.get("Authorization", "").removeprefix("Bearer ")]
        if "application/x-www-form-urlencoded" in request.headers.get("Content-Type", ""):
            fields = parse_qs(request.content.decode("utf-8"))
            for name in ("code", "code_verifier", "refresh_token", "token"):
                secrets.extend(fields.get(name, []))
        raise redact_error(error, secrets) from None
    if not isinstance(data, dict):
        raise LLMError("invalid_response", "认证或模型目录返回了无效的数据结构。")
    return data


class ChatGPTSession:
    """对外只暴露账户操作和可用连接，隐藏 OAuth 和令牌轮换细节。"""

    def __init__(self, store: AccountStore | None = None,
                 client_factory: Callable[[], httpx.AsyncClient] | None = None) -> None:
        """注入 HTTP 和存储以测试完整流程，默认保留系统代理配置。"""
        self.store = store or AccountStore()
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(
            timeout=40, headers={"User-Agent": user_agent()}, follow_redirects=False))

    async def verify_identity(self, token: str, client_id: str, client: httpx.AsyncClient,
                              nonce: str | None = None) -> dict[str, object]:
        """用发布的 JWKS 验证签名、签发者、受众、有效期及本次随机值。"""
        import jwt
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in {"RS256", "ES256"}:
                raise ValueError("unsupported signature")
            discovery = checked_json(await client.get(ISSUER + "/.well-known/openid-configuration"))
            if discovery.get("issuer") != ISSUER:
                raise ValueError("unexpected issuer")
            jwks_url = str(discovery.get("jwks_uri", ""))
            if urlsplit(jwks_url).scheme != "https" or urlsplit(jwks_url).hostname != "auth.openai.com":
                raise ValueError("unexpected JWKS endpoint")
            jwks = checked_json(await client.get(jwks_url))
            keys = jwks.get("keys", [])
            key_data = next(key for key in keys if isinstance(key, dict) and key.get("kid") == header.get("kid"))
            key = jwt.PyJWK.from_dict(key_data, algorithm=header["alg"])
            claims = jwt.decode(token, key.key, algorithms=[header["alg"]], issuer=ISSUER,
                                audience=client_id, options={"require": ["exp", "iss", "aud", "sub"]}, leeway=30)
            if not isinstance(claims.get("sub"), str) or not claims["sub"]:
                raise ValueError("missing subject")
            if nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
                raise ValueError("nonce mismatch")
            return claims
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError("authentication", "ChatGPT 身份签名或登录状态校验失败，请重新登录。") from exc

    async def sign_in(self, account_id: str | None = None, *, cancel: CancellationToken | None = None,
                      on_url: Callable[[str], None] | None = None,
                      browser: Callable[[str], object] = webbrowser.open,
                      timeout: float = 180) -> Account:
        """先建立回调监听再打开浏览器，成功状态立即保存且不依赖配置保存按钮。"""
        token = cancel or CancellationToken()
        account = self.store.get(account_id) if account_id else Account(uuid.uuid4().hex)
        host = self.store.host_id()
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        future: asyncio.Future[dict[str, str]] = asyncio.get_running_loop().create_future()
        hint = ""
        if account.status == "connected":
            try:
                hint = self.store.tokens(account).id_token
            except LLMError:
                # 凭据库不可用时仍允许重新授权，不能依赖旧秘密才能修复登录。
                pass

        async def receive(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            """只接受当前尝试的回调，不把授权码或回调地址写入日志。"""
            status, message = "400 Bad Request", "登录回调无效，请返回配置程序重试。"
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
                first = head.split(b"\r\n", 1)[0].decode("ascii")
                method, target, _ = first.split(" ", 2)
                path = urlsplit(target)
                query = parse_qs(path.query)
                returned = query.get("state", [""])
                if method == "GET" and path.path == "/auth/callback" and len(returned) == 1 and hmac.compare_digest(returned[0], state):
                    values = {key: value[0] for key, value in query.items() if len(value) == 1}
                    if not future.done():
                        future.set_result(values)
                    status, message = "200 OK", "授权结果已收到，请返回数字小祥配置程序。"
                body = message.encode("utf-8")
                writer.write((f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                              f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n").encode() + body)
                await writer.drain()
            except (ValueError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass

        server = await asyncio.start_server(receive, "127.0.0.1", 0, limit=16384)
        redirect = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/auth/callback"
        params = {"client_id": account.client_id or "dynamic_agent_client", "ext_agent_host_id": host,
                  "response_type": "code", "redirect_uri": redirect, "scope": SCOPES, "resource": RESOURCE,
                  "state": state, "nonce": nonce, "code_challenge_method": "S256", "code_challenge": challenge}
        if not account.client_id:
            params["agent_name_hint"] = "数字小祥"
        else:
            if hint:
                params["id_token_hint"] = hint
            if account.email:
                params["login_hint"] = account.email
            if account.subject and "chatgpt.tokens.use.direct" not in account.scopes:
                # 已授权账户退出后走普通登录；只对曾拒绝订阅权限的身份重新请求同意。
                params["prompt"] = "consent"
        url = AUTHORIZE + "?" + urlencode(params)
        try:
            if on_url is not None:
                on_url(url)
            try:
                browser(url)
            except Exception:
                # 无浏览器启动器不取消监听，用户仍可使用复制的登录链接。
                pass
            deadline = time.monotonic() + timeout
            while not future.done():
                if token.cancelled:
                    raise LLMError("cancelled", "登录已取消。")
                if time.monotonic() >= deadline:
                    raise LLMError("timeout", "登录等待超时，请检查本机回调是否被代理拦截后重试。")
                await asyncio.wait({future}, timeout=0.05)
            callback = future.result()
        finally:
            server.close()
            await server.wait_closed()
            if not future.done():
                future.cancel()
        if callback.get("error"):
            raise LLMError("authentication", "ChatGPT 登录未完成：" + callback["error"])
        client_id = callback.get("client_id") or account.client_id
        if not client_id or client_id == "dynamic_agent_client" or not callback.get("code"):
            raise LLMError("authentication", "授权回调缺少注册 Client ID 或授权码。")
        if account.client_id and account.client_id != client_id:
            raise LLMError("authentication", "返回的注册身份与选中的连接不一致。")
        account.client_id = client_id
        if not account_id:
            self.store.save_account(account)  # 换码失败也保留已签发注册，不重复动态注册。
        async with session_lock(self.store.session_lock(account.id)):
            if account_id:
                self.store.assert_idle(account.id)
                # 浏览器停留期间，另一进程可能已刷新并降级凭据位置；不能写回旧快照。
                account = self.store.get(account.id)
                if account.client_id != client_id:
                    raise LLMError("authentication", "登录期间所选注册已经改变，请重新开始。")
            async with self.client_factory() as client:
                data = checked_json(await client.post(TOKEN, data={"grant_type": "authorization_code",
                    "client_id": client_id, "code": callback["code"], "code_verifier": verifier,
                    "redirect_uri": redirect, "resource": RESOURCE}))
                id_token = str(data.get("id_token") or "")
                claims = await self.verify_identity(id_token, client_id, client, nonce)
                if account.subject and (account.subject != claims["sub"] or account.issuer != claims["iss"]):
                    raise LLMError("authentication", "重新登录的身份不属于所选连接，请使用“添加账户”。")
                account.subject, account.issuer = str(claims["sub"]), str(claims["iss"])
                account.email = str(claims.get("email") or "")
                account.scopes = str(data.get("scope") or "").split()
                account.status = "connected"
                self.store.save_tokens(account, self._tokens(data, id_token=id_token, require_refresh=account.sharing))
        return account

    def _tokens(self, data: dict[str, object], *, id_token: str = "", previous: Tokens | None = None,
                require_refresh: bool = True) -> Tokens:
        """把完整轮换响应作为一个集合保存，不丢弃替换的刷新令牌。"""
        access = str(data.get("access_token") or "")
        refresh = str(data.get("refresh_token") or (previous.refresh_token if previous else ""))
        if require_refresh and (not access or not refresh):
            raise LLMError("authentication", "登录服务没有返回完整的可刷新凭据。")
        return Tokens(access, refresh, id_token or str(data.get("id_token") or (previous.id_token if previous else "")),
                      time.time() + float(data.get("expires_in") or 3600))

    async def access(self, identifier: str, *, rejected_token: str | None = None) -> tuple[Account, Tokens]:
        """按需串行刷新；其他进程已经刷新时复用新集合，不重复轮换。"""
        async with session_lock(self.store.session_lock(identifier)):
            account = self.store.get(identifier)
            if not account.sharing:
                raise LLMError("authentication", "该连接未授权使用 ChatGPT 订阅，请在配置程序中登录或授权。")
            tokens = self.store.tokens(account)
            force = rejected_token is not None and hmac.compare_digest(rejected_token, tokens.access_token)
            if tokens.expires_at > time.time() + 60 and not force:
                return account, tokens
            async with self.client_factory() as client:
                try:
                    data = checked_json(await client.post(TOKEN, data={"grant_type": "refresh_token",
                        "client_id": account.client_id, "refresh_token": tokens.refresh_token, "resource": RESOURCE}))
                except LLMError as exc:
                    if exc.provider_code in {"invalid_grant", "invalid_refresh_token", "token_expired",
                                             "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}:
                        self.store.clear_tokens(account)
                        account.status = "needs-login"
                        self.store.save_account(account)
                    elif exc.provider_code == "invalid_client":
                        account.status = "needs-login"
                        self.store.save_account(account)
                    raise
                if data.get("id_token"):
                    claims = await self.verify_identity(str(data["id_token"]), account.client_id, client)
                    if claims["sub"] != account.subject:
                        raise LLMError("authentication", "刷新返回的账户身份不一致。")
                if "scope" in data:
                    account.scopes = str(data["scope"]).split()
                updated = self._tokens(data, previous=tokens)
                self.store.save_tokens(account, updated)
                if not account.sharing:
                    raise LLMError("permission", "ChatGPT 订阅使用权限已失效，请重新授权。")
                return account, updated

    async def models(self, identifier: str) -> list[dict[str, object]]:
        """使用所选账户实时发现模型，不能套用 API Key 或其他账户的目录。"""
        _, tokens = await self.access(identifier)
        async with self.client_factory() as client:
            data = checked_json(await client.get(RESOURCE + "/models", headers={"Authorization": "Bearer " + tokens.access_token}))
        entries = data.get("models", [])
        models = [item for item in entries if isinstance(item, dict) and item.get("visibility") == "list"
                  and item.get("supported_in_api", True)]
        write_private_json(self.store.directory / "models" / (identifier + ".json"),
                           {"saved_at": time.time(), "models": models})
        return models

    def cached_models(self, identifier: str) -> list[dict[str, object]]:
        """读取账户自己的目录缓存，刷新失败不伪造其他账户可用模型。"""
        path = self.store.directory / "models" / (identifier + ".json")
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data.get("models", [])
            return [item for item in entries if isinstance(item, dict) and isinstance(item.get("slug"), str)]
        except (OSError, ValueError, AttributeError, TypeError):
            # 目录属于可重建缓存，不允许其损坏阻止修复账户或重新登录。
            return []

    async def sign_out(self, identifier: str) -> str:
        """忙碌时拒绝注销；清理本地秘密并始终保留注册以供下次登录。"""
        async with session_lock(self.store.session_lock(identifier)):
            self.store.assert_idle(identifier)
            account = self.store.get(identifier)
            warning = ""
            if account.credential_store:
                try:
                    tokens = self.store.tokens(account)
                    async with self.client_factory() as client:
                        discovery = checked_json(await client.get(ISSUER + "/.well-known/openid-configuration"))
                        endpoint = str(discovery.get("revocation_endpoint") or "")
                        if urlsplit(endpoint).scheme != "https" or urlsplit(endpoint).hostname != "auth.openai.com":
                            raise LLMError("authentication", "注销端点校验失败。")
                        for attempt in range(3):
                            response = await client.post(endpoint, data={"token": tokens.refresh_token,
                                "token_type_hint": "refresh_token", "client_id": account.client_id})
                            if response.status_code < 500 or attempt == 2:
                                break
                            await asyncio.sleep(0.25 * (2 ** attempt))
                        if not response.is_success:
                            checked_json(response)
                except (LLMError, httpx.HTTPError):
                    warning = "已退出本地登录，但未能确认远端撤销；可在 ChatGPT 设置中断开数字小祥。"
            cleanup = self.store.clear_tokens(account)
            return " ".join(item for item in (warning, cleanup) if item)
