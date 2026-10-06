"""额度模板、脚本隔离、网络边界及跨配置状态回归。"""
from __future__ import annotations

import dataclasses
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import threading
import time
from types import SimpleNamespace

import pytest
import quickjs

from chat.api_usage import (
    ApiTarget, ApiUsageConfig, ApiUsageService, UsageError, UsageSecretStore,
    UsageScriptRunner, default_template, normalize_base, origin, parse_entries,
    resolve_target, template_script, redact,
)


def extract(template, response, base, **kwargs):
    config = ApiUsageConfig(template=template, **kwargs)
    code, _ = template_script(config, ApiTarget("custom", base))
    context = quickjs.Context()
    context.eval("var config=" + code + ";")
    context.eval("var response=" + json.dumps(response) + ";")
    return json.loads(context.eval("JSON.stringify(config.extractor(response))"))


def terminal(service, target, config, *, test=False, overrides=None):
    results = queue.Queue()
    service.query(target, config, results.put, test=test, overrides=overrides)
    while True:
        result = results.get(timeout=15)
        if result.status != "loading":
            return result


def test_deepseek_keeps_currencies_and_zero():
    result = extract("deepseek", {"is_available": False, "balance_infos": [
        {"currency": "CNY", "total_balance": "0", "granted_balance": "0", "topped_up_balance": "0"},
        {"currency": "USD", "total_balance": "2.5", "granted_balance": "1.5", "topped_up_balance": "1"},
    ]}, "https://api.deepseek.com/v1")
    rows = parse_entries(result)
    assert [row.remaining for row in rows] == [0, 2.5]
    assert [row.unit for row in rows] == ["CNY", "USD"]
    assert not rows[0].isValid
    assert "赠金" in rows[1].extra


@pytest.mark.parametrize("host,unit", [("api.siliconflow.cn", "CNY"), ("api.siliconflow.com", "USD")])
def test_siliconflow(host, unit):
    result = extract("siliconflow", {"code": 20000, "data": {"totalBalance": "12.5", "chargeBalance": "10", "balance": "2.5"}},
                     f"https://{host}/v1")
    assert result["remaining"] == 12.5
    assert result["unit"] == unit


def test_openrouter_account_and_key_are_distinct():
    account = extract("openrouter_account", {"data": {"total_credits": 100, "total_usage": 25}}, "https://openrouter.ai/api/v1")
    key = extract("openrouter_key", {"data": {"limit": 20, "limit_remaining": 12, "usage": 8}}, "https://openrouter.ai/api/v1")
    assert account["remaining"] == 75 and account["planName"] == "账户余额"
    assert key["remaining"] == 12 and key["planName"] == "密钥额度"
    unlimited = extract("openrouter_key", {"data": {"limit": None, "usage": 5}}, "https://openrouter.ai/api/v1")
    assert unlimited["remaining"] is None and "未设置密钥限额" in unlimited["extra"]


def test_new_api_scale_and_root():
    config = ApiUsageConfig(template="new_api", quota_scale=100, unit="次")
    _, base = template_script(config, ApiTarget("custom", "https://relay.example/v1"))
    assert base == "https://relay.example"
    result = extract("new_api", {"success": True, "data": {"quota": 300, "used_quota": 100}},
                     "https://relay.example/v1", quota_scale=100, unit="次")
    assert result == {"remaining": 3, "used": 1, "total": 4, "unit": "次", "planName": "账户额度"}


@pytest.mark.parametrize("template,response,base", [
    ("deepseek", {"balance_infos": []}, "https://api.deepseek.com"),
    ("openrouter_key", {"data": {"limit": 1}}, "https://openrouter.ai"),
    ("siliconflow", {"data": {}}, "https://api.siliconflow.cn"),
    ("new_api", {"success": False, "message": "expired"}, "https://relay.example"),
])
def test_missing_fields_are_errors_not_zero(template, response, base):
    with pytest.raises(quickjs.JSException):
        extract(template, response, base)


def test_official_detection_uses_exact_host_not_model_or_substring():
    target = ApiTarget("deepseek", "https://api.deepseek.com.evil.example/v1", "secret")
    assert default_template(target) == "custom"
    with pytest.raises(UsageError, match="不匹配"):
        template_script(ApiUsageConfig(template="deepseek"), target)


