from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Callable, Optional

from .bestdori_client import DownloadStream
from .cache import CacheManager, cache_manager
from .models import AssetKey, CancelToken


class LinkMode(Enum):
    """将缓存文件落到模型目录时的策略。"""

    AUTO = "auto"          # 优先硬链接，失败则复制
    HARDLINK = "hardlink"  # 只允许硬链接，失败直接报错
    COPY = "copy"          # 永远复制


@dataclass(frozen=True)
class CacheResult:
    """资产缓存查询/写入结果。"""

    path: Optional[Path]  # optional 缺失时为 None
    event: str            # cache_hit / downloaded / missing_optional


class AssetCache:
    """全局资产缓存。

    目标：同一 AssetKey(bundle_name,file_name,server) 只下载一次；后续复用缓存并硬链接到模型目录。
    """

    def __init__(self, cache: CacheManager = cache_manager, *, namespace: str = "live2d") -> None:
        self.cache = cache
        self.namespace = namespace
        self._locks: dict[AssetKey, Lock] = {}
        self._locks_lock = Lock()

    @classmethod
    def global_default(cls) -> "AssetCache":
        return cls(cache_manager)

    def _lock_for(self, key: AssetKey) -> Lock:
        with self._locks_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = Lock()
                self._locks[key] = lock
            return lock

    def get_cache_path(self, key: AssetKey) -> Path:
        # 将 server 纳入路径，避免跨服串用。
        server_part = key.server.value if key.server is not None else "_unknown_server"
        rel = PurePosixPath(self.namespace) / "assets" / server_part / PurePosixPath(key.bundle_name) / key.file_name
        return self.cache.resolve_path(rel)

    def get_or_download(
        self,
        *,
        key: AssetKey,
        open_stream: Callable[[], Optional[DownloadStream]],
        allow_not_found: bool,
        cancel: Optional[CancelToken] = None,
        on_bytes: Optional[Callable[[int, Optional[int]], None]] = None,
    ) -> CacheResult:
        """获取缓存文件；若未命中则下载写入缓存。

        :param key: 资产唯一键
        :param open_stream: 打开网络下载流的函数（仅在缓存未命中时调用）
        :param allow_not_found: True 时，404 视为正常缺失
        :param cancel: 可选取消令牌
        :param on_bytes: 可选回调，参数为 (bytes_done, bytes_total)
        """
        if cancel is not None:
            cancel.raise_if_cancelled()

        cache_path = self.get_cache_path(key)
        lock = self._lock_for(key)
        with lock:
            # double-check：锁内再次确认
            if cache_path.exists():
                return CacheResult(path=cache_path, event="cache_hit")

            if cancel is not None:
                cancel.raise_if_cancelled()

            stream = open_stream()
            if stream is None:
                if allow_not_found:
                    return CacheResult(path=None, event="missing_optional")
                # allow_not_found=False 时，不应该返回 None
                raise RuntimeError("open_stream 返回 None，但 allow_not_found=False")

            def _write(out):
                with stream:
                    for chunk in stream.iter_bytes():
                        out.write(chunk)
                        if on_bytes is not None:
                            on_bytes(stream.downloaded_bytes, stream.total_bytes)

            self.cache.atomic_write_bytes(cache_path, _write)
            return CacheResult(path=cache_path, event="downloaded")

    def materialize_to(
        self,
        *,
        cache_path: Path,
        dest_path: Path,
        mode: LinkMode = LinkMode.AUTO,
        overwrite: bool = False,
    ) -> str:
        """把缓存文件写入/链接到目标路径。

        返回：linked / copied / skipped
        """
        if dest_path.exists():
            if not overwrite:
                return "skipped"
            dest_path.unlink()

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        if mode == LinkMode.COPY:
            shutil.copy2(cache_path, dest_path)
            return "copied"

        try:
            os.link(cache_path, dest_path)
            return "linked"
        except OSError:
            if mode == LinkMode.HARDLINK:
                raise
            shutil.copy2(cache_path, dest_path)
            return "copied"
