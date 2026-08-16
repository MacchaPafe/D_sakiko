from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


PROTOCOL_VERSION = 1


class SessionRequest(BaseModel):
    access_code: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)


class CommandEnvelope(BaseModel):
    protocol_version: int
    kind: str
    type: str
    request_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any]


@dataclass
class ProtocolError(Exception):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


def command_result(
    request_id: str,
    *,
    data: dict[str, Any] | None = None,
    error: ProtocolError | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "kind": "response",
        "type": "command_result",
        "request_id": request_id,
        "ok": error is None,
        "data": data if error is None else None,
        "error": error.as_dict() if error else None,
    }


def http_error(error: ProtocolError) -> dict[str, Any]:
    return {"error": error.as_dict()}
