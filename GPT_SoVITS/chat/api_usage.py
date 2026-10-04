"""账户额度查询：与聊天回合、Qt 和角色演出独立的配置及查询服务。"""
from __future__ import annotations

import dataclasses
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Callable
from urllib.parse import urlsplit, urlunsplit


MAX_BYTES = 1024 * 1024
OFFICIAL_BASES = {
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "ollama": "http://localhost:11434",
}
TEMPLATE_NAMES = {
    "deepseek": "DeepSeek 账户余额",
    "siliconflow": "SiliconFlow 账户余额",
    "openrouter_key": "OpenRouter 密钥额度",
    "openrouter_account": "OpenRouter 账户余额（管理密钥）",
    "new_api": "New API 账户额度",
    "generic": "通用余额模板",
    "custom": "自定义 CC Switch 脚本",
}


class UsageError(ValueError):
    """可展示给用户的查询错误。"""


def normalize_base(url: str) -> str:
    """保留端点路径；凭据、片段和查询参数不进入配置作用域。"""
    try:
        parts = urlsplit(str(url).strip())
        port = parts.port
    except ValueError:
        return str(url).strip().rstrip("/")
    if not parts.hostname:
        return str(url).strip().rstrip("/")
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port and not (parts.scheme.lower() == "https" and port == 443) and not (
        parts.scheme.lower() == "http" and port == 80
    ):
        host += f":{port}"
    return urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/"), "", ""))


def origin(url: str) -> str:
    """检查查询协议和 HTTP 本地例外，并返回规范化源。"""
    try:
        parts = urlsplit(url)
        parts.port
    except ValueError as error:
        raise UsageError("查询地址格式错误，请检查主机名和端口。") from error
    if not parts.hostname or parts.username is not None or parts.password is not None:
        raise UsageError("查询地址格式错误，请使用不包含用户名和密码的完整网址。")
    if parts.scheme not in {"http", "https"}:
        raise UsageError("查询地址须使用 HTTPS，或本机／局域网 HTTP。")
    if parts.scheme == "http":
        local = parts.hostname.lower() == "localhost"
        try:
            address = ipaddress.ip_address(parts.hostname)
            local = address.is_loopback or (address.is_private and not address.is_link_local)
        except ValueError:
            pass
        if not local:
            raise UsageError("公网查询地址请使用 HTTPS。")
    normalized = normalize_base(url)
    p = urlsplit(normalized)
    return urlunsplit((p.scheme, p.netloc, "", "", ""))


def redact(text: object, secrets: tuple[str, ...] = ()) -> str:
    """公共结果和诊断中移除本次使用的凭据。"""
    result = str(text)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            for form in (secret, repr(secret)[1:-1], json.dumps(secret, ensure_ascii=True)[1:-1]):
                result = result.replace(form, "[已隐藏]")
    return result[:4000]


@dataclasses.dataclass(frozen=True)
class ApiTarget:
    provider_id: str
    base_url: str
    api_key: str = dataclasses.field(default="", repr=False)
    shared: bool = False

    @property
    def config_key(self) -> str:
        digest = hashlib.sha256(normalize_base(self.base_url).encode()).hexdigest()[:24]
        return f"{self.provider_id}:{digest}"

    @property
    def name(self) -> str:
        return {"deepseek": "DeepSeek", "openrouter": "OpenRouter", "custom": "自定义 API",
                "deepseek_up": "作者共用 API", "modelscope": "ModelScope"}.get(
            self.provider_id, self.provider_id)


def resolve_target(config: object) -> ApiTarget:
    """读取已保存的 API 选择，不读取作者的共用密钥。"""
    if config.use_default_deepseek_api.value:
        return ApiTarget("deepseek_up", "", shared=True)
    if config.enable_custom_llm_api_provider.value:
        return ApiTarget("custom", normalize_base(config.custom_llm_api_url.value),
                         str(config.custom_llm_api_key.value or ""))
    provider = str(config.llm_api_provider.value or "")
    bases = config.llm_api_base_url.value or {}
    keys = config.llm_api_key.value or {}
    return ApiTarget(provider, normalize_base(bases.get(provider) or OFFICIAL_BASES.get(provider, "")),
                     str(keys.get(provider) or ""))


