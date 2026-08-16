from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass


COOKIE_NAME = "dsakiko_session"


@dataclass(frozen=True)
class SessionReplacement:
    token: str
    replaced_token: str | None


class SingleControllerAuth:
    """只保留一个控制端；访问码正确的新设备会接管旧会话。"""

    def __init__(self, access_code: str | None = None) -> None:
        self.access_code = access_code or f"{secrets.randbelow(1_000_000):06d}"
        self.active_token: str | None = None
        self.client_session_id: str | None = None

    def login(self, access_code: str, client_session_id: str | None = None) -> SessionReplacement:
        if not hmac.compare_digest(access_code, self.access_code):
            raise ValueError("访问码错误")

        old_token = self.active_token
        self.active_token = secrets.token_urlsafe(32)
        self.client_session_id = client_session_id
        return SessionReplacement(self.active_token, old_token)

    def is_authenticated(self, token: str | None) -> bool:
        return bool(token and self.active_token and hmac.compare_digest(token, self.active_token))

    def logout(self, token: str | None) -> bool:
        if not self.is_authenticated(token):
            return False
        self.active_token = None
        self.client_session_id = None
        return True
