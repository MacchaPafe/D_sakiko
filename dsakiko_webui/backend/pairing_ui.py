from __future__ import annotations

import io
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import segno
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import RequestResponseEndpoint

from .auth import AccessController, PairingGrant
from .networking import NetworkAddress, discover_network_addresses


PAIRING_UI_HEADER = "X-Dsakiko-Pairing-UI"
PAIRING_UI_ASSETS = Path(__file__).with_name("pairing_ui_static")
PAIRING_HELP_ASSET = PAIRING_UI_ASSETS / "help.svg"
SHARED_ASSETS_ROOT = Path(__file__).parents[1] / "shared" / "assets"
PAIRING_MASCOT_ASSET = SHARED_ASSETS_ROOT / "access-gate" / "neutral.png"


class AddressSelectionRequest(BaseModel):
    """描述本机展示页选择的已发现局域网地址。"""

    address: str = Field(min_length=7, max_length=15)


@dataclass(frozen=True)
class PairingUiLocation:
    """描述本机展示页监听位置和浏览器授权 nonce。"""

    port: int
    nonce: str

    @property
    def url(self) -> str:
        """生成不会把 nonce 发给 HTTP 服务端的本机页面地址。"""
        return f"http://127.0.0.1:{self.port}/#ui={self.nonce}"


