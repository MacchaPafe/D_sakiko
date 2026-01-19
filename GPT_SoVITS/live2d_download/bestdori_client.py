from dataclasses import dataclass
from pathlib import Path
import random
from typing import Any, Iterator, Optional
import time
import requests

from .models import CancelToken, HttpStatusError, Server
from .cache import CacheManager, cache_manager

@dataclass
class CachePolicy:
    enabled: bool = True
    ttl_seconds: int = 24 * 3600
    cache_dir: Optional[Path] = None


BytesIterator = Iterator[bytes]


@dataclass
class DownloadStream:
    """一个可关闭的下载流。

    - 用 `with` 包裹，确保 `resp.close()` 一定会执行。
    - `iter_bytes()` 返回 bytes_iterator：每次 yield 一个 chunk。
    """
    resp: requests.Response
    chunk_size: int
    cancel: Optional[CancelToken] = None
    total_bytes: Optional[int] = None
    downloaded_bytes: int = 0

    def __enter__(self) -> "DownloadStream":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.resp.close()

    def iter_bytes(self) -> BytesIterator:
        try:
            for chunk in self.resp.iter_content(chunk_size=self.chunk_size, decode_unicode=False):
                self.downloaded_bytes += len(chunk)
                if self.cancel is not None:
                    self.cancel.raise_if_cancelled()
                if not chunk:
                    continue
                yield chunk
        finally:
            # 双保险：即使调用方不使用 with，这里也会在迭代结束/异常时关闭连接
            self.close()