def test_normalized_scope_and_local_http():
    assert normalize_base("https://API.EXAMPLE:443/v1/") == "https://api.example/v1"
    assert origin("http://127.0.0.1:8123/v1") == "http://127.0.0.1:8123"
    assert origin("http://[::1]:8123/v1") == "http://[::1]:8123"
    for url in ["http://127.0.0.1.evil.example", "ftp://example.com", "https://key@example.com"]:
        with pytest.raises(UsageError):
            origin(url)


@pytest.mark.parametrize("bad", [True, "1", float("nan"), float("inf")])
def test_numeric_result_validation(bad):
    with pytest.raises(UsageError):
        parse_entries({"remaining": bad})


def test_null_unknown_and_redaction():
    row = parse_entries({"remaining": None, "extra": "credential=sk-private"}, ("sk-private",))[0]
    assert row.remaining is None and "sk-private" not in row.extra


def test_persisted_config_contains_only_secret_references():
    config = ApiUsageConfig(api_key_ref="ref-1", access_token_ref="ref-2")
    assert "api_key" not in config.to_dict()
    assert ApiUsageConfig.from_dict(config.to_dict(), ApiTarget("deepseek", "https://api.deepseek.com")) == config


def test_secret_store_session_only_when_backend_fails():
    class Backend:
        def set_password(self, *args): raise RuntimeError("no backend")
        def get_password(self, *args): return None
    store = UsageSecretStore(Backend())
    assert not store.put("ref", "session-secret")
    assert store.get("ref") == "session-secret"
    with pytest.raises(UsageError):
        UsageSecretStore(Backend()).get("ref")


def test_shared_api_disabled_and_never_reads_a_shared_key():
    item = lambda value: SimpleNamespace(value=value)
    target = resolve_target(SimpleNamespace(use_default_deepseek_api=item(True)))
    service = ApiUsageService(runner=lambda *args: pytest.fail("must not query"))
    try:
        assert terminal(service, target, ApiUsageConfig(enabled=True)).status == "disabled"
        assert target.api_key == ""
    finally:
        service.close()


def test_openrouter_account_requires_dedicated_management_key():
    service = ApiUsageService(runner=lambda *args: pytest.fail("must not query"))
    try:
        result = terminal(service, ApiTarget("openrouter", "https://openrouter.ai/api/v1", "chat-key"),
                          ApiUsageConfig(enabled=True, template="openrouter_account"))
        assert "管理密钥" in result.error
    finally:
        service.close()


def test_last_good_data_and_error_redaction():
    replies = iter([{"data": {"remaining": 5}}, {"error": "failed sk-private"}])
    service = ApiUsageService(runner=lambda *args: next(replies))
    target = ApiTarget("deepseek", "https://api.deepseek.com", "sk-private")
    config = ApiUsageConfig(enabled=True, template="deepseek")
    try:
        good = terminal(service, target, config)
        failed = terminal(service, target, config)
        assert good.status == "success"
        assert failed.stale and failed.entries == good.entries
        assert failed.last_success_at == good.last_success_at
        assert "sk-private" not in failed.error
    finally:
        service.close()


def test_deduplication_switch_and_late_reply():
    gate = threading.Event()
    seen = []
    def runner(payload, cancel, started):
        seen.append(payload["variables"]["apiKey"])
        if payload["variables"]["apiKey"] == "old":
            gate.wait(2)
        return {"data": {"remaining": 1}}
    service = ApiUsageService(runner=runner)
    config = ApiUsageConfig(enabled=True, template="deepseek")
    old_results = queue.Queue()
    old = ApiTarget("deepseek", "https://api.deepseek.com", "old")
    try:
        assert service.query(old, config, old_results.put)
        assert not service.query(old, config, old_results.put)
        new = terminal(service, dataclasses.replace(old, api_key="new"), config)
        assert new.status == "success"
        gate.set()
        time.sleep(0.1)
        assert old_results.qsize() == 1  # 仅 loading，旧结果没有交付。
        assert seen == ["old", "new"]
    finally:
        gate.set()
        service.close()


