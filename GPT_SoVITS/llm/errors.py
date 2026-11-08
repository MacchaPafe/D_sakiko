"""供应商无关的模型请求错误。"""
from __future__ import annotations

from collections.abc import Iterable


class LLMError(RuntimeError):
    """保留可处理的类别与服务端状态，不在错误中保存凭据。"""

    def __init__(self, code: str, message: str, *, status_code: int | None = None,
                 provider_code: str = "", original: BaseException | None = None,
                 request_id: str = "", body: str = "", action: str = "", param: str = "") -> None:
        """创建统一错误并保留原始异常供本地诊断。"""
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.provider_code = provider_code
        self.original = original
        self.request_id = request_id
        self.body = body
        self.action = action
        self.param = param


def redact_error(error: LLMError, secrets: Iterable[str]) -> LLMError:
    """保留错误语义和状态，阻止服务端回显或异常链泄漏本次凭据。"""
    for secret in secrets:
        if secret:
            error.args = (str(error).replace(secret, "[已隐藏凭据]"),)
            error.body = error.body.replace(secret, "[已隐藏凭据]")
    error.original = None
    return error


def response_error(status: int, data: object, request_id: str = "") -> LLMError:
    """保留订阅准入、额度及标准错误形状，不把所有拒绝都当令牌过期。"""
    import json
    payload = data if isinstance(data, dict) else {}
    error = payload.get("error", payload)
    oauth_code = error if isinstance(error, str) else ""
    error = error if isinstance(error, dict) else {}
    provider_code = str(error.get("code") or oauth_code or "")
    message = str(error.get("message") or payload.get("detail") or payload.get("error_description") or f"模型请求失败（HTTP {status}）。")
    code = "authentication" if status == 401 else "permission" if status == 403 else "rate_limit" if status == 429 else "request_failed"
    action = ""
    if provider_code == "subscription_sharing_usage_limit_exceeded":
        code, action = "quota", "manage_usage"
        message = "ChatGPT 订阅或数字小祥的使用限额已达到，请打开配置中的“管理用量”。"
    elif provider_code == "subscription_sharing_user_not_eligible":
        code = "permission"
        message = "当前 ChatGPT 账户、工作区或策略不允许使用订阅额度。"
    elif provider_code == "subscription_sharing_unsupported_capability":
        code = "unsupported_parameter"
        message += " 参数：" + str(error.get("param") or "未知")
    elif status == 503 or provider_code in {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"}:
        action = "retry_later"
    return LLMError(code, message, status_code=status, provider_code=provider_code,
                    request_id=request_id, body=json.dumps(data, ensure_ascii=False), action=action,
                    param=str(error.get("param") or ""))


def normalize_error(error: Exception) -> LLMError:
    """依据明确异常类型及状态分类，不把未知参数猜成不支持。"""
    if isinstance(error, LLMError):
        return error
    name = type(error).__name__.lower()
    status = getattr(error, "status_code", None)
    message = str(error)
    code = "request_failed"
    if "timeout" in name:
        code = "timeout"
    elif status == 401 or "authentication" in name:
        code = "authentication"
    elif status == 402 or "insufficient" in message.lower():
        code = "quota"
    elif status == 429 or "ratelimit" in name:
        code = "rate_limit"
    elif status == 403:
        code = "permission"
    elif "unsupportedparam" in name or "unsupported_parameter" in message:
        code = "unsupported_parameter"
    elif status == 400:
        code = "bad_request"
    elif "connection" in name:
        code = "connection"
    return LLMError(code, message, status_code=status if isinstance(status, int) else None,
                    provider_code=str(getattr(error, "code", "") or ""), original=error)
