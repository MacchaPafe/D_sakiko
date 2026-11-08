"""受保护的凭据存储、稳定注册身份及跨进程会话锁。"""
from __future__ import annotations

import json
import os
import csv
import io
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from filelock import FileLock, Timeout
from platformdirs import user_data_path

from ..errors import LLMError


class Vault(Protocol):
    """隔离操作系统凭据库，使失败降级能够离线验证。"""

    def get_password(self, service: str, username: str) -> str | None:
        """读取指定连接的凭据。"""
        ...

    def set_password(self, service: str, username: str, password: str) -> None:
        """保存指定连接的凭据。"""
        ...

    def delete_password(self, service: str, username: str) -> None:
        """删除指定连接的凭据。"""
        ...


@dataclass
class Account:
    """可供 UI 展示的注册信息，不包含令牌。"""

    id: str
    client_id: str = ""
    email: str = ""
    issuer: str = ""
    subject: str = ""
    scopes: list[str] = field(default_factory=list)
    status: str = "pending"
    credential_store: str = ""

    @property
    def sharing(self) -> bool:
        """登录身份和订阅使用权限分别判断。"""
        return self.status == "connected" and "chatgpt.tokens.use.direct" in self.scopes

    @property
    def label(self) -> str:
        """同邮箱的不同注册仍显示不同的连接标签。"""
        return f"{self.email or '未完成的登录'} · {self.id[:6]}"

    @property
    def identity(self) -> str:
        """凭据刷新不改变连接身份，账户或注册变化则始终隔离。"""
        return json.dumps([self.id, self.client_id, self.issuer, self.subject], ensure_ascii=False)


@dataclass(repr=False)
class Tokens:
    """只在认证运行时内使用的令牌集合，不返回给界面。"""

    access_token: str
    refresh_token: str
    id_token: str
    expires_at: float


def default_directory() -> Path:
    """使用用户数据目录，不把可用令牌放入项目或普通配置。"""
    return user_data_path("D_sakiko", appauthor=False) / "chatgpt"


