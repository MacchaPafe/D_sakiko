from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from threading import Event
import tempfile
import time
from urllib.parse import quote, urlencode

import requests
from .catalog import CACHE_ROOT

ORIGIN = "https://models.dsakiko.org"


class DownloadError(RuntimeError):
    pass


class Cancelled(DownloadError):
    pass


def check_cancel(cancel: Event):
    """检查任务取消事件，停止后续网络读取或文件发布。"""
    if cancel.is_set():
        raise Cancelled("下载已取消")


def validate_key(key: str) -> str:
    """校验 R2 相对路径，拒绝越界路径和 Windows 特殊文件名。"""
    if not isinstance(key, str) or not key.startswith(("ournotes/", "images/")):
        raise DownloadError("资源路径不合法")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in (".", "..")
           or part.endswith((".", " ")) or part.split('.')[0].upper() in
           {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
            *[f"LPT{i}" for i in range(1, 10)]} for part in key.split("/")):
        raise DownloadError("资源路径不合法")
    return key


@dataclass(frozen=True)
class Resource:
    kind: str
    key: str
    title: str
    character: str = ""
    sha256: str | None = None
    model_id: str = ""

    @property
    def filename(self):
        """返回资源路径中的文件名，用于本地保存。"""
        return self.key.rsplit("/", 1)[-1]

    @property
    def preview_key(self):
        """推导背景缩略图或头像预览路径，模型不请求缩略图。"""
        if self.kind == "background":
            return self.key.replace("images/backgrounds/", "images/background-previews/", 1)
        return self.key if self.kind == "avatar" else None


def configured_token():
    """读取环境覆盖值或私有发行凭证，不读取上传管理凭据。"""
    token = os.environ.get("DSAKIKO_DOWNLOAD_TOKEN", "").strip()
    if not token:
        try:
            from ._credentials import DOWNLOAD_TOKEN
            token = DOWNLOAD_TOKEN.strip()
        except ImportError:
            pass
    return token


