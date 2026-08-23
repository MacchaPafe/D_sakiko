from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Mapping, Sequence

import requests


OPENAI_CODEX_PROVIDER_ID = "openai_codex"
CODEX_API_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_CALLBACK_PORTS = (1455, 1457)
CODEX_MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex-spark",
)
DEFAULT_CODEX_MODEL = CODEX_MODELS[0]
TOKEN_REFRESH_MARGIN_SECONDS = 300


class CodexError(RuntimeError):
    """OpenAI Codex provider 的基础异常。"""


class CodexAuthenticationError(CodexError):
    """登录缺失、令牌过期或刷新失败。"""


class CodexRateLimitError(CodexError):
    """Codex 账号额度或速率受限。"""


class CodexPermissionError(CodexError):
    """当前账号无权访问请求的模型。"""


class CodexBadRequestError(CodexError):
    """Codex 拒绝了请求内容。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class CodexConnectionError(CodexError):
    """无法连接 OAuth 或 Codex 服务。"""


def _base64url_no_padding(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_pkce_pair() -> tuple[str, str]:
    """生成 OAuth PKCE verifier/challenge。"""
    verifier = _base64url_no_padding(secrets.token_bytes(64))
    challenge = _base64url_no_padding(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorization_url(redirect_uri: str, state: str, code_challenge: str) -> str:
    """构造与 Codex CLI 登录兼容的授权地址。"""
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CODEX_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email offline_access",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state": state,
            "originator": "d_sakiko",
        }
    )
    return f"{CODEX_AUTHORIZE_URL}?{query}"


def _decode_jwt_payload(token: object) -> dict[str, object]:
    if not isinstance(token, str) or token.count(".") < 2:
        return {}
    try:
        encoded = token.split(".", 2)[1]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_account_metadata(token_payload: Mapping[str, object]) -> dict[str, str]:
    auth_claim = token_payload.get("https://api.openai.com/auth")
    auth = auth_claim if isinstance(auth_claim, Mapping) else {}

    def first_string(*values: object) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    return {
        "account_id": first_string(
            auth.get("chatgpt_account_id"),
            token_payload.get("chatgpt_account_id"),
            token_payload.get("account_id"),
        ),
        "email": first_string(token_payload.get("email"), auth.get("email")),
        "plan_type": first_string(
            auth.get("chatgpt_plan_type"),
            token_payload.get("chatgpt_plan_type"),
            token_payload.get("plan_type"),
        ),
    }


def normalize_token_response(
    response_data: Mapping[str, object],
    *,
    previous_refresh_token: str = "",
    now: float | None = None,
) -> dict[str, object]:
    """把 OAuth token 响应转换为可持久化的稳定结构。"""
    access_token = str(response_data.get("access_token") or "").strip()
    if not access_token:
        raise CodexAuthenticationError("OAuth 响应缺少 access_token。")
    refresh_token = str(response_data.get("refresh_token") or previous_refresh_token or "").strip()
    id_token = str(response_data.get("id_token") or "").strip()
    try:
        expires_in = max(1, int(response_data.get("expires_in") or 3600))
    except (TypeError, ValueError):
        expires_in = 3600

    access_claims = _decode_jwt_payload(access_token)
    id_claims = _decode_jwt_payload(id_token)
    metadata = _extract_account_metadata({**id_claims, **access_claims})
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "expires_at": float(time.time() if now is None else now) + expires_in,
        **metadata,
    }


def load_codex_oauth_bundle() -> dict[str, object]:
    """从统一配置中读取最新 Codex OAuth 令牌。"""
    from qconfig import d_sakiko_config

    snapshot = d_sakiko_config.snapshot()
    raw = getattr(snapshot, "codex_oauth").value
    return dict(raw) if isinstance(raw, dict) else {}


def save_codex_oauth_bundle(bundle: Mapping[str, object]) -> None:
    """原子写入 Codex OAuth 令牌。"""
    from qconfig import d_sakiko_config

    d_sakiko_config.set(d_sakiko_config.codex_oauth, dict(bundle))


def clear_codex_oauth_bundle() -> None:
    save_codex_oauth_bundle({})


def codex_account_summary() -> dict[str, str]:
    bundle = load_codex_oauth_bundle()
    return {
        "account_id": str(bundle.get("account_id") or ""),
        "email": str(bundle.get("email") or ""),
        "plan_type": str(bundle.get("plan_type") or ""),
    }


class _OAuthCallbackServer:
    def __init__(self, state: str) -> None:
        self.state = state
        self.result: dict[str, str] = {}
        self.server: HTTPServer | None = None
        last_error: OSError | None = None

        callback_owner = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/auth/callback":
                    self.send_error(404)
                    return
                params = urllib.parse.parse_qs(parsed.query)
                received_state = (params.get("state") or [""])[0]
                if received_state != callback_owner.state:
                    callback_owner.result = {"error": "OAuth state 校验失败。"}
                    self._finish(400, "登录失败：state 校验失败，可以关闭此页面。")
                    return
                error = (params.get("error_description") or params.get("error") or [""])[0]
                code = (params.get("code") or [""])[0]
                if error:
                    callback_owner.result = {"error": error}
                    self._finish(400, "登录被取消或拒绝，可以关闭此页面。")
                    return
                if not code:
                    callback_owner.result = {"error": "OAuth 回调缺少授权码。"}
                    self._finish(400, "登录失败：缺少授权码，可以关闭此页面。")
                    return
                callback_owner.result = {"code": code}
                self._finish(200, "OpenAI Codex 登录成功，可以关闭此页面并返回 D_sakiko。")

            def _finish(self, status: int, message: str) -> None:
                body = (
                    "<!doctype html><meta charset='utf-8'>"
                    "<title>D_sakiko Codex</title>"
                    f"<body style='font-family:sans-serif;padding:40px'><h2>{message}</h2></body>"
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        for port in CODEX_CALLBACK_PORTS:
            try:
                self.server = HTTPServer(("127.0.0.1", port), CallbackHandler)
                self.server.timeout = 0.25
                self.port = port
                break
            except OSError as exc:
                last_error = exc
        else:
            raise CodexConnectionError(
                f"OAuth 回调端口 {CODEX_CALLBACK_PORTS} 均不可用。"
            ) from last_error

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.port}/auth/callback"

    def wait(self, cancel_event: threading.Event, timeout: float) -> str:
        assert self.server is not None
        deadline = time.monotonic() + timeout
        try:
            while not self.result and not cancel_event.is_set() and time.monotonic() < deadline:
                self.server.handle_request()
        finally:
            self.server.server_close()
        if cancel_event.is_set():
            raise CodexAuthenticationError("登录已取消。")
        if not self.result:
            raise CodexAuthenticationError("等待 OAuth 登录超时。")
        if self.result.get("error"):
            raise CodexAuthenticationError(self.result["error"])
        return self.result["code"]

    def close(self) -> None:
        """关闭尚未进入等待循环的回调服务器。"""
        if self.server is not None:
            self.server.server_close()


class CodexOAuthClient:
    """负责登录、刷新和提供当前可用的 Codex access token。"""

    _refresh_lock = threading.Lock()

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def sign_in(
        self,
        *,
        cancel_event: threading.Event | None = None,
        timeout: float = 300,
        open_browser: Callable[[str], object] = webbrowser.open,
    ) -> dict[str, object]:
        cancel_event = cancel_event or threading.Event()
        verifier, challenge = generate_pkce_pair()
        state = _base64url_no_padding(secrets.token_bytes(32))
        callback = _OAuthCallbackServer(state)
        auth_url = build_authorization_url(callback.redirect_uri, state, challenge)
        browser_opened = open_browser(auth_url)
        if browser_opened is False:
            callback.close()
            raise CodexConnectionError("未能打开 OAuth 登录页面。")
        code = callback.wait(cancel_event, timeout)
        token_data = self._post_token(
            {
                "grant_type": "authorization_code",
                "client_id": CODEX_CLIENT_ID,
                "code": code,
                "redirect_uri": callback.redirect_uri,
                "code_verifier": verifier,
            }
        )
        bundle = normalize_token_response(token_data)
        save_codex_oauth_bundle(bundle)
        return bundle

    def _post_token(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        try:
            response = self.session.post(CODEX_TOKEN_URL, data=dict(payload), timeout=30)
        except requests.RequestException as exc:
            raise CodexConnectionError("连接 OpenAI OAuth 服务失败。") from exc
        if response.status_code >= 400:
            raise CodexAuthenticationError(_response_error_text(response, "OAuth 令牌请求失败。"))
        try:
            data = response.json()
        except ValueError as exc:
            raise CodexAuthenticationError("OAuth 服务返回了无效 JSON。") from exc
        if not isinstance(data, Mapping):
            raise CodexAuthenticationError("OAuth 服务返回结构无效。")
        return data

    def get_access_bundle(
        self,
        *,
        force_refresh: bool = False,
        stale_access_token: str | None = None,
    ) -> dict[str, object]:
        bundle = load_codex_oauth_bundle()
        if not bundle.get("access_token"):
            raise CodexAuthenticationError("尚未登录 OpenAI Codex。")
        expires_at = _as_float(bundle.get("expires_at"))
        if not force_refresh and expires_at > time.time() + TOKEN_REFRESH_MARGIN_SECONDS:
            return bundle
        access_token_before_lock = str(stale_access_token or bundle.get("access_token") or "")

        with self._refresh_lock:
            latest = load_codex_oauth_bundle()
            latest_expires_at = _as_float(latest.get("expires_at"))
            if not force_refresh and latest_expires_at > time.time() + TOKEN_REFRESH_MARGIN_SECONDS:
                return latest
            # 多个请求同时收到 401 时，第一个请求刷新后，后续请求直接复用新令牌，
            # 避免在全局锁内重复消耗 refresh token。
            if (
                force_refresh
                and str(latest.get("access_token") or "") != access_token_before_lock
                and latest_expires_at > time.time() + TOKEN_REFRESH_MARGIN_SECONDS
            ):
                return latest
            refresh_token = str(latest.get("refresh_token") or "")
            if not refresh_token:
                raise CodexAuthenticationError("Codex 登录已过期，请重新登录。")
            token_data = self._post_token(
                {
                    "grant_type": "refresh_token",
                    "client_id": CODEX_CLIENT_ID,
                    "refresh_token": refresh_token,
                }
            )
            refreshed = normalize_token_response(
                token_data,
                previous_refresh_token=refresh_token,
            )
            for key in ("account_id", "email", "plan_type", "id_token"):
                if not refreshed.get(key) and latest.get(key):
                    refreshed[key] = latest[key]
            save_codex_oauth_bundle(refreshed)
            return refreshed


def _as_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _response_error_text(response: requests.Response, fallback: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        if isinstance(error, str) and error:
            return error
        message = payload.get("message")
        if isinstance(message, str) and message:
            return message
    text = str(getattr(response, "text", "") or "").strip()
    return text[:500] if text else fallback


def _message_content_to_responses(content: object) -> object:
    if not isinstance(content, list):
        return str(content or "")
    parts: list[dict[str, object]] = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        part_type = part.get("type")
        if part_type == "text":
            parts.append({"type": "input_text", "text": str(part.get("text") or "")})
        elif part_type == "image_url":
            image = part.get("image_url")
            image_url = image.get("url") if isinstance(image, Mapping) else image
            if isinstance(image_url, str) and image_url:
                converted: dict[str, object] = {"type": "input_image", "image_url": image_url}
                if isinstance(image, Mapping) and image.get("detail"):
                    converted["detail"] = image["detail"]
                parts.append(converted)
    return parts


def messages_to_responses_input(messages: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """把 Chat Completions 历史转换成 Responses input items。"""
    items: list[dict[str, object]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": str(message.get("content") or ""),
                }
            )
            continue

        hidden_reasoning = message.get("_codex_reasoning_items")
        if isinstance(hidden_reasoning, list):
            items.extend(dict(item) for item in hidden_reasoning if isinstance(item, Mapping))

        content = message.get("content")
        if content not in (None, "", []):
            normalized_role = "developer" if role == "system" else role
            items.append(
                {
                    "role": normalized_role,
                    "content": _message_content_to_responses(content),
                }
            )

        tool_calls = message.get("tool_calls")
        if role == "assistant" and isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, Mapping):
                    continue
                function = call.get("function")
                if not isinstance(function, Mapping):
                    continue
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or "{}"),
                    }
                )
    return items


def tools_to_responses_tools(tools: Sequence[Mapping[str, object]] | None) -> list[dict[str, object]]:
    converted: list[dict[str, object]] = []
    for tool in tools or ():
        function = tool.get("function") if isinstance(tool, Mapping) else None
        if not isinstance(function, Mapping):
            continue
        item: dict[str, object] = {
            "type": "function",
            "name": str(function.get("name") or ""),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        }
        if function.get("description"):
            item["description"] = str(function["description"])
        if "strict" in function:
            item["strict"] = bool(function["strict"])
        converted.append(item)
    return converted


def build_codex_request_payload(
    *,
    model: str,
    messages: Sequence[Mapping[str, object]],
    tools: Sequence[Mapping[str, object]] | None = None,
    tool_choice: object = "auto",
    **kwargs: object,
) -> dict[str, object]:
    """构造 ChatGPT Codex Responses 请求体并过滤不受支持的参数。"""
    model_id = model.split("/", 1)[1] if model.startswith(f"{OPENAI_CODEX_PROVIDER_ID}/") else model
    system_instructions: list[str] = []
    input_messages: list[Mapping[str, object]] = []
    for message in messages:
        if str(message.get("role") or "") == "system":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                system_instructions.append(content)
            continue
        input_messages.append(message)

    payload: dict[str, object] = {
        "model": model_id,
        "input": messages_to_responses_input(input_messages),
        "store": False,
        # ChatGPT 的 Codex backend 是 stream-only 接口。项目上层仍然使用
        # 非流式调用；这里在 transport 层读取完整 SSE 后再返回统一结果。
        "stream": True,
        "include": ["reasoning.encrypted_content"],
    }
    if system_instructions:
        payload["instructions"] = "\n\n".join(system_instructions)
    response_tools = tools_to_responses_tools(tools)
    if response_tools:
        payload["tools"] = response_tools
        if isinstance(tool_choice, str) and tool_choice in {"auto", "none", "required"}:
            payload["tool_choice"] = tool_choice
        elif isinstance(tool_choice, Mapping):
            function = tool_choice.get("function")
            if isinstance(function, Mapping) and function.get("name"):
                payload["tool_choice"] = {"type": "function", "name": function["name"]}

    thinking = kwargs.get("thinking")
    effort = kwargs.get("reasoning_effort")
    reasoning: dict[str, object] = {}
    if isinstance(thinking, Mapping) and thinking.get("type") == "disabled":
        reasoning["effort"] = "none"
    elif effort not in (None, "", "default"):
        reasoning["effort"] = effort
    if not (isinstance(thinking, Mapping) and thinking.get("type") == "disabled"):
        reasoning["summary"] = "auto"
    if reasoning:
        payload["reasoning"] = reasoning
    return payload


def responses_to_chat_completion(payload: Mapping[str, object]) -> dict[str, object]:
    """把 Responses 返回值转换为现有工具链使用的 Chat Completions 结构。"""
    text_parts: list[str] = []
    tool_calls: list[dict[str, object]] = []
    reasoning_items: list[dict[str, object]] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            item_type = item.get("type")
            if item_type == "reasoning" and item.get("encrypted_content"):
                reasoning_items.append(dict(item))
            elif item_type == "function_call":
                tool_calls.append(
                    {
                        "id": str(item.get("call_id") or item.get("id") or ""),
                        "type": "function",
                        "function": {
                            "name": str(item.get("name") or ""),
                            "arguments": str(item.get("arguments") or "{}"),
                        },
                    }
                )
            elif item_type == "message":
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, Mapping) and part.get("type") == "output_text":
                            text_parts.append(str(part.get("text") or ""))

    if not text_parts and isinstance(payload.get("output_text"), str):
        text_parts.append(str(payload["output_text"]))
    message: dict[str, object] = {
        "role": "assistant",
        "content": "".join(text_parts),
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning_items:
        message["_codex_reasoning_items"] = reasoning_items

    raw_usage = payload.get("usage")
    usage: dict[str, object] = {}
    if isinstance(raw_usage, Mapping):
        input_tokens = int(raw_usage.get("input_tokens") or 0)
        output_tokens = int(raw_usage.get("output_tokens") or 0)
        usage = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": int(raw_usage.get("total_tokens") or input_tokens + output_tokens),
        }
        details = raw_usage.get("input_tokens_details")
        if isinstance(details, Mapping) and details.get("cached_tokens") is not None:
            usage["prompt_tokens_details"] = {"cached_tokens": details.get("cached_tokens")}

    return {
        "id": str(payload.get("id") or ""),
        "object": "chat.completion",
        "model": str(payload.get("model") or ""),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": usage,
    }


def _decode_sse_line(raw_line: object) -> str:
    if isinstance(raw_line, bytes):
        return raw_line.decode("utf-8", errors="replace")
    return str(raw_line or "")


def _iter_sse_json_events(response: requests.Response):
    """逐个解析 Responses SSE 事件，只向调用者暴露 JSON data。"""
    data_lines: list[str] = []

    def flush():
        if not data_lines:
            return None
        raw_data = "\n".join(data_lines).strip()
        data_lines.clear()
        if not raw_data or raw_data == "[DONE]":
            return None
        try:
            event = json.loads(raw_data)
        except ValueError as exc:
            raise CodexBadRequestError("Codex 返回了无效的 SSE JSON。") from exc
        if not isinstance(event, Mapping):
            raise CodexBadRequestError("Codex 返回了无效的 SSE 事件。")
        return event

    try:
        lines = response.iter_lines(decode_unicode=False)
    except (AttributeError, TypeError) as exc:
        raise CodexBadRequestError("Codex 返回的 SSE 响应不可读取。") from exc

    try:
        for raw_line in lines:
            line = _decode_sse_line(raw_line).rstrip("\r")
            if not line:
                event = flush()
                if event is not None:
                    yield event
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
    except requests.Timeout as exc:
        raise CodexConnectionError("Codex SSE 读取超时。") from exc
    except requests.RequestException as exc:
        raise CodexConnectionError("Codex SSE 连接中断。") from exc

    event = flush()
    if event is not None:
        yield event


def _event_output_index(event: Mapping[str, object], fallback: int) -> int:
    try:
        return int(event.get("output_index", fallback))
    except (TypeError, ValueError):
        return fallback


def _ensure_message_text_item(
    output_items: dict[int, dict[str, object]],
    output_index: int,
    content_index: int,
) -> dict[str, object]:
    item = output_items.setdefault(
        output_index,
        {"type": "message", "role": "assistant", "content": []},
    )
    if item.get("type") != "message":
        item = {"type": "message", "role": "assistant", "content": []}
        output_items[output_index] = item
    content = item.get("content")
    if not isinstance(content, list):
        content = []
        item["content"] = content
    while len(content) <= content_index:
        content.append({"type": "output_text", "text": "", "annotations": []})
    part = content[content_index]
    if not isinstance(part, dict) or part.get("type") != "output_text":
        part = {"type": "output_text", "text": "", "annotations": []}
        content[content_index] = part
    return part


def _sse_failure_message(event: Mapping[str, object]) -> str:
    response = event.get("response")
    error = response.get("error") if isinstance(response, Mapping) else event.get("error")
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(error, str) and error.strip():
        return error.strip()
    return "Codex 流式响应失败。"


def collect_codex_sse_response(response: requests.Response) -> dict[str, object]:
    """把 Codex stream-only SSE 在 transport 层聚合为完整 Responses 对象。"""
    response_meta: dict[str, object] = {}
    completed_response: dict[str, object] | None = None
    output_items: dict[int, dict[str, object]] = {}
    saw_event = False
    saw_completed = False

    for event in _iter_sse_json_events(response):
        saw_event = True
        event_type = str(event.get("type") or "")
        event_response = event.get("response")
        if isinstance(event_response, Mapping):
            for key, value in event_response.items():
                if key != "output" and value is not None:
                    response_meta[key] = value

        if event_type in {"response.output_item.added", "response.output_item.done"}:
            item = event.get("item")
            if isinstance(item, Mapping):
                index = _event_output_index(event, len(output_items))
                # done 事件含有最终文本、函数参数和 encrypted reasoning，直接覆盖快照。
                output_items[index] = dict(item)
        elif event_type in {"response.output_text.delta", "response.output_text.done"}:
            output_index = _event_output_index(event, 0)
            try:
                content_index = int(event.get("content_index", 0))
            except (TypeError, ValueError):
                content_index = 0
            part = _ensure_message_text_item(output_items, output_index, content_index)
            if event_type.endswith(".delta"):
                part["text"] = str(part.get("text") or "") + str(event.get("delta") or "")
            elif isinstance(event.get("text"), str):
                part["text"] = event["text"]
        elif event_type in {
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        }:
            output_index = _event_output_index(event, len(output_items))
            item = output_items.setdefault(
                output_index,
                {
                    "type": "function_call",
                    "call_id": str(event.get("call_id") or event.get("item_id") or ""),
                    "name": str(event.get("name") or ""),
                    "arguments": "",
                },
            )
            if event_type.endswith(".delta"):
                item["arguments"] = str(item.get("arguments") or "") + str(event.get("delta") or "")
            elif isinstance(event.get("arguments"), str):
                item["arguments"] = event["arguments"]
        elif event_type == "response.failed":
            raise CodexBadRequestError(_sse_failure_message(event))
        elif event_type == "response.incomplete":
            raise CodexBadRequestError("Codex 响应未完整结束。")

        if event_type == "response.completed":
            saw_completed = True
            if isinstance(event_response, Mapping):
                completed_response = dict(event_response)

    if not saw_event:
        raise CodexBadRequestError("Codex 返回了空的 SSE 响应。")
    if not saw_completed:
        raise CodexConnectionError("Codex SSE 在 response.completed 前中断。")

    final_response = dict(response_meta)
    if completed_response is not None:
        final_response.update({key: value for key, value in completed_response.items() if value is not None})
    completed_output = final_response.get("output")
    # Codex backend 有时在 response.completed 中返回 output:null；此时以此前的
    # output_item.done 快照为准，避免丢失文本、工具调用和 encrypted reasoning。
    if not isinstance(completed_output, list) or not completed_output:
        final_response["output"] = [output_items[index] for index in sorted(output_items)]
    return final_response


def _raise_for_codex_response(response: requests.Response) -> None:
    if response.status_code < 400:
        return
    message = _response_error_text(response, f"Codex 请求失败（HTTP {response.status_code}）。")
    if response.status_code == 401:
        raise CodexAuthenticationError(message)
    if response.status_code == 403:
        raise CodexPermissionError(message)
    if response.status_code == 429:
        raise CodexRateLimitError(message)
    if response.status_code == 408 or response.status_code >= 500:
        raise CodexConnectionError(message)
    raise CodexBadRequestError(message, status_code=response.status_code)


def codex_completion(
    *,
    model: str,
    messages: Sequence[Mapping[str, object]],
    tools: Sequence[Mapping[str, object]] | None = None,
    tool_choice: object = "auto",
    oauth_client: CodexOAuthClient | None = None,
    **kwargs: object,
) -> dict[str, object]:
    """用 Codex OAuth 调用 Responses，并返回 Chat Completions 风格字典。"""
    client = oauth_client or CodexOAuthClient()
    request_payload = build_codex_request_payload(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        **kwargs,
    )
    timeout = float(kwargs.get("timeout") or 30)

    def send(bundle: Mapping[str, object]) -> requests.Response:
        headers = {
            "Authorization": f"Bearer {bundle.get('access_token', '')}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "OpenAI-Beta": "responses=experimental",
            "originator": "d_sakiko",
        }
        account_id = str(bundle.get("account_id") or "")
        if account_id:
            headers["chatgpt-account-id"] = account_id
        try:
            return client.session.post(
                CODEX_API_URL,
                headers=headers,
                json=request_payload,
                timeout=timeout,
                stream=True,
            )
        except requests.Timeout as exc:
            raise CodexConnectionError("Codex 请求超时。") from exc
        except requests.RequestException as exc:
            raise CodexConnectionError("连接 OpenAI Codex 服务失败。") from exc

    bundle = client.get_access_bundle()
    response = send(bundle)
    if response.status_code == 401:
        bundle = client.get_access_bundle(
            force_refresh=True,
            stale_access_token=str(bundle.get("access_token") or ""),
        )
        response = send(bundle)
    _raise_for_codex_response(response)
    content_type = str(getattr(response, "headers", {}).get("Content-Type", "") or "").lower()
    # chatgpt.com 当前可能省略 Content-Type，只保留 chunked SSE；请求本身已经
    # 明确 stream:true，因此无 Content-Type 时也按事件流读取。明确声明 JSON 的
    # 兼容 relay 仍走下方 JSON 分支。
    if "text/event-stream" in content_type or not content_type:
        return responses_to_chat_completion(collect_codex_sse_response(response))
    try:
        response_payload = response.json()
    except ValueError as exc:
        raise CodexBadRequestError("Codex 返回了无效 JSON。", status_code=response.status_code) from exc
    if not isinstance(response_payload, Mapping):
        raise CodexBadRequestError("Codex 返回结构无效。", status_code=response.status_code)
    return responses_to_chat_completion(response_payload)