def write_private_json(path: Path, value: object) -> None:
    """以用户私有临时文件原子替换数据，写入失败不覆盖旧记录。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.parent.chmod(0o700)
    elif not (path.parent / ".private-acl").exists():
        # Windows 的 chmod(0600) 不限制读取者，独立设置当前用户的目录 ACL。
        identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
            check=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        sid = next(csv.reader(io.StringIO(identity.stdout)))[-1]
        if not sid.startswith("S-1-"):
            raise RuntimeError("无法确定当前 Windows 用户身份，未保存登录凭据。")
        subprocess.run(["icacls", str(path.parent), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
            check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        (path.parent / ".private-acl").touch()
    descriptor, name = tempfile.mkstemp(prefix=".saving-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class AccountStore:
    """管理唯一注册表和凭据位置；文件降级后不会再读旧钥匙串副本。"""

    service = "D_sakiko.ChatGPT"

    def __init__(self, directory: Path | None = None, vault: Vault | None = None) -> None:
        """允许注入存储目录及凭据库，测试不访问真实账户。"""
        self.directory = directory or default_directory()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.directory.chmod(0o700)
        if vault is None:
            import keyring
            vault = keyring
        self.vault = vault
        self.lock = FileLock(str(self.directory / "accounts.lock"), timeout=15)

    def _read(self) -> dict[str, object]:
        """读取注册表；损坏时明确报错，不默默重建 Host ID。"""
        path = self.directory / "accounts.json"
        if not path.exists():
            return {"version": 1, "host_id": "urn:uuid:" + str(uuid.uuid4()), "accounts": []}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("accounts"), list):
            raise LLMError("authentication", "ChatGPT 本地注册表损坏或版本不受支持。")
        return data

    def host_id(self) -> str:
        """首次创建后固定本机身份，账户切换或退出不重建。"""
        with self.lock:
            data = self._read()
            write_private_json(self.directory / "accounts.json", data)
            return str(data["host_id"])

    def list_accounts(self) -> list[Account]:
        """返回无令牌的账户记录副本。"""
        with self.lock:
            return [Account(**record) for record in self._read()["accounts"]]

    def get(self, identifier: str) -> Account:
        """按本地注册身份查找，不用邮箱合并不同工作区。"""
        for account in self.list_accounts():
            if account.id == identifier:
                return account
        raise LLMError("authentication", "ChatGPT 连接不存在，请在配置程序中登录。")

    def save_account(self, account: Account) -> None:
        """更新一条注册的元数据，不接触其他注册。"""
        with self.lock:
            data = self._read()
            data["accounts"] = [record for record in data["accounts"] if record["id"] != account.id] + [asdict(account)]
            write_private_json(self.directory / "accounts.json", data)

    def session_lock(self, identifier: str) -> FileLock:
        """同一会话的刷新、重新登录和注销在所有进程间串行。"""
        uuid.UUID(identifier)
        return FileLock(str(self.directory / (identifier + ".session.lock")), timeout=30)

    def save_tokens(self, account: Account, tokens: Tokens) -> None:
        """优先凭据库；写入失败时静默转入独立文件并固定权威位置。"""
        content = json.dumps(asdict(tokens))
        if account.credential_store != "file":
            try:
                self.vault.set_password(self.service, account.id, content)
                account.credential_store = "keyring"
            except Exception:
                account.credential_store = "file"
        if account.credential_store == "file":
            write_private_json(self.directory / "credentials" / (account.id + ".json"), asdict(tokens))
        self.save_account(account)

    def tokens(self, account: Account) -> Tokens:
        """只读取该注册记录声明的位置，不回退到可能已失效的旧令牌。"""
        try:
            if account.credential_store == "file":
                raw = (self.directory / "credentials" / (account.id + ".json")).read_text(encoding="utf-8")
            elif account.credential_store == "keyring":
                raw = self.vault.get_password(self.service, account.id)
            else:
                raw = None
            if not raw:
                raise ValueError("missing credentials")
            return Tokens(**json.loads(raw))
        except Exception as exc:
            raise LLMError("authentication", "无法读取 ChatGPT 登录凭据，请重新登录。") from exc

    def clear_tokens(self, account: Account) -> str:
        """清理秘密并禁用注册；凭据库清理失败也不继续使用旧会话。"""
        warning = ""
        if account.credential_store == "keyring":
            try:
                self.vault.delete_password(self.service, account.id)
            except Exception:
                warning = "本地登录已停用，但系统凭据库中的旧记录未能删除。"
        try:
            (self.directory / "credentials" / (account.id + ".json")).unlink(missing_ok=True)
        except OSError:
            warning += " 本地登录已停用，但凭据文件未能删除。"
        account.status = "signed-out"
        account.credential_store = ""
        self.save_account(account)
        return warning.strip()

    def assert_idle(self, identifier: str) -> None:
        """注销前检查所有进程的使用租约，进程退出后的旧锁不算忙碌。"""
        for path in self.directory.glob(identifier + ".use-*.lock"):
            lock = FileLock(path, timeout=0)
            try:
                lock.acquire()
            except Timeout as exc:
                raise LLMError("account_busy", "该账户仍有生成任务，请先停止生成再退出登录。") from exc
            else:
                lock.release()
                path.unlink(missing_ok=True)

    def lease(self, identifier: str) -> AccountLease:
        """开始一次受保护的使用，和注销检查互斥建立租约。"""
        return AccountLease(self, identifier)


class AccountLease:
    """标记仍在使用某注册的请求或轮次，不串行化不同生成。"""

    def __init__(self, store: AccountStore, identifier: str, *, locked: bool = False) -> None:
        """在会话锁内建立独立租约，避免与同时退出竞态。"""
        self.path = store.directory / (identifier + ".use-" + uuid.uuid4().hex + ".lock")
        self.lock = FileLock(self.path)
        self.closed = False
        from contextlib import nullcontext
        with (nullcontext() if locked else store.session_lock(identifier)):
            if not store.get(identifier).sharing:
                raise LLMError("authentication", "该 ChatGPT 账户尚未登录或未授权使用订阅额度。")
            self.lock.acquire()

    def close(self) -> None:
        """释放本进程持有的使用状态，可重复调用。"""
        if not self.closed:
            self.lock.release()
            self.path.unlink(missing_ok=True)
            self.closed = True
