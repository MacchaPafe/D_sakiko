"""使用系统凭据库保存控制秘密，磁盘回执不包含反馈正文。"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import requests
from platformdirs import user_data_path

from feedback.protocol import convention_header


class SecretStore(Protocol):
    """隔离系统凭据库，便于测试失败和重启恢复。"""

    def set_password(self, service: str, username: str, password: str) -> None:
        """安全保存秘密。"""
        ...

    def get_password(self, service: str, username: str) -> str | None:
        """读取秘密。"""
        ...

    def delete_password(self, service: str, username: str) -> None:
        """删除秘密。"""
        ...


@dataclass(frozen=True)
class Receipt:
    """只保存标题和撤回控制信息的本机回执。"""

    request_id: str
    title: str
    feedback_id: str
    status: str
    created_at: int
    endpoint: str


class ReceiptStore:
    """独立于聊天记录保存回执，正文不落盘。"""

    def __init__(self, path: Path | None = None, secrets_backend: SecretStore | None = None) -> None:
        """初始化最小回执库；秘密仍留在操作系统凭据库。"""
        self.path = path or user_data_path("D_sakiko", appauthor=False) / "feedback" / "receipts.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._secrets = secrets_backend
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS receipts (request_id TEXT PRIMARY KEY, title TEXT NOT NULL, feedback_id TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, endpoint TEXT NOT NULL)")
        self.path.chmod(0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """为当前线程建立短事务连接。"""
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _backend(self) -> SecretStore:
        """只接受系统安全凭据库，不退回明文文件。"""
        if self._secrets is not None:
            return self._secrets
        import keyring
        backend = keyring.get_keyring()
        module = type(backend).__module__
        if module not in {"keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService", "keyring.backends.kwallet"}:
            raise ValueError("系统安全凭据库不可用，无法保存撤回凭据。请启用系统钥匙串/凭据管理器后重试。")
        return backend

    def add(self, request_id: str, title: str, endpoint: str) -> Receipt:
        """首次网络发送之前持久化凭据和待确认记录。"""
        receipt = Receipt(request_id, title, "", "pending", int(time.time()), endpoint)
        backend = self._backend()
        backend.set_password("D_sakiko.feedback", request_id, secrets.token_hex(32))
        try:
            with self._connect() as db:
                db.execute("INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?)", tuple(receipt.__dict__.values()))
        except Exception:
            backend.delete_password("D_sakiko.feedback", request_id)
            raise
        return receipt

    def token(self, request_id: str) -> str:
        """读取对应提交的控制秘密，缺失时停止网络操作。"""
        token = self._backend().get_password("D_sakiko.feedback", request_id)
        if token is None:
            raise ValueError("找不到这次提交的撤回凭据。请使用原设备的系统凭据库。")
        return token

    def all(self) -> list[Receipt]:
        """按提交顺序返回尚未撤回的最小记录。"""
        with self._connect() as db:
            return [Receipt(*row) for row in db.execute("SELECT * FROM receipts ORDER BY created_at DESC, rowid DESC")]

    def confirm(self, request_id: str, feedback_id: str) -> None:
        """记录已收到成功回执。"""
        with self._connect() as db:
            db.execute("UPDATE receipts SET status='submitted', feedback_id=? WHERE request_id=?", (feedback_id, request_id))

    def remove(self, request_id: str) -> None:
        """服务器确认撤回后移除本机凭据和记录。"""
        backend = self._backend()
        if backend.get_password("D_sakiko.feedback", request_id) is not None:
            backend.delete_password("D_sakiko.feedback", request_id)
        with self._connect() as db:
            db.execute("DELETE FROM receipts WHERE request_id=?", (request_id,))


def configured_endpoint() -> str:
    """从发布配置或本机环境读取公开接收地址。"""
    endpoint = os.environ.get("DSAKIKO_FEEDBACK_URL", "").rstrip("/")
    if not endpoint:
        config = Path(__file__).with_name("service.json")
        if config.exists():
            raw: object = json.loads(config.read_text("utf-8"))
            if isinstance(raw, dict):
                endpoint = str(raw.get("url") or "").rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("反馈服务尚未配置。请在部署完成后设置 HTTPS 接收地址。")
    return endpoint


def recipient_notice() -> str:
    """显示发布者填写的接收方和联系方式，不读取任何管理凭据。"""
    config = Path(__file__).with_name("service.json")
    if config.exists():
        try:
            raw: object = json.loads(config.read_text("utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("operator"), str) and isinstance(raw.get("contact"), str):
                return f"接收方：{raw['operator']}。联系方式：{raw['contact']}。"
        except (OSError, ValueError):
            pass
    return "接收方及联系方式见所使用反馈服务的发布说明。"


class FeedbackClient:
    """发送冻结字节和撤回请求，不在网络失败时生成新编号。"""

    def __init__(self, store: ReceiptStore) -> None:
        """共享本机回执存储。"""
        self.store = store

    def _request(self, receipt: Receipt, method: str, body: bytes | None = None) -> dict[str, object]:
        """限制超时和响应体，禁止跟随重定向泄漏正文或控制凭据。"""
        headers = {"X-DSakiko-Feedback": convention_header(receipt.request_id),
                   "X-DSakiko-Control": self.store.token(receipt.request_id), "Content-Type": "application/json"}
        try:
            with requests.request(method, receipt.endpoint + "/v1/feedback", params={"request_id": receipt.request_id} if method == "DELETE" else None,
                                  data=body, headers=headers, timeout=(10, 30), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    errors = {400: "请求已过期或版本不受支持。", 403: "控制凭据不匹配。", 409: "原请求已撤回或正文发生冲突。",
                              413: "内容超过服务端大小限制。", 429: "提交过于频繁或今日额度已满，请稍后重试。", 503: "反馈服务暂时不可用。"}
                    raise ValueError(errors.get(response.status_code, "反馈服务返回异常，请稍后重试。"))
                data = bytearray()
                for chunk in response.iter_content(4096):
                    data.extend(chunk)
                    if len(data) > 16384:
                        raise ValueError("反馈服务返回异常，请稍后重试。")
                result: object = json.loads(data)
                if not isinstance(result, dict) or result.get("ok") is not True or result.get("request_id") != receipt.request_id:
                    raise ValueError("未收到有效回执，仍可重试或撤回。")
                return result
        except (requests.RequestException, json.JSONDecodeError) as exc:
            raise ValueError("网络未确认提交结果。重试会沿用原编号；也可在“已提交反馈”中撤回。") from exc

    def submit(self, receipt: Receipt, body: bytes) -> None:
        """成功后更新回执，不保存正文。"""
        result = self._request(receipt, "POST", body)
        feedback_id = result.get("feedback_id")
        if not isinstance(feedback_id, str) or len(feedback_id) > 100:
            raise ValueError("未收到有效回执，仍可重试或撤回。")
        self.store.confirm(receipt.request_id, feedback_id)

    def withdraw(self, receipt: Receipt) -> None:
        """按客户端编号撤回，即使首次响应丢失也可操作。"""
        self._request(receipt, "DELETE")
        self.store.remove(receipt.request_id)