def default_template(target: ApiTarget) -> str:
    try:
        host = urlsplit(target.base_url).hostname
    except ValueError:
        return "custom"
    return {"api.deepseek.com": "deepseek", "openrouter.ai": "openrouter_key",
            "api.siliconflow.cn": "siliconflow", "api.siliconflow.com": "siliconflow"}.get(host, "custom")


@dataclasses.dataclass(frozen=True)
class ApiUsageConfig:
    enabled: bool = False
    template: str = "custom"
    code: str = ""
    base_url: str = ""
    api_key_ref: str = ""
    access_token_ref: str = ""
    user_id: str = ""
    timeout: int = 10
    auto_interval: int = 5
    quota_scale: float = 500000
    unit: str = "USD"
    approved_origins: tuple[str, ...] = ()
    revision: int = 0

    @classmethod
    def from_dict(cls, data: dict | None, target: ApiTarget) -> ApiUsageConfig:
        fields = {field.name for field in dataclasses.fields(cls)}
        if data is not None and not isinstance(data, dict):
            raise UsageError("额度查询配置格式错误，请重新保存配置。")
        values = {k: v for k, v in (data or {}).items() if k in fields}
        values.setdefault("template", default_template(target))
        values["approved_origins"] = tuple(values.get("approved_origins", ()))
        return cls(**values)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def validate(self) -> None:
        if (not isinstance(self.code, str) or not isinstance(self.base_url, str)
                or not isinstance(self.enabled, bool) or not isinstance(self.template, str)
                or not isinstance(self.revision, int) or self.revision < 0
                or not isinstance(self.timeout, int) or not isinstance(self.auto_interval, int)
                or not isinstance(self.quota_scale, (int, float))
                or isinstance(self.quota_scale, bool)):
            raise UsageError("额度查询配置类型错误，请重新保存配置。")
        for value in (self.api_key_ref, self.access_token_ref, self.user_id, self.unit, *self.approved_origins):
            if not isinstance(value, str):
                raise UsageError("额度查询配置类型错误，请重新保存配置。")
        if self.template not in TEMPLATE_NAMES:
            raise UsageError("请选择有效的查询模板。")
        if isinstance(self.timeout, bool) or not 2 <= self.timeout <= 30:
            raise UsageError("查询超时须为 2—30 秒。")
        if isinstance(self.auto_interval, bool) or not 0 <= self.auto_interval <= 1440:
            raise UsageError("自动刷新间隔须为 0—1440 分钟。")
        if not math.isfinite(self.quota_scale) or self.quota_scale <= 0:
            raise UsageError("额度换算比例须大于零。")
        if len(self.code.encode("utf-8")) > 65536:
            raise UsageError("查询脚本超过 64 KiB。")
        if self.enabled and self.template == "custom" and not self.code.strip():
            raise UsageError("请粘贴 CC Switch 查询脚本，或选择内置模板。")


class UsageSecretStore:
    """专用凭据优先存系统凭据库；不可用时仅保留本进程内存。"""
    def __init__(self, backend: object = None) -> None:
        self.backend = backend
        self.session: dict[str, str] = {}

    def _backend(self):
        if self.backend is not None:
            return self.backend
        import keyring
        backend = keyring.get_keyring()
        if type(backend).__module__ not in {"keyring.backends.macOS", "keyring.backends.Windows",
                                            "keyring.backends.SecretService", "keyring.backends.kwallet"}:
            raise UsageError("系统凭据库未启用。")
        return backend

    def put(self, reference: str, value: str) -> bool:
        if not value:
            self.session.pop(reference, None)
        else:
            self.session[reference] = value
        try:
            backend = self._backend()
            if value:
                backend.set_password("D_sakiko.api_usage", reference, value)
            elif backend.get_password("D_sakiko.api_usage", reference):
                backend.delete_password("D_sakiko.api_usage", reference)
            return True
        except Exception:
            return not bool(value)

    def get(self, reference: str) -> str:
        if not reference:
            return ""
        if reference in self.session:
            return self.session[reference]
        try:
            value = self._backend().get_password("D_sakiko.api_usage", reference)
        except Exception:
            value = None
        if value is None:
            raise UsageError("查询专用凭据未保存到系统凭据库，请重新填写。")
        return value