class ResourceService:
    def __init__(self, token: str | None = None):
        """初始化只用于自有资源域名的下载凭证。"""
        self.token = configured_token() if token is None else token

    def _read(self, path, cancel, consume):
        """向固定自有域名发起请求，禁止携带 Token 跟随重定向。"""
        check_cancel(cancel)
        if not self.token:
            raise DownloadError("未配置资源下载凭证，请使用完整发行包")
        try:
            with requests.Session() as session:
                # 每个请求独立 Session；Token 不进入日志、查询参数或其他来源。
                session.headers.update({"Authorization": "Bearer " + self.token,
                                        "User-Agent": "D-Sakiko-Downloader/2.0",
                                        "Accept-Encoding": "identity"})
                with session.get(ORIGIN + path, stream=True, timeout=(10, 15),
                                 allow_redirects=False) as response:
                    if response.status_code != 200:
                        message = {401: "下载凭证无效", 403: "请求被服务端拦截", 404: "资源不存在",
                                   429: "请求过于频繁，请稍后重试", 503: "资源服务暂时不可用"}.get(
                                       response.status_code, f"请求失败（HTTP {response.status_code}）")
                        raise DownloadError(message)
                    return consume(response)
        except requests.RequestException:
            check_cancel(cancel)
            raise DownloadError("网络连接失败或超时，请重试") from None

    def _bytes(self, path, cancel, limit):
        """以可取消的方式读取有限大小的目录或预览数据。"""
        def consume(response):
            """消费响应数据并检查取消状态，在发布文件前完成必要校验。"""
            result = bytearray()
            for chunk in response.iter_content(65536):
                check_cancel(cancel)
                result.extend(chunk)
                if len(result) > limit:
                    raise DownloadError("资源超过预览大小限制")
            check_cancel(cancel)
            return bytes(result)
        return self._read(path, cancel, consume)

    def catalog(self, kind: str, character: str, cancel: Event):
        """查询并校验资源目录，明确区分空目录与网络失败。"""
        if kind not in ("model", "background", "avatar"):
            raise DownloadError("未知资源类型")
        if kind != "background" and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", character):
            raise DownloadError("角色标识不合法")
        params = {"character": character} if kind == "model" else {"kind": kind}
        if kind == "avatar":
            params["character"] = character
        path = ("/api/v3/models" if kind == "model" else "/api/images") + "?" + urlencode(params)
        try:
            data = json.loads(self._bytes(path, cancel, 4 * 1024 * 1024))
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError()
            result = []
            keys = set()
            for row in data["items"]:
                key = validate_key(row["r2_key"])
                prefix = {"model": f"ournotes/{character}/", "background": "images/backgrounds/",
                          "avatar": f"images/chat-icons/{character}/"}[kind]
                if not key.startswith(prefix) or not key.endswith(".zip" if kind == "model" else ".png"):
                    raise ValueError()
                if kind != "background" and row["character_id"] != character:
                    raise ValueError()
                if kind != "model" and row["kind"] != kind:
                    raise ValueError()
                digest = row.get("sha256")
                if digest is not None and not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise ValueError()
                if key in keys:
                    raise ValueError()
                keys.add(key)
                model_id = row.get("model_id", "")
                if kind == "model" and (not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", model_id)):
                    raise ValueError()
                title = row.get("name") or model_id or key.rsplit("/", 1)[-1]
                if not isinstance(title, str):
                    raise ValueError()
                result.append(Resource(kind, key, title, character, digest, model_id))
            return result
        except (ValueError, KeyError, TypeError):
            raise DownloadError("资源目录格式不正确") from None

    def preview(self, key, cancel, refresh=False):
        """读取或显示资源预览；失败时由界面提供占位和重试。"""
        validate_key(key)
        if not key.startswith("images/"):
            raise DownloadError("预览路径不合法")
        check_cancel(cancel)
        cache = CACHE_ROOT / "resource-previews"
        target = cache / (hashlib.sha256(key.encode()).hexdigest() + ".png")
        if not refresh and target.is_file() and time.time() - target.stat().st_mtime < 6 * 3600:
            return target.read_bytes()
        content = self._bytes("/" + quote(key, safe="/"), cancel, 12 * 1024 * 1024)
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DownloadError("预览图片格式错误")
        check_cancel(cancel)
        # 缓存写入失败不影响本次显示；固定路径图片最长六小时重新获取。
        temporary = None
        try:
            cache.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=cache, suffix=".part")
            temporary = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
            temporary.replace(target)
        except OSError:
            pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return content

    def download(self, resource: Resource, directory: Path, cancel: Event, progress):
        """执行后台下载；成功后才发布结果，失败或取消清理临时文件。"""
        validate_key(resource.key)
        directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".download-", suffix=".part", dir=directory)
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as output:
                def consume(response):
                    """消费响应数据并检查取消状态，在发布文件前完成必要校验。"""
                    length = response.headers.get("Content-Length")
                    total = int(length) if length and length.isdecimal() else 0
                    done = 0
                    digest = hashlib.sha256()
                    for chunk in response.iter_content(65536):
                        check_cancel(cancel)
                        output.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        progress(done, total)
                    check_cancel(cancel)
                    if total and done != total:
                        raise DownloadError("下载文件大小不符，请重试")
                    if not done:
                        raise DownloadError("下载文件为空")
                    if resource.sha256 and digest.hexdigest() != resource.sha256:
                        raise DownloadError("SHA-256 校验失败，请重新下载")
                self._read("/" + quote(resource.key, safe="/"), cancel, consume)
            check_cancel(cancel)
            if resource.kind in ("background", "avatar"):
                # 用户确认后覆盖同名图片；完整下载并校验后原子替换，失败不破坏旧图。
                target = directory / resource.filename
                temporary.replace(target)
                return target
            # 同目录硬链接是原子的“不覆盖发布”；保留用户已有图片与并发下载结果。
            name = resource.filename
            for index in range(10000):
                target = directory / (name if index == 0 else f"{Path(name).stem} ({index}){Path(name).suffix}")
                try:
                    os.link(temporary, target)
                    return target
                except FileExistsError:
                    continue
            raise DownloadError("同名文件过多，请整理下载目录")
        finally:
            temporary.unlink(missing_ok=True)