class BestdoriClient:
    def __init__(
        self,
        *,
        server: Server = Server.JAPANESE,
        base_assets_url: str = "https://bestdori.com/assets",
        chara_roster_url: str = "https://bestdori.com/api/characters",
        assets_index_url: str = "https://bestdori.com/api/explorer/jp/assets/_info.json",
        costume_index_url: str = "https://bestdori.com/api/costumes/all.5.json",
        timeout_seconds: float = 30.0,
        session: Optional[requests.Session] = None,
        cache: Optional[CachePolicy] = None,
        user_agent: str = "bestdori-live2d-downloader-python/0.1",
    ) -> None:
        self.server = server
        self.base_assets_url = base_assets_url.rstrip("/")
        self.chara_roster_url = chara_roster_url.rstrip("/")
        self.assets_index_url = assets_index_url
        self.costume_index_url = costume_index_url
        self.timeout_seconds = timeout_seconds
        # 如果没有传入 Session，则创建一个新的，并且设置 UA
        if session is None:
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": user_agent})
        else:
            self.session = session

        self.cache = cache or CachePolicy()
        self._cache_manager: CacheManager = (
            CacheManager(self.cache.cache_dir) if self.cache.cache_dir is not None else cache_manager
        )

    # --- 基础工具 ---
    def _get_json(self, url: str, *, cache_key: Optional[str] = None) -> dict[str, Any]:
        """发起网络请求，并返回 JSON 响应。

        当 cache_key 不为空且 cache.enabled=True 时，启用缓存；缓存保留时间为 cache.ttl_seconds。
        """
        # 如果启用缓存，尝试读取缓存
        if cache_key is not None and self.cache.enabled:
            data = self._cache_manager.read_expire_json(cache_key, self.cache.ttl_seconds)
            if data is not None:
                return data
            
        response = self.session.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()
        json_ = response.json()

        # 如果启用缓存，写入缓存
        if cache_key is not None and self.cache.enabled:
            self._cache_manager.write_expire_json(cache_key, json_)

        return json_

    def get_asset_url(self, bundle_name: str, file_name: str) -> str:
        """
        获得资源文件的完整下载 URL。
        """
        return f"{self.base_assets_url}/{self.server.value}/{bundle_name}_rip/{file_name}"

    # --- API 端点 ---
    def get_characters_roster(self) -> dict[str, Any]:
        """GET /api/characters/all.2.json（建议缓存 24h）"""
        url = f"{self.chara_roster_url}/all.2.json"
        return self._get_json(url, cache_key="chara_roster.json")

    def get_character(self, chara_id: int) -> dict[str, Any]:
        """GET /api/characters/{id}.json（可缓存 24h）"""
        url = f"{self.chara_roster_url}/{chara_id}.json"
        return self._get_json(url, cache_key=f"chara_{chara_id}.json")

    def get_assets_index(self) -> dict[str, Any]:
        """GET /api/assets（建议缓存 24h）"""
        return self._get_json(self.assets_index_url, cache_key="assets_info.json")
    
    def get_costume_index(self) -> dict[str, Any]:
        """GET /api/costumes/all.5.json（建议缓存 24h）"""
        return self._get_json(self.costume_index_url, cache_key="costumes_info.json")

    def get_costume_icon(self, costume_id: int, costume_name: str) -> bytes:
        """获得指定服装的图标二进制数据。"""
        url = f"{self.base_assets_url}/{self.server.value}/thumb/costume/group{costume_id // 50}_rip/{costume_name}.png"
        return self.session.get(url, timeout=self.timeout_seconds).content

    def get_live2d_assets_map(self) -> dict[str, int]:
        """获得 live2d 服装名称 -> 服装 id 的映射表"""
        assets = self.get_assets_index()
        data = assets["live2d"]["chara"]
        return data

    def validate_live2d_name(self, live2d_name: str) -> bool:
        live2d_map = self.get_live2d_assets_map()
        return live2d_name in live2d_map

    def get_build_data(self, live2d_name: str) -> dict[str, Any]:
        """GET /assets/jp/live2d/chara/{live2d}_rip/buildData.asset（通常不缓存）"""
        url = f"{self.base_assets_url}/{self.server.value}/live2d/chara/{live2d_name}_rip/buildData.asset"
        return self._get_json(url, cache_key=None)

    def iter_download_asset(
        self,
        bundle_name: str,
        file_name: str,
        *,
        cancel: Optional[CancelToken] = None,
        allow_not_found: bool = False,
        chunk_size: int = 1024 * 256,
        retries: int = 3,
        backoff_base: float = 0.5,
    ) -> Optional[DownloadStream]:
        """下载资源文件并返回下载流迭代器。

        - allow_not_found=True 时，404 视为“正常缺失”，返回 None
        - cancel 不为空时，支持取消下载；当 cancel.cancel() 方法被调用时，立刻取消下载。
        """
        return self._open_download(
            url=self.get_asset_url(bundle_name, file_name),
            cancel=cancel,
            allow_not_found=allow_not_found,
            chunk_size=chunk_size,
            retries=retries,
            backoff_base=backoff_base,
        )
    
    def _open_download(
        self,
        url: str,
        *,
        cancel: Optional[CancelToken] = None,
        allow_not_found: bool = False,
        chunk_size: int = 1024 * 256,
        retries: int = 3,
        backoff_base: float = 0.5,
    ) -> Optional[DownloadStream]:
        """打开下载流（默认重试 3 次），返回 DownloadStream 或 None（允许缺失时）。

        重试策略（建议保持简单）：
        - 网络错误（requests.RequestException）重试
        - HTTP 429/5xx 重试
        - HTTP 404：如果 allow_not_found=True，返回 None；否则不重试，直接报错
        """
        retry_statuses = {429, 500, 502, 503, 504}
        last_exc: Optional[BaseException] = None

        # retries=3 表示“最多额外重试 3 次”，总尝试次数为 1 + retries
        for attempt in range(retries + 1):
            if cancel is not None:
                cancel.raise_if_cancelled()

            resp: Optional[requests.Response] = None
            try:
                resp = self.session.get(
                    url,
                    stream=True,
                    timeout=self.timeout_seconds,
                )

                status = resp.status_code
                if status == 404 and allow_not_found:
                    resp.close()
                    return None

                if status == 200:
                    content_type = (resp.headers.get("Content-Type") or "").lower()
                    if content_type.startswith("text/html"):
                        # Bestdori 有时会返回 HTML 错误页；这里按错误处理
                        raise RuntimeError(f"疑似返回错误页（text/html）: {url}")

                    length = resp.headers.get("Content-Length")
                    total = int(length) if (length and length.isdigit()) else None
                    return DownloadStream(resp=resp, chunk_size=chunk_size, cancel=cancel, total_bytes=total)

                # 非 200
                if status in retry_statuses and attempt < retries:
                    resp.close()
                    sleep_s = backoff_base * (2**attempt) + random.random() * 0.1
                    time.sleep(sleep_s)
                    continue

                # 不可重试 or 重试耗尽
                resp.close()
                raise HttpStatusError(status, url)

            except (requests.RequestException, RuntimeError) as e:
                last_exc = e
                if resp is not None:
                    resp.close()
                if attempt < retries:
                    sleep_s = backoff_base * (2**attempt) + random.random() * 0.1
                    time.sleep(sleep_s)
                    continue
                raise

        # 正常不会走到这里
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("下载失败（未知原因）")