@pytest.fixture
def http_server():
    requests = []
    response = {"status": 200, "body": {"balance": 12.5, "success": True,
                "data": {"quota": 1500000, "used_quota": 500000, "group": "test"}}, "headers": {}}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                             "user_id": self.headers.get("New-Api-User")})
            time.sleep(response.get("delay", 0))
            self.send_response(response["status"])
            for k, v in response["headers"].items(): self.send_header(k, v)
            self.end_headers()
            body = response["body"]
            try:
                self.wfile.write((json.dumps(body).encode() if isinstance(body, dict) else body))
            except OSError:
                pass  # 超时／取消测试会主动断开连接。
        do_POST = do_GET
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, response
    finally:
        server.shutdown()
        server.server_close()


GENERIC = """({request:{url:'{{baseUrl}}/user/balance',method:'GET',
    headers:{Authorization:'Bearer {{apiKey}}'}},
    extractor:function(r){return {isValid:true,remaining:r.balance,unit:'USD'};}})"""
NEW_API = """({request:{url:'{{baseUrl}}/api/user/self',method:'GET',headers:{
    Authorization:'Bearer {{accessToken}}','New-Api-User':'{{userId}}'}},
    extractor:function(r){return [{remaining:r.data.quota/500000,used:r.data.used_quota/500000,
    total:(r.data.quota+r.data.used_quota)/500000,unit:'USD',planName:r.data.group}];}})"""


@pytest.mark.parametrize("code,expected", [(GENERIC, 12.5), (NEW_API, 3)])
def test_cc_switch_scripts_in_real_child(http_server, code, expected):
    base, requests, _ = http_server
    service = ApiUsageService()
    config = ApiUsageConfig(enabled=True, template="custom", code=code, user_id="42")
    try:
        result = terminal(service, ApiTarget("custom", base, "sk-test"), config,
                          test=True, overrides={"access_token": "access-test"})
        assert result.status == "success", result.error
        assert result.entries[0].remaining == expected
        assert requests[0]["authorization"] == "Bearer " + ("access-test" if code == NEW_API else "sk-test")
    finally:
        service.close()


def test_placeholder_quotes_and_backslashes(http_server):
    base, requests, _ = http_server
    secret = 'a"b\'c\\d${notExpression}`e'
    service = ApiUsageService()
    try:
        result = terminal(service, ApiTarget("custom", base, secret),
                          ApiUsageConfig(enabled=True, template="custom", code=GENERIC))
        assert result.status == "success", result.error
        assert requests[0]["authorization"] == "Bearer " + secret
    finally:
        service.close()


def test_other_origin_requires_confirmation_before_network(http_server):
    base, requests, _ = http_server
    config = ApiUsageConfig(enabled=True, template="custom", code=GENERIC, base_url=base)
    service = ApiUsageService()
    target = ApiTarget("custom", "https://chat.example/v1", "private")
    try:
        result = terminal(service, target, config)
        assert result.status == "needs_confirmation" and not requests
        approved = dataclasses.replace(config, approved_origins=(origin(base),))
        assert terminal(service, target, approved).status == "success"
        assert len(requests) == 1
    finally:
        service.close()


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
def test_http_errors_and_no_redirect(http_server, status):
    base, requests, response = http_server
    response.update(status=status, headers={"Location": base + "/next"})
    service = ApiUsageService()
    try:
        result = terminal(service, ApiTarget("custom", base, "test-secret"),
                          ApiUsageConfig(enabled=True, template="custom", code=GENERIC))
        assert result.status == "error" and f"HTTP {status}" in result.error
        assert len(requests) == 1
        assert "test-secret" not in result.error
    finally:
        service.close()


@pytest.mark.parametrize("body,expected", [(b"not JSON", "JSON"), (b"x" * (1024*1024+1), "1 MiB")],
                         ids=["invalid-json", "oversize-response"])
def test_bad_or_large_response(http_server, body, expected):
    base, _, response = http_server
    response["body"] = body
    service = ApiUsageService()
    try:
        result = terminal(service, ApiTarget("custom", base, "test"),
                          ApiUsageConfig(enabled=True, template="custom", code=GENERIC))
        assert result.status == "error" and expected in result.error
    finally:
        service.close()


