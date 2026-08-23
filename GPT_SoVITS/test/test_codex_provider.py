from __future__ import annotations

import base64
import json
import sys
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


GPT_SOVITS_DIR = Path(__file__).resolve().parents[1]
if str(GPT_SOVITS_DIR) not in sys.path:
    sys.path.insert(0, str(GPT_SOVITS_DIR))

from llm_provider import codex


def jwt(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]):
        self.status_code = status_code
        self.payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class FakeHttpServer:
    def __init__(self):
        self.timeout = None
        self.closed = False

    def handle_request(self):
        return None

    def server_close(self):
        self.closed = True


class CodexProviderTest(unittest.TestCase):
    def test_callback_server_falls_back_to_second_registered_port(self):
        fake_server = FakeHttpServer()
        with mock.patch.object(codex, "HTTPServer", side_effect=[OSError("busy"), fake_server]) as server:
            callback = codex._OAuthCallbackServer("state")
        self.assertEqual(callback.port, 1457)
        self.assertEqual(server.call_args_list[0].args[0], ("127.0.0.1", 1455))
        self.assertEqual(server.call_args_list[1].args[0], ("127.0.0.1", 1457))

    def test_callback_wait_honors_cancel_event(self):
        fake_server = FakeHttpServer()
        with mock.patch.object(codex, "HTTPServer", return_value=fake_server):
            callback = codex._OAuthCallbackServer("state")
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(codex.CodexAuthenticationError):
            callback.wait(cancel, 1)
        self.assertTrue(fake_server.closed)

    def test_sign_in_closes_callback_when_browser_cannot_open(self):
        callback = mock.Mock()
        callback.redirect_uri = "http://localhost:1455/auth/callback"
        client = codex.CodexOAuthClient(session=FakeSession([]))
        with (
            mock.patch.object(codex, "_OAuthCallbackServer", return_value=callback),
            self.assertRaises(codex.CodexConnectionError),
        ):
            client.sign_in(open_browser=lambda _url: False)
        callback.close.assert_called_once_with()
        callback.wait.assert_not_called()

    def test_authorization_url_contains_pkce_and_registered_callback(self):
        url = codex.build_authorization_url(
            "http://localhost:1455/auth/callback",
            "state-value",
            "challenge-value",
        )
        self.assertIn("client_id=" + codex.CODEX_CLIENT_ID, url)
        self.assertIn("code_challenge=challenge-value", url)
        self.assertIn("state=state-value", url)
        self.assertIn("codex_cli_simplified_flow=true", url)

    def test_token_response_extracts_account_metadata(self):
        access = jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "acct-123",
                    "chatgpt_plan_type": "plus",
                },
                "email": "person@example.com",
            }
        )
        bundle = codex.normalize_token_response(
            {"access_token": access, "refresh_token": "refresh", "expires_in": 60},
            now=100,
        )
        self.assertEqual(bundle["account_id"], "acct-123")
        self.assertEqual(bundle["email"], "person@example.com")
        self.assertEqual(bundle["plan_type"], "plus")
        self.assertEqual(bundle["expires_at"], 160)

    def test_messages_tools_images_and_reasoning_are_converted(self):
        messages = [
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
                ],
            },
            {
                "role": "assistant",
                "content": "checking",
                "_codex_reasoning_items": [
                    {"type": "reasoning", "id": "r1", "encrypted_content": "encrypted"}
                ],
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "weather", "arguments": '{"city":"上海"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "sunny"},
        ]
        payload = codex.build_codex_request_payload(
            model="openai_codex/gpt-5.6-sol",
            messages=messages,
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "description": "weather lookup",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            tool_choice="auto",
            temperature=1.0,
            top_p=1.0,
            max_tokens=100,
            response_format={"type": "json_object"},
            reasoning_effort="high",
        )
        self.assertEqual(payload["model"], "gpt-5.6-sol")
        self.assertFalse(payload["store"])
        self.assertNotIn("temperature", payload)
        self.assertNotIn("max_output_tokens", payload)
        self.assertEqual(payload["reasoning"]["effort"], "high")
        self.assertEqual(payload["instructions"], "system")
        self.assertEqual(payload["input"][0]["content"][1]["type"], "input_image")
        self.assertEqual(payload["input"][-1]["type"], "function_call_output")
        self.assertEqual(payload["tools"][0]["name"], "weather")

    def test_response_is_normalized_and_keeps_encrypted_reasoning(self):
        result = codex.responses_to_chat_completion(
            {
                "id": "resp-1",
                "model": "gpt-5.6-sol",
                "output": [
                    {"type": "reasoning", "id": "r1", "encrypted_content": "encrypted"},
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "稍等"}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "weather",
                        "arguments": '{"city":"上海"}',
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        )
        choice = result["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(choice["message"]["content"], "稍等")
        self.assertEqual(choice["message"]["tool_calls"][0]["id"], "call-1")
        self.assertEqual(choice["message"]["_codex_reasoning_items"][0]["id"], "r1")
        self.assertEqual(result["usage"]["total_tokens"], 15)

    def test_completion_retries_once_after_401_and_uses_refreshed_token(self):
        session = FakeSession(
            [
                FakeResponse(401, {"error": {"message": "expired"}}),
                FakeResponse(
                    200,
                    {
                        "id": "resp-2",
                        "model": "gpt-5.6-sol",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "ok"}],
                            }
                        ],
                    },
                ),
            ]
        )
        client = codex.CodexOAuthClient(session=session)
        bundles = [
            {"access_token": "old", "account_id": "acct"},
            {"access_token": "new", "account_id": "acct"},
        ]

        def get_bundle(*, force_refresh=False, stale_access_token=None):
            self.assertEqual(force_refresh, len(bundles) == 1)
            if force_refresh:
                self.assertEqual(stale_access_token, "old")
            return bundles.pop(0)

        with mock.patch.object(client, "get_access_bundle", side_effect=get_bundle):
            result = codex.codex_completion(
                model="openai_codex/gpt-5.6-sol",
                messages=[{"role": "user", "content": "hello"}],
                oauth_client=client,
            )
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][1]["headers"]["Authorization"], "Bearer old")
        self.assertEqual(session.calls[1][1]["headers"]["Authorization"], "Bearer new")

    def test_expired_token_is_refreshed_and_persisted(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600},
                )
            ]
        )
        client = codex.CodexOAuthClient(session=session)
        expired = {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": 1,
            "account_id": "acct-123",
        }
        saved = []
        with (
            mock.patch.object(codex, "load_codex_oauth_bundle", return_value=expired),
            mock.patch.object(codex, "save_codex_oauth_bundle", side_effect=lambda bundle: saved.append(bundle)),
        ):
            refreshed = client.get_access_bundle()
        self.assertEqual(refreshed["access_token"], "new-access")
        self.assertEqual(refreshed["account_id"], "acct-123")
        self.assertEqual(saved[0]["refresh_token"], "new-refresh")
        self.assertEqual(session.calls[0][1]["data"]["grant_type"], "refresh_token")
        self.assertNotIn("scope", session.calls[0][1]["data"])

    def test_concurrent_forced_refresh_only_posts_once(self):
        session = FakeSession(
            [FakeResponse(200, {"access_token": "new-access", "expires_in": 3600})]
        )
        client = codex.CodexOAuthClient(session=session)
        current = {
            "access_token": "old-access",
            "refresh_token": "refresh",
            "expires_at": time.time() + 3600,
        }
        state_lock = threading.Lock()

        def load_bundle():
            with state_lock:
                return dict(current)

        def save_bundle(bundle):
            with state_lock:
                current.clear()
                current.update(bundle)

        barrier = threading.Barrier(3)
        results = []

        def refresh():
            barrier.wait()
            results.append(
                client.get_access_bundle(
                    force_refresh=True,
                    stale_access_token="old-access",
                )["access_token"]
            )

        with (
            mock.patch.object(codex, "load_codex_oauth_bundle", side_effect=load_bundle),
            mock.patch.object(codex, "save_codex_oauth_bundle", side_effect=save_bundle),
        ):
            threads = [threading.Thread(target=refresh) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=2)

        self.assertEqual(results, ["new-access", "new-access"])
        self.assertEqual(len(session.calls), 1)

    def test_http_statuses_map_server_failures_to_connection_errors(self):
        with self.assertRaises(codex.CodexConnectionError):
            codex._raise_for_codex_response(FakeResponse(500, {"error": "server"}))
        with self.assertRaises(codex.CodexConnectionError):
            codex._raise_for_codex_response(FakeResponse(408, {"error": "timeout"}))
        with self.assertRaises(codex.CodexBadRequestError):
            codex._raise_for_codex_response(FakeResponse(400, {"error": "bad"}))

    def test_local_oauth_and_responses_service_end_to_end(self):
        requests_seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                requests_seen.append((self.path, dict(self.headers), body))
                if self.path == "/token":
                    form = urllib.parse.parse_qs(body.decode("utf-8"))
                    self.assertEqual(form["grant_type"], ["refresh_token"])
                    payload = {
                        "access_token": "fresh-access",
                        "refresh_token": "fresh-refresh",
                        "expires_in": 3600,
                    }
                elif self.path == "/responses":
                    self.assertEqual(self.headers.get("Authorization"), "Bearer fresh-access")
                    self.assertEqual(self.headers.get("chatgpt-account-id"), "acct-local")
                    self.assertEqual(self.headers.get("OpenAI-Beta"), "responses=experimental")
                    request_json = json.loads(body)
                    self.assertEqual(request_json["store"], False)
                    if "reasoning.encrypted_content" not in request_json["include"]:
                        raise AssertionError("missing encrypted reasoning include")
                    payload = {
                        "id": "resp-local",
                        "model": "gpt-5.6-sol",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "local-ok"}],
                            }
                        ],
                    }
                else:
                    self.send_error(404)
                    return
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format, *_args):
                return

            def assertEqual(self, left, right):
                if left != right:
                    raise AssertionError(f"{left!r} != {right!r}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        stored = {
            "access_token": "expired-access",
            "refresh_token": "refresh-local",
            "expires_at": 1,
            "account_id": "acct-local",
        }

        def load_bundle():
            return dict(stored)

        def save_bundle(bundle):
            stored.clear()
            stored.update(bundle)

        try:
            with (
                mock.patch.object(codex, "CODEX_TOKEN_URL", base_url + "/token"),
                mock.patch.object(codex, "CODEX_API_URL", base_url + "/responses"),
                mock.patch.object(codex, "load_codex_oauth_bundle", side_effect=load_bundle),
                mock.patch.object(codex, "save_codex_oauth_bundle", side_effect=save_bundle),
            ):
                result = codex.codex_completion(
                    model="openai_codex/gpt-5.6-sol",
                    messages=[{"role": "user", "content": "hello local"}],
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(result["choices"][0]["message"]["content"], "local-ok")
        self.assertEqual([entry[0] for entry in requests_seen], ["/token", "/responses"])


if __name__ == "__main__":
    unittest.main()