@dataclasses.dataclass(frozen=True)
class UsageEntry:
    isValid: bool = True
    invalidMessage: str | None = None
    remaining: float | None = None
    used: float | None = None
    total: float | None = None
    unit: str | None = None
    planName: str | None = None
    extra: str | None = None


def parse_entries(data: object, secrets: tuple[str, ...] = ()) -> tuple[UsageEntry, ...]:
    """严格区分空值、零和无效账户，不跨币种聚合。"""
    items = data if isinstance(data, list) else [data]
    if not items or len(items) > 100:
        raise UsageError("查询结果须包含 1—100 项额度明细。")
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise UsageError("脚本须返回对象或对象数组。")
        values = {}
        for field in dataclasses.fields(UsageEntry):
            value = item.get(field.name)
            if value is None:
                continue
            if field.name in {"remaining", "used", "total"}:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise UsageError(f"{field.name} 须为有限数字或空值。")
            elif field.name == "isValid":
                if not isinstance(value, bool):
                    raise UsageError("isValid 须为布尔值。")
            elif not isinstance(value, str):
                raise UsageError(f"{field.name} 须为文本或空值。")
            else:
                value = redact(value, secrets)
            values[field.name] = value
        result.append(UsageEntry(**values))
    return tuple(result)


@dataclasses.dataclass(frozen=True)
class UsageSnapshot:
    status: str = "disabled"
    entries: tuple[UsageEntry, ...] = ()
    error: str = ""
    checked_at: float | None = None
    last_success_at: float | None = None
    stale: bool = False
    confirmation_origin: str = ""


NUMBER_HELPER = """function amount(v) {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean') throw Error('余额字段缺失');
  var n = Number(v); if (!Number.isFinite(n)) throw Error('余额字段不是有效数字'); return n;
} """


def template_script(config: ApiUsageConfig, target: ApiTarget) -> tuple[str, str]:
    """返回脚本及查询基址；官方模板必须匹配真实官方主机。"""
    base = normalize_base(config.base_url or target.base_url)
    host = urlsplit(base).hostname
    if config.template == "custom":
        return config.code, base
    official = {"deepseek": {"api.deepseek.com"}, "siliconflow": {"api.siliconflow.cn", "api.siliconflow.com"},
                "openrouter_key": {"openrouter.ai"}, "openrouter_account": {"openrouter.ai"}}
    if config.template in official:
        target_host = urlsplit(target.base_url).hostname
        if host not in official[config.template] or target_host not in official[config.template]:
            raise UsageError("当前 API 地址与官方模板不匹配，请选择通用或自定义脚本。")
        base = f"https://{host}"
    paths = {"deepseek": "/user/balance", "siliconflow": "/v1/user/info",
             "openrouter_key": "/api/v1/key", "openrouter_account": "/api/v1/credits",
             "new_api": "/api/user/self", "generic": "/user/balance"}
    if config.template == "new_api":
        # New API 的管理接口位于站点根目录，不是聊天的 /v1 子路径。
        parts = urlsplit(base)
        base = urlunsplit((parts.scheme, parts.netloc, parts.path.removesuffix("/v1"), "", ""))
    extractor = {
        "deepseek": """if (!Array.isArray(r.balance_infos) || !r.balance_infos.length) throw Error('余额信息缺失');
            return r.balance_infos.map(function(b) { return {isValid:r.is_available !== false,
              invalidMessage:r.is_available === false ? '账户当前没有可用余额' : null,
              remaining:amount(b.total_balance), unit:b.currency, planName:'账户余额',
              extra:'充值余额：'+amount(b.topped_up_balance)+'；赠金：'+amount(b.granted_balance)}; });""",
        "siliconflow": """if (r.code !== 20000 && r.code !== 0 && r.code !== undefined) throw Error(r.message || '余额查询失败');
            if (!r.data) throw Error('余额信息缺失');
            return {remaining:amount(r.data.totalBalance),unit:UNIT,planName:'账户余额',
              extra:'充值余额：'+amount(r.data.chargeBalance)+'；赠金：'+amount(r.data.balance)};""",
        "openrouter_key": """if (!r.data) throw Error('密钥额度信息缺失'); var d=r.data;
            return {remaining:d.limit == null ? null : amount(d.limit_remaining),
              used:amount(d.usage),total:d.limit == null ? null : amount(d.limit),unit:'USD',
              planName:'密钥额度',extra:d.limit == null ? '未设置密钥限额（不代表账户余额无限）' : ''};""",
        "openrouter_account": """if (!r.data) throw Error('账户余额信息缺失');
            return {remaining:amount(r.data.total_credits)-amount(r.data.total_usage),
              used:amount(r.data.total_usage),total:amount(r.data.total_credits),unit:'USD',planName:'账户余额'};""",
        "new_api": """if (!r.success || !r.data) throw Error(r.message || '账户额度查询失败');
            return {remaining:amount(r.data.quota)/SCALE, used:amount(r.data.used_quota)/SCALE,
              total:(amount(r.data.quota)+amount(r.data.used_quota))/SCALE,
              unit:UNIT,planName:r.data.group || '账户额度'};""",
        "generic": """return {isValid:r.is_active !== false,remaining:amount(r.balance),unit:UNIT};""",
    }[config.template]
    unit = config.unit
    if config.template == "siliconflow":
        unit = "CNY" if host == "api.siliconflow.cn" else "USD"
    extractor = NUMBER_HELPER + extractor.replace("SCALE", str(config.quota_scale)).replace("UNIT", json.dumps(unit))
    headers = {"Authorization": "Bearer {{apiKey}}", "User-Agent": "D_sakiko/api-usage"}
    if config.template == "new_api":
        headers.update({"Authorization": "Bearer {{accessToken}}", "New-Api-User": "{{userId}}"})
    request = {"url": "{{baseUrl}}" + paths[config.template], "method": "GET", "headers": headers}
    return "({request:" + json.dumps(request) + ",extractor:function(r){" + extractor + "}})", base