@pytest.mark.parametrize("code", ["syntax???", "(()=>{throw Error('private-key');})()",
    "(()=>{while(true){};})()", "(()=>{var a=[];while(true)a.push('x'.repeat(1024));})()"])
def test_bad_scripts_are_bounded_and_redacted(code):
    service = ApiUsageService()
    start = time.monotonic()
    try:
        result = terminal(service, ApiTarget("custom", "https://example.invalid", "private-key"),
                          ApiUsageConfig(enabled=True, template="custom", code=code))
        assert result.status == "error" and "private-key" not in result.error
        assert time.monotonic() - start < 12
    finally:
        service.close()


def test_nan_script_is_not_converted_to_null(http_server):
    base, _, _ = http_server
    code = GENERIC.replace("remaining:r.balance", "remaining:NaN")
    service = ApiUsageService()
    try:
        result = terminal(service, ApiTarget("custom", base, "test"),
                          ApiUsageConfig(enabled=True, template="custom", code=code))
        assert result.status == "error" and "非有限" in result.error
    finally:
        service.close()


def test_cancel_reaps_child_without_late_success():
    runner = UsageScriptRunner()
    cancel = threading.Event()
    processes = []
    errors = []
    def query():
        try:
            runner({"code": "(()=>{while(true){};})()", "variables": {}, "timeout": 10,
                    "allowed_origins": []}, cancel, processes.append)
        except UsageError as error:
            errors.append(error)
    thread = threading.Thread(target=query)
    thread.start()
    limit = time.monotonic() + 3
    while not processes and time.monotonic() < limit: time.sleep(0.01)
    cancel.set()
    thread.join(3)
    assert not thread.is_alive()
    assert processes and processes[0].poll() is not None
    assert errors


def test_network_timeout_has_no_zero_balance(http_server):
    base, _, response = http_server
    response["delay"] = 2.5
    service = ApiUsageService()
    try:
        result = terminal(service, ApiTarget("custom", base, "secret"),
            ApiUsageConfig(enabled=True, template="custom", code=GENERIC, timeout=2))
        assert result.status == "error" and not result.entries
        assert "secret" not in result.error
    finally:
        service.close()


def test_escaped_exception_credentials_are_redacted():
    secret = 'key\nwith"quotes\\path'
    for text in (repr(secret), json.dumps(secret), secret):
        assert "with" not in redact(text, (secret,))


@pytest.mark.parametrize("url", ["https://[broken", "https://host:broken", "https://host:999999"])
def test_malformed_urls_do_not_crash_config_loading(url):
    target = ApiTarget("custom", normalize_base(url), "secret")
    assert target.config_key and default_template(target) == "custom"
    with pytest.raises(UsageError): origin(url)


def test_script_array_and_no_host_capabilities(http_server):
    base, _, _ = http_server
    code = """({request:{url:'{{baseUrl}}/balance'},extractor: function(r) {
        if (typeof require !== 'undefined' || typeof fetch !== 'undefined' || typeof process !== 'undefined')
            throw Error('unexpected host capability');
        return [{remaining:r.balance, unit:'CNY'}, {remaining:0,unit:'USD',extra:'separate'}];
    }})"""
    service = ApiUsageService()
    try:
        result = terminal(service, ApiTarget("custom", base), ApiUsageConfig(enabled=True, template="custom", code=code))
        assert result.status == "success"
        assert [(entry.remaining, entry.unit) for entry in result.entries] == [(12.5, "CNY"), (0, "USD")]
    finally:
        service.close()


def test_close_during_process_start_reaps_child():
    processes = []
    runner = UsageScriptRunner()
    def delayed_start(payload, cancel, started):
        time.sleep(0.1)
        def capture(process):
            processes.append(process)
            started(process)
        return runner(payload, cancel, capture)
    service = ApiUsageService(runner=delayed_start)
    service.query(ApiTarget("custom", "https://example.invalid"),
        ApiUsageConfig(enabled=True, template="custom", code="(()=>{while(true){};})()"), lambda result: None)
    service.close()
    assert processes and all(process.poll() is not None for process in processes)
    assert not service._threads