class PairingPresentation:
    """协调配对原文、二维码、候选地址和本机展示状态。"""

    def __init__(
        self,
        access: AccessController,
        *,
        webui_port: int,
        address_discovery: Callable[[], list[NetworkAddress]] = discover_network_addresses,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """创建展示状态并立即生成首个一次性配对凭证。"""
        self._access = access
        self._webui_port = webui_port
        self._address_discovery = address_discovery
        self._clock = clock
        self._lock = threading.RLock()
        self._addresses: list[NetworkAddress] = []
        self._selected_address: str | None = None
        self._last_discovery_at = 0.0
        self._revision = 0
        self._grant: PairingGrant = self._access.regenerate_pairing()
        self.refresh_addresses()

    def refresh_addresses(self) -> None:
        """重新发现候选地址，并尽量保留用户当前选择。"""
        discovered = self._address_discovery()
        with self._lock:
            addresses = {item.address for item in discovered}
            previous = self._selected_address
            self._addresses = discovered
            if previous not in addresses:
                self._selected_address = discovered[0].address if discovered else None
            if self._selected_address != previous:
                self._revision += 1
            self._last_discovery_at = self._clock()

    def refresh_if_due(self) -> None:
        """候选地址缓存超过五秒时自动重新发现。"""
        with self._lock:
            due = self._clock() - self._last_discovery_at >= 5.0
        if due:
            self.refresh_addresses()

    def select_address(self, address: str) -> bool:
        """仅允许选择当前已经发现的候选地址。"""
        with self._lock:
            if address not in {item.address for item in self._addresses}:
                return False
            if address != self._selected_address:
                self._selected_address = address
                self._revision += 1
            return True

    def regenerate(self) -> None:
        """撤销当前配对凭证，并持有新的展示原文。"""
        grant = self._access.regenerate_pairing()
        with self._lock:
            self._grant = grant
            self._revision += 1

    def state_payload(self) -> dict[str, object]:
        """生成不含配对原文的本机页面状态响应。"""
        self.refresh_if_due()
        snapshot = self._access.pairing_snapshot()
        with self._lock:
            return {
                "status": snapshot.status,
                "remaining_seconds": snapshot.remaining_seconds,
                "connected": snapshot.connected,
                "revision": self._revision,
                "selected_address": self._selected_address,
                "addresses": [
                    {
                        "address": item.address,
                        "interface_name": item.interface_name,
                        "is_default": item.is_default,
                    }
                    for item in self._addresses
                ],
            }

    def presentation_payload(self) -> dict[str, object]:
        """生成受 UI nonce 保护的二维码、链接和备用访问码。"""
        with self._lock:
            if self._selected_address is None:
                return {
                    "revision": self._revision,
                    "pairing_url": None,
                    "fallback_url": None,
                    "qr_svg": None,
                    "access_code": self._access.access_code,
                }
            fallback_url = f"http://{self._selected_address}:{self._webui_port}/"
            pairing_url = (
                f"{fallback_url}#pair={self._grant.token}"
            )
            return {
                "revision": self._revision,
                "pairing_url": pairing_url,
                "fallback_url": fallback_url,
                "qr_svg": _qr_svg(pairing_url),
                "access_code": self._access.access_code,
            }


def generate_ui_nonce() -> str:
    """生成只授权当前本机展示页的随机 nonce。"""
    return secrets.token_urlsafe(32)


def _qr_svg(value: str) -> str:
    """把配对 URL 编码为无外部引用的标准二维码 SVG。"""
    output = io.BytesIO()
    qr = segno.make_qr(value, error="q")
    qr.save(
        output,
        kind="svg",
        scale=8,
        border=4,
        xmldecl=False,
        omitsize=True,
    )
    return output.getvalue().decode("utf-8")


def create_pairing_ui_app(
    presentation: PairingPresentation,
    location: PairingUiLocation,
) -> FastAPI:
    """创建只应绑定 loopback 的本机配对展示应用。"""
    app = FastAPI(title="数字小祥 WebUI 本机配对", docs_url=None, redoc_url=None)
    expected_host = f"127.0.0.1:{location.port}"
    expected_origin = f"http://127.0.0.1:{location.port}"

    def authorized(request: Request, *, mutation: bool = False) -> bool:
        """验证本机页面 Host、UI nonce，并为修改请求验证同源 Origin。"""
        if request.headers.get("host") != expected_host:
            return False
        if not secrets.compare_digest(
            request.headers.get(PAIRING_UI_HEADER, ""),
            location.nonce,
        ):
            return False
        return not mutation or request.headers.get("origin") == expected_origin

    def forbidden() -> JSONResponse:
        """返回不泄漏本机展示状态的统一拒绝响应。"""
        return JSONResponse({"error": "forbidden"}, status_code=403)

    @app.middleware("http")
    async def security_headers(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """为本机展示页统一附加缓存、引用和内容安全策略。"""
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data: blob:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        return response

    @app.get("/")
    async def index() -> FileResponse:
        """返回不包含任何凭证的本机展示页面静态壳。"""
        return FileResponse(PAIRING_UI_ASSETS / "index.html", media_type="text/html")

    @app.get("/app.js")
    async def javascript() -> FileResponse:
        """返回本机展示页的同源 JavaScript。"""
        return FileResponse(PAIRING_UI_ASSETS / "app.js", media_type="text/javascript")

    @app.get("/style.css")
    async def stylesheet() -> FileResponse:
        """返回本机展示页的同源样式表。"""
        return FileResponse(PAIRING_UI_ASSETS / "style.css", media_type="text/css")

    @app.get("/neutral.png")
    async def neutral_mascot() -> FileResponse:
        """返回与访问码页面共用的中性状态角色图。"""
        return FileResponse(PAIRING_MASCOT_ASSET, media_type="image/png")

    @app.get("/help.svg")
    async def help_icon() -> FileResponse:
        """返回配对说明入口使用的问号图标。"""
        return FileResponse(PAIRING_HELP_ASSET, media_type="image/svg+xml")

    @app.get("/api/state")
    async def state(request: Request) -> JSONResponse:
        """返回不含原始配对凭证的展示状态。"""
        if not authorized(request):
            return forbidden()
        return JSONResponse(presentation.state_payload())

    @app.get("/api/presentation")
    async def presentation_data(request: Request) -> JSONResponse:
        """返回受 UI nonce 保护的二维码和备用访问码。"""
        if not authorized(request):
            return forbidden()
        return JSONResponse(presentation.presentation_payload())

    @app.post("/api/regenerate")
    async def regenerate(request: Request) -> JSONResponse:
        """撤销旧凭证并生成新的本机展示内容。"""
        if not authorized(request, mutation=True):
            return forbidden()
        presentation.regenerate()
        return JSONResponse(presentation.state_payload())

    @app.post("/api/refresh")
    async def refresh(request: Request) -> JSONResponse:
        """手动重新发现可用于手机访问的局域网地址。"""
        if not authorized(request, mutation=True):
            return forbidden()
        presentation.refresh_addresses()
        return JSONResponse(presentation.state_payload())

    @app.post("/api/address")
    async def select_address(
        body: AddressSelectionRequest,
        request: Request,
    ) -> JSONResponse:
        """切换到已经发现的候选地址而不更换配对凭证。"""
        if not authorized(request, mutation=True):
            return forbidden()
        if not presentation.select_address(body.address):
            return JSONResponse({"error": "unknown_address"}, status_code=400)
        return JSONResponse(presentation.state_payload())

    return app