class UsageScriptRunner:
    """通过有界子进程运行 JS；请求前先检查目标，避免后台隐式确认新域名。"""
    def __call__(self, payload: dict, cancel: threading.Event, started: Callable) -> dict:
        process = subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).with_name("api_usage_worker.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        started(process)
        replies: queue.Queue = queue.Queue()

        def read() -> None:
            try:
                for _ in range(3):
                    line = process.stdout.readline(MAX_BYTES + 1)
                    if not line:
                        break
                    if len(line.encode("utf-8")) > MAX_BYTES:
                        replies.put({"error": "查询结果超过 1 MiB。"})
                        return
                    replies.put(json.loads(line))
            except (OSError, ValueError):
                pass
            finally:
                replies.put({"error": "查询子进程已退出。"})

        threading.Thread(target=read, name="ApiUsageReplies", daemon=True).start()
        deadline = time.monotonic() + payload["timeout"] + 12
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
            process.stdin.flush()
            while not cancel.is_set():
                if time.monotonic() > deadline:
                    raise UsageError("额度查询超时，请稍后刷新。")
                try:
                    reply = replies.get(timeout=0.1)
                except queue.Empty:
                    continue
                if reply.get("stage") == "request":
                    request_origin = origin(reply["url"])
                    if request_origin not in payload["allowed_origins"]:
                        return {"confirmation_origin": request_origin}
                    process.stdin.write('{"continue":true}\n')
                    process.stdin.flush()
                else:
                    return reply
            raise UsageError("查询已取消。")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()


class ApiUsageService:
    """维护当前 API 查询、单独的草稿测试、去重和最后成功快照。"""
    def __init__(self, secrets: UsageSecretStore | None = None, runner: Callable | None = None) -> None:
        self.secrets = secrets or UsageSecretStore()
        self.runner = runner or UsageScriptRunner()
        self._lock = threading.RLock()
        self._jobs: dict[str, tuple[threading.Event, list]] = {}
        self._cache: dict[str, UsageSnapshot] = {}
        self._active = ""
        self._closed = False
        self._threads: set[threading.Thread] = set()

    def _payload(self, target: ApiTarget, config: ApiUsageConfig, overrides: dict | None) -> tuple[dict, tuple[str, ...]]:
        config.validate()
        if target.shared:
            raise UsageError("作者共用 API 不展示共用账户额度，请配置自己的 API。")
        code, base = template_script(config, target)
        override = overrides or {}
        key = (override["api_key"] if "api_key" in override else
               self.secrets.get(config.api_key_ref) if config.api_key_ref else target.api_key)
        access = (override["access_token"] if "access_token" in override else
                  self.secrets.get(config.access_token_ref) if config.access_token_ref else "")
        if config.template == "openrouter_account" and not (config.api_key_ref or override.get("api_key")):
            raise UsageError("账户余额模板需要单独填写 OpenRouter 管理密钥。")
        if config.template == "new_api" and not (access and config.user_id):
            raise UsageError("New API 账户查询需要 Access Token 和 User ID。")
        if config.template not in {"custom", "new_api"} and not key:
            raise UsageError("请先填写当前 API Key。")
        allowed = list(config.approved_origins)
        if target.base_url:
            allowed.append(origin(target.base_url))
        return {"code": code, "variables": {"apiKey": key, "baseUrl": base.rstrip("/"),
                "accessToken": access, "userId": config.user_id}, "timeout": config.timeout,
                "allowed_origins": allowed, "config_revision": config.revision}, (key, access)

    def cancel(self) -> None:
        with self._lock:
            self._active = ""
            for event, processes in self._jobs.values():
                event.set()
                for process in processes:
                    if process.poll() is None:
                        process.terminate()

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.cancel()
        # 等待正在创建子进程的线程进入取消分支，避免解释器退出与 Popen 竞态。
        deadline = time.monotonic() + 2
        with self._lock:
            threads = tuple(self._threads)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))

    def query(self, target: ApiTarget, config: ApiUsageConfig, callback: Callable,
              *, test: bool = False, overrides: dict | None = None) -> bool:
        if self._closed:
            return False
        if not test and (not config.enabled or target.shared):
            self.cancel()
            callback(UsageSnapshot(error="作者共用 API 不展示账户额度。" if target.shared else "请在 API 设置中启用额度查询。"))
            return False
        try:
            payload, secrets = self._payload(target, config, overrides)
        except Exception as error:
            if not test:
                self.cancel()
            callback(UsageSnapshot("error", error=redact(error, (target.api_key,)), checked_at=time.time()))
            return False
        identity = hashlib.sha256(json.dumps([target.config_key, payload], sort_keys=True).encode()).hexdigest()
        job_key = ("test:" if test else "query:") + identity
        with self._lock:
            if self._closed:
                return False
            if job_key in self._jobs and not self._jobs[job_key][0].is_set():
                return False
            if not test and self._active != identity:
                self.cancel()
                self._active = identity
            last = self._cache.get(identity, UsageSnapshot()) if not test else UsageSnapshot()
            event, processes = threading.Event(), []
            self._jobs[job_key] = event, processes
        callback(dataclasses.replace(last, status="loading", error=""))

        def started(process) -> None:
            with self._lock:
                processes.append(process)
                if event.is_set() and process.poll() is None:
                    process.terminate()

        def run() -> None:
            try:
                reply = self.runner(payload, event, started)
                if reply.get("confirmation_origin"):
                    snapshot = dataclasses.replace(last, status="needs_confirmation", stale=bool(last.entries),
                        checked_at=time.time(), error="请确认查询目标后再发送凭据。",
                        confirmation_origin=reply["confirmation_origin"])
                elif reply.get("error"):
                    raise UsageError(reply["error"])
                else:
                    entries = parse_entries(reply.get("data"), secrets)
                    now = time.time()
                    snapshot = UsageSnapshot("success", entries=entries, checked_at=now, last_success_at=now)
            except Exception as error:
                snapshot = dataclasses.replace(last, status="error", checked_at=time.time(),
                                               stale=bool(last.entries), error=redact(error, secrets))
            with self._lock:
                self._threads.discard(threading.current_thread())
                if self._jobs.get(job_key, (None,))[0] is event:
                    self._jobs.pop(job_key, None)
                if self._closed or event.is_set() or (not test and self._active != identity):
                    return
                if not test:
                    self._cache[identity] = snapshot
            callback(snapshot)

        thread = threading.Thread(target=run, name="ApiUsageQuery", daemon=True)
        with self._lock:
            if self._closed or event.is_set():
                if self._jobs.get(job_key, (None,))[0] is event:
                    self._jobs.pop(job_key, None)
                return False
            self._threads.add(thread)
            thread.start()
        return True

    def test(self, target: ApiTarget, config: ApiUsageConfig, callback: Callable,
             overrides: dict | None = None) -> bool:
        return self.query(target, config, callback, test=True, overrides=overrides)
