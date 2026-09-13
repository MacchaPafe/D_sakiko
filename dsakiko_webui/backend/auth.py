from __future__ import annotations

import hashlib
import hmac
import logging
import math
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal


COOKIE_NAME = "dsakiko_session"
PAIRING_TTL_SECONDS = 5 * 60
PAIRING_RETRY_SECONDS = 30
SOURCE_RECORD_LIMIT = 4096
PAIRING_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
logger = logging.getLogger(__name__)

PairingStatus = Literal["active", "used", "expired"]


@dataclass(frozen=True)
class SessionReplacement:
    """描述一次认证签发的会话以及是否替换旧控制端。"""

    token: str
    replaced_token: str | None
    replaced_existing_controller: bool
    idempotent: bool = False


@dataclass(frozen=True)
class PairingGrant:
    """携带仅供本机展示适配器使用的原始配对凭证。"""

    token: str
    expires_at: float


@dataclass(frozen=True)
class PairingSnapshot:
    """提供不含原始凭证的配对状态快照。"""

    status: PairingStatus
    expires_at: float | None
    remaining_seconds: int
    connected: bool


class AccessCodeRejected(ValueError):
    """表示六位访问码校验失败。"""


class PairingRejected(ValueError):
    """表示配对凭证无效、过期或已经被其他客户端使用。"""


class AccessRateLimited(RuntimeError):
    """表示当前来源需要等待后才能再次尝试访问码。"""

    def __init__(self, retry_after_seconds: int) -> None:
        """记录服务端要求的最短等待秒数。"""
        super().__init__("访问尝试过于频繁")
        self.retry_after_seconds = max(1, retry_after_seconds)


@dataclass
class _SourceRateState:
    """保存单个来源的令牌桶、失败窗口和冷却状态。"""

    tokens: float
    last_refill_at: float
    failures: deque[float] = field(default_factory=deque)
    applied_thresholds: set[int] = field(default_factory=set)
    cooldown_until: float = 0.0
    last_active_at: float = 0.0
    rate_log_until: float = 0.0


