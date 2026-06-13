from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum


USER_LIMIT_HEADER = "modelscope-ratelimit-requests-limit"
USER_REMAINING_HEADER = "modelscope-ratelimit-requests-remaining"
MODEL_LIMIT_HEADER = "modelscope-ratelimit-model-requests-limit"
MODEL_REMAINING_HEADER = "modelscope-ratelimit-model-requests-remaining"
RETRY_AFTER_HEADER = "retry-after"


class ModelScopeRateLimitKind(str, Enum):
    """描述 ModelScope 429 的业务分类。"""

    USER_QUOTA_EXHAUSTED = "user_quota_exhausted"
    MODEL_QUOTA_EXHAUSTED = "model_quota_exhausted"
    TEMPORARY_RATE_LIMIT = "temporary_rate_limit"


@dataclass(frozen=True)
class ModelScopeQuota:
    """保存 ModelScope 响应头中的用户和单模型额度。"""

    user_limit: int | None
    user_remaining: int | None
    model_limit: int | None
    model_remaining: int | None


class ModelScopeQuotaExceededError(Exception):
    """表示 ModelScope 的用户或单模型每日额度已经耗尽。"""

    def __init__(
        self,
        kind: ModelScopeRateLimitKind,
        quota: ModelScopeQuota,
        original_error: BaseException,
    ) -> None:
        """保存额度分类、额度数据和原始 429 异常。"""

        self.kind: ModelScopeRateLimitKind = kind
        self.quota: ModelScopeQuota = quota
        self.original_error: BaseException = original_error
        self.response: object = getattr(original_error, "response", None)
        super().__init__(kind.value)


def _parse_non_negative_int(value: str | None) -> int | None:
    """将响应头解析为非负整数，非法值返回 None。"""

    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def normalize_headers(headers: object) -> dict[str, str]:
    """将未知响应头对象转换为不区分大小写的普通字典。"""

    items_method = getattr(headers, "items", None)
    if not callable(items_method):
        return {}
    try:
        raw_items = items_method()
    except Exception:
        return {}

    normalized: dict[str, str] = {}
    try:
        for raw_name, raw_value in raw_items:
            normalized[str(raw_name).lower()] = str(raw_value)
    except (TypeError, ValueError):
        return {}
    return normalized


def extract_error_headers(error: BaseException) -> dict[str, str]:
    """从 LiteLLM/OpenAI 风格异常中提取响应头。"""

    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    return normalize_headers(headers)


def parse_quota_headers(headers: Mapping[str, str]) -> ModelScopeQuota:
    """解析 ModelScope 官方额度响应头。"""

    normalized = {str(name).lower(): str(value) for name, value in headers.items()}
    return ModelScopeQuota(
        user_limit=_parse_non_negative_int(normalized.get(USER_LIMIT_HEADER)),
        user_remaining=_parse_non_negative_int(normalized.get(USER_REMAINING_HEADER)),
        model_limit=_parse_non_negative_int(normalized.get(MODEL_LIMIT_HEADER)),
        model_remaining=_parse_non_negative_int(normalized.get(MODEL_REMAINING_HEADER)),
    )


def classify_rate_limit(quota: ModelScopeQuota) -> ModelScopeRateLimitKind:
    """根据剩余额度判断 429 是额度耗尽还是临时限流。"""

    if quota.user_remaining == 0:
        return ModelScopeRateLimitKind.USER_QUOTA_EXHAUSTED
    if quota.model_remaining == 0:
        return ModelScopeRateLimitKind.MODEL_QUOTA_EXHAUSTED
    return ModelScopeRateLimitKind.TEMPORARY_RATE_LIMIT


def parse_retry_after_seconds(
    headers: Mapping[str, str],
    *,
    now: datetime | None = None,
) -> float | None:
    """解析 Retry-After 的秒数或 HTTP 日期格式。"""

    normalized = {str(name).lower(): str(value) for name, value in headers.items()}
    raw_value = normalized.get(RETRY_AFTER_HEADER)
    if raw_value is None:
        return None

    try:
        seconds = float(raw_value)
    except ValueError:
        seconds = -1.0
    if seconds >= 0:
        return seconds

    try:
        retry_at = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - current_time).total_seconds())
