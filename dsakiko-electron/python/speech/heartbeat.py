"""语音服务只探测所属宿主，不取得业务写权限或依赖前端页面。"""

from __future__ import annotations

import asyncio
import http.client
import json
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, cast
from urllib.parse import urlsplit


@dataclass(frozen=True)
class HostProbe:
    """宿主通过环境提供的回环探测凭据，运行身份不能随重连而改变。"""

    url: str
    token: str
    backend_run_id: str

    @classmethod
    def parse(cls, raw: str) -> Optional[HostProbe]:
        """独立测试可省略宿主；正式宿主配置必须完整且只允许本机 HTTP。"""
        if not raw:
            return None
        value: object = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("宿主探测配置无效")
        fields = cast(dict[str, object], value)
        url, token, run_id = fields.get("url"), fields.get("token"), fields.get("backendRunId")
        if not all(isinstance(item, str) and item for item in (url, token, run_id)):
            raise ValueError("宿主探测配置不完整")
        assert isinstance(url, str) and isinstance(token, str) and isinstance(run_id, str)
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None or parsed.path != "/api/v1/health" or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("宿主探测地址必须为受控的回环接口")
        return cls(url, token, run_id)


def probe_host(host: HostProbe) -> str:
    """一次有界探测返回 alive、gone 或 unavailable，不跟随重定向。"""
    target = urlsplit(host.url)
    connection = http.client.HTTPConnection("127.0.0.1", target.port, timeout=2)
    try:
        connection.request("GET", target.path, headers={"Authorization": "Bearer " + host.token})
        response = connection.getresponse()
        if response.status in (401, 403):
            return "gone"
        if response.status != 200:
            return "unavailable"
        value: object = json.loads(response.read(8193))
        if not isinstance(value, dict):
            return "unavailable"
        fields = cast(dict[str, object], value)
        if fields.get("backendRunId") != host.backend_run_id or fields.get("status") == "stopping":
            return "gone"
        return "alive" if fields.get("status") == "running" else "unavailable"
    except (OSError, http.client.HTTPException, ValueError):
        return "unavailable"
    finally:
        connection.close()


async def watch_host(
    stopping: asyncio.Event,
    probe: Callable[[], Awaitable[str]],
    interval: float = 5.0,
    timeout: float = 60.0,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """连续探测失败才退出；唤醒后先探测成功与否，避免仅凭休眠时长判定死亡。"""
    failed_since: Optional[float] = None
    while not stopping.is_set():
        status = await probe()
        now = clock()
        if status == "alive":
            failed_since = None
        elif status == "gone":
            stopping.set()
            return
        else:
            if failed_since is None:
                failed_since = now
            if now - failed_since >= timeout:
                stopping.set()
                return
        try:
            await asyncio.wait_for(stopping.wait(), interval)
        except asyncio.TimeoutError:
            pass