class AccessController:
    """统一管理访问码、配对凭证、单控制端会话与认证限速。"""

    _FAILURE_WINDOW_SECONDS = 15 * 60
    _GLOBAL_WINDOW_SECONDS = 10 * 60
    _DEFENSE_MINIMUM_SECONDS = 10 * 60
    _NORMAL_CAPACITY = 5.0
    _NORMAL_REFILL_SECONDS = 12.0
    _DEFENSE_CAPACITY = 2.0
    _DEFENSE_REFILL_SECONDS = 30.0
    _COOLDOWNS = ((20, 60), (30, 10 * 60), (40, 30 * 60))

    def __init__(
        self,
        access_code: str | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """创建仅存在于当前进程生命周期内的访问控制状态。"""
        self._access_code = access_code or f"{secrets.randbelow(1_000_000):06d}"
        self._clock = clock
        self._lock = threading.RLock()
        self._active_token: str | None = None
        self._client_session_id: str | None = None
        self._pairing_digest: bytes | None = None
        self._pairing_expires_at: float | None = None
        self._pairing_used = False
        self._pairing_client_session_id: str | None = None
        self._pairing_session_token: str | None = None
        self._pairing_retry_until = 0.0
        self._sources: dict[str, _SourceRateState] = {}
        initial_now = self._clock()
        self._overflow_source = _SourceRateState(
            self._NORMAL_CAPACITY,
            initial_now,
            last_active_at=initial_now,
        )
        self._global_failures: deque[float] = deque()
        self._defense_started_at: float | None = None

    @property
    def access_code(self) -> str:
        """返回本次启动生成的备用六位访问码。"""
        return self._access_code

    def login_with_access_code(
        self,
        access_code: str,
        source_ip: str,
        client_session_id: str | None = None,
    ) -> SessionReplacement:
        """校验来源限速与访问码，并签发新的单控制端会话。"""
        normalized_ip = self._normalize_source(source_ip)
        with self._lock:
            now = self._clock()
            self._refresh_global_state(now)
            state = self._source_state(normalized_ip, now)
            retry_after = self._retry_after(state, now)
            if retry_after > 0:
                self._log_rate_limit(normalized_ip, state, now, retry_after)
                raise AccessRateLimited(retry_after)

            state.tokens -= 1.0
            state.last_active_at = now
            if not hmac.compare_digest(access_code, self._access_code):
                self._record_failure(normalized_ip, state, now)
                raise AccessCodeRejected("访问码错误")

            if state is self._overflow_source:
                capacity, _ = self._bucket_policy()
                self._overflow_source = _SourceRateState(
                    capacity,
                    now,
                    last_active_at=now,
                )
            else:
                self._sources.pop(normalized_ip, None)
            return self._issue_session(client_session_id)

    def regenerate_pairing(self) -> PairingGrant:
        """撤销旧配对凭证并生成新的五分钟一次性凭证。"""
        raw_token = secrets.token_urlsafe(32)
        digest = self._digest_pairing_token(raw_token)
        with self._lock:
            expires_at = self._clock() + PAIRING_TTL_SECONDS
            self._pairing_digest = digest
            self._pairing_expires_at = expires_at
            self._pairing_used = False
            self._pairing_client_session_id = None
            self._pairing_session_token = None
            self._pairing_retry_until = 0.0
            return PairingGrant(raw_token, expires_at)

    def login_with_pairing(
        self,
        pairing_token: str,
        client_session_id: str,
    ) -> SessionReplacement:
        """原子兑换一次性配对凭证，并允许同客户端短时幂等重试。"""
        if not PAIRING_TOKEN_PATTERN.fullmatch(pairing_token) or not client_session_id:
            raise PairingRejected("配对凭证无效")
        digest = self._digest_pairing_token(pairing_token)
        with self._lock:
            now = self._clock()
            if self._pairing_digest is None or not hmac.compare_digest(
                digest,
                self._pairing_digest,
            ):
                raise PairingRejected("配对凭证无效")

            if self._pairing_used:
                if (
                    now <= self._pairing_retry_until
                    and client_session_id == self._pairing_client_session_id
                    and self._pairing_session_token is not None
                    and hmac.compare_digest(
                        self._pairing_session_token,
                        self._active_token or "",
                    )
                ):
                    return SessionReplacement(
                        token=self._pairing_session_token,
                        replaced_token=None,
                        replaced_existing_controller=False,
                        idempotent=True,
                    )
                raise PairingRejected("配对凭证已经使用")

            if self._pairing_expires_at is None or now >= self._pairing_expires_at:
                raise PairingRejected("配对凭证已经过期")

            replacement = self._issue_session(client_session_id)
            self._pairing_used = True
            self._pairing_client_session_id = client_session_id
            self._pairing_session_token = replacement.token
            self._pairing_retry_until = now + PAIRING_RETRY_SECONDS
            return replacement

    def pairing_snapshot(self) -> PairingSnapshot:
        """返回不泄漏原始凭证的当前配对状态。"""
        with self._lock:
            now = self._clock()
            if self._pairing_used:
                status: PairingStatus = "used"
                remaining = 0
            elif self._pairing_expires_at is None or now >= self._pairing_expires_at:
                status = "expired"
                remaining = 0
            else:
                status = "active"
                remaining = max(0, math.ceil(self._pairing_expires_at - now))
            return PairingSnapshot(
                status=status,
                expires_at=self._pairing_expires_at,
                remaining_seconds=remaining,
                connected=self._active_token is not None,
            )

    def is_authenticated(self, token: str | None) -> bool:
        """判断 Cookie 中的会话令牌是否仍是唯一活动会话。"""
        with self._lock:
            return bool(
                token
                and self._active_token
                and hmac.compare_digest(token, self._active_token)
            )

    def logout(self, token: str | None) -> bool:
        """注销匹配的活动会话。"""
        with self._lock:
            if not self.is_authenticated(token):
                return False
            self._active_token = None
            self._client_session_id = None
            return True

    def _issue_session(self, client_session_id: str | None) -> SessionReplacement:
        """签发随机会话令牌并替换原控制端。"""
        old_token = self._active_token
        new_token = secrets.token_urlsafe(32)
        self._active_token = new_token
        self._client_session_id = client_session_id
        return SessionReplacement(
            token=new_token,
            replaced_token=old_token,
            replaced_existing_controller=old_token is not None,
        )

    def _source_state(self, source_ip: str, now: float) -> _SourceRateState:
        """取得来源记录，并在必要时清理过期记录控制内存。"""
        state = self._sources.get(source_ip)
        if state is None:
            self._prune_sources(now)
            if len(self._sources) >= SOURCE_RECORD_LIMIT:
                return self._overflow_source
            capacity, _ = self._bucket_policy()
            state = _SourceRateState(capacity, now, last_active_at=now)
            self._sources[source_ip] = state
        return state

    def _retry_after(self, state: _SourceRateState, now: float) -> int:
        """更新令牌桶并返回当前需要等待的秒数。"""
        self._trim_failures(state, now)
        capacity, refill_seconds = self._bucket_policy()
        elapsed = max(0.0, now - state.last_refill_at)
        state.tokens = min(capacity, state.tokens + elapsed / refill_seconds)
        state.last_refill_at = now
        waits = [max(0.0, state.cooldown_until - now)]
        if state.tokens < 1.0:
            waits.append((1.0 - state.tokens) * refill_seconds)
        return math.ceil(max(waits))

    def _record_failure(
        self,
        source_ip: str,
        state: _SourceRateState,
        now: float,
    ) -> None:
        """记录一次真实的错误访问码并应用跨越的冷却门槛。"""
        state.failures.append(now)
        self._global_failures.append(now)
        count = len(state.failures)
        for threshold, cooldown_seconds in self._COOLDOWNS:
            if count >= threshold and threshold not in state.applied_thresholds:
                state.applied_thresholds.add(threshold)
                state.cooldown_until = max(state.cooldown_until, now + cooldown_seconds)
                logger.warning(
                    "WebUI 访问码来源 %s 达到 %s 次失败，冷却 %s 秒",
                    source_ip,
                    threshold,
                    cooldown_seconds,
                )
        self._refresh_global_state(now)

    def _trim_failures(self, state: _SourceRateState, now: float) -> None:
        """移除十五分钟窗口外的来源失败记录并回退门槛阶段。"""
        cutoff = now - self._FAILURE_WINDOW_SECONDS
        while state.failures and state.failures[0] <= cutoff:
            state.failures.popleft()
        count = len(state.failures)
        state.applied_thresholds = {
            threshold for threshold in state.applied_thresholds if count >= threshold
        }

    def _refresh_global_state(self, now: float) -> None:
        """维护十分钟全局失败窗口与防御模式滞回。"""
        cutoff = now - self._GLOBAL_WINDOW_SECONDS
        while self._global_failures and self._global_failures[0] <= cutoff:
            self._global_failures.popleft()
        if self._defense_started_at is None and len(self._global_failures) >= 100:
            self._defense_started_at = now
            logger.warning("WebUI 访问码进入全局防御模式")
        elif (
            self._defense_started_at is not None
            and now - self._defense_started_at >= self._DEFENSE_MINIMUM_SECONDS
            and len(self._global_failures) < 20
        ):
            self._defense_started_at = None
            logger.warning("WebUI 访问码退出全局防御模式")

    def _bucket_policy(self) -> tuple[float, float]:
        """返回当前模式下的令牌桶容量与单令牌恢复秒数。"""
        if self._defense_started_at is not None:
            return self._DEFENSE_CAPACITY, self._DEFENSE_REFILL_SECONDS
        return self._NORMAL_CAPACITY, self._NORMAL_REFILL_SECONDS

    def _prune_sources(self, now: float) -> None:
        """清理无活动来源，并将来源记录表限制在固定规模。"""
        stale_before = now - self._FAILURE_WINDOW_SECONDS
        stale = [
            source
            for source, state in self._sources.items()
            if state.last_active_at <= stale_before and state.cooldown_until <= now
        ]
        for source in stale:
            self._sources.pop(source, None)
        if len(self._sources) < SOURCE_RECORD_LIMIT:
            return
        candidates = sorted(
            (
                (state.last_active_at, source)
                for source, state in self._sources.items()
                if state.cooldown_until <= now
            ),
        )
        if candidates:
            self._sources.pop(candidates[0][1], None)

    def _log_rate_limit(
        self,
        source_ip: str,
        state: _SourceRateState,
        now: float,
        retry_after: int,
    ) -> None:
        """同一等待区间只记录一次来源限速日志。"""
        limited_until = now + retry_after
        if state.rate_log_until >= limited_until:
            return
        state.rate_log_until = limited_until
        logger.warning(
            "WebUI 访问码来源 %s 被限速，等待 %s 秒",
            source_ip,
            retry_after,
        )

    @staticmethod
    def _normalize_source(source_ip: str) -> str:
        """规范化直接 TCP 对端地址并为缺失值提供稳定标识。"""
        return source_ip.strip().lower() or "unknown"

    @staticmethod
    def _digest_pairing_token(pairing_token: str) -> bytes:
        """计算配对凭证摘要，避免在控制状态中保存原文。"""
        return hashlib.sha256(pairing_token.encode("ascii")).digest()
