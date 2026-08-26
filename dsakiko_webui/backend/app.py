from __future__ import annotations

import asyncio
import logging
import queue
import time
import unicodedata
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, Request, Response, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from .assets import PROJECT_ROOT, AssetRegistry
from .auth import (
    COOKIE_NAME,
    AccessCodeRejected,
    AccessController,
    AccessRateLimited,
    PairingRejected,
    SessionReplacement,
)
from .protocol import (
    PROTOCOL_VERSION,
    PairingRequest,
    ProtocolError,
    SessionRequest,
    SettingsUpdateRequest,
    http_error,
)
from .networking import discover_network_addresses
from .runtime import HeadlessRuntime
from .uploads import MAX_IMAGE_UPLOAD_BYTES, PendingImageStore
from .ws import WebSocketManager
from GPT_SoVITS.runtime.runtime_lock import acquire_runtime_lock


logger = logging.getLogger(__name__)
FRONTEND_DIST = PROJECT_ROOT / "dsakiko_webui" / "frontend" / "dist"
WEBUI_PORT = 8000


def print_startup_banner(access_code: str, pairing_ui_url: str | None = None) -> None:
    addresses = discover_network_addresses()
    local_ip = addresses[0].address if addresses else None
    lines = [
        "数字小祥WebUI",
        "",
        "首选：在电脑打开本机配对页，并使用手机扫描二维码",
    ]
    if pairing_ui_url:
        lines.append(f"本机配对页：{pairing_ui_url}")
    else:
        lines.append("本机配对页未启动，请使用下方备用方式")
    lines.extend([
        "",
        "备用：手机与电脑连接同一可信局域网后手动登录",
        f"手机访问地址：http://{local_ip}:{WEBUI_PORT}" if local_ip else "未检测到可用局域网地址",
        f"六位访问码：{access_code}",
    ])
    def display_width(value: str) -> int:
        return sum(
            2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
            for character in value
        )

    content_width = max(display_width(line) for line in lines)
    border = "#" * (content_width + 4)
    banner = [border]
    for line in lines:
        padding = " " * (content_width - display_width(line))
        banner.append(f"# {line}{padding} #")
    banner.append(border)
    print("\n" + "\n".join(banner) + "\n", flush=True)


def create_app(
    runtime: object | None = None,
    auth: AccessController | None = None,
    *,
    initialize_runtime: bool = True,
) -> FastAPI:
    assets = runtime.assets if runtime is not None else AssetRegistry()
    if runtime is None:
        uploads = PendingImageStore()
        runtime = HeadlessRuntime(assets, uploads)
    else:
        uploads = getattr(runtime, "uploads", None) or PendingImageStore()
    auth = auth or AccessController()
    ws_manager = WebSocketManager(auth, runtime)

    async def event_pump() -> None:
        while True:
            try:
                event = runtime.events.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            await ws_manager.send_event(event)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runtime_lease = app.state.runtime_lease
        if initialize_runtime and runtime_lease is None:
            runtime_lease = acquire_runtime_lock(PROJECT_ROOT, "web")
        event_task = asyncio.create_task(event_pump())
        initialize_task: asyncio.Task[None] | None = None
        if initialize_runtime:
            async def initialize() -> None:
                try:
                    await asyncio.to_thread(runtime.initialize)
                except Exception:
                    logger.exception("WebUI Runtime 初始化失败")
            initialize_task = asyncio.create_task(initialize())
        if initialize_runtime:
            logger.info("WebUI 访问码：%s", auth.access_code)
            print_startup_banner(auth.access_code, app.state.pairing_ui_url)
        try:
            yield
        finally:
            event_task.cancel()
            if initialize_task is not None:
                await initialize_task
            if initialize_runtime:
                await asyncio.to_thread(runtime.shutdown)
            if runtime_lease is not None:
                runtime_lease.release()
            uploads.close()

    app = FastAPI(title="数字小祥 WebUI", version="1.0", lifespan=lifespan)
    app.state.runtime = runtime
    app.state.auth = auth
    app.state.ws_manager = ws_manager
    app.state.uploads = uploads
    app.state.runtime_lease = None
    app.state.pairing_ui_url = None

    @app.middleware("http")
    async def browser_security_headers(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """阻止入口和认证响应被缓存或把来源 URL 发送给其他站点。"""
        response = await call_next(request)
        static_path = request.url.path.lower()
        if static_path.endswith((".js", ".mjs")):
            response.headers["Content-Type"] = "application/javascript"
        protected_response = (
            request.url.path == "/"
            or request.url.path.startswith("/api/v1/session")
            or request.url.path.startswith("/api/v1/pairing")
        )
        if protected_response:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def authenticated(request: Request) -> bool:
        return auth.is_authenticated(request.cookies.get(COOKIE_NAME))

    def session_response(replacement: SessionReplacement) -> JSONResponse:
        """把访问控制结果转换为安全的浏览器会话响应。"""
        response = JSONResponse({
            "authenticated": True,
            "replaced_existing_controller": replacement.replaced_existing_controller,
        }, headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        })
        response.set_cookie(
            COOKIE_NAME,
            replacement.token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return response

    @app.get("/api/v1/health")
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "protocol_version": PROTOCOL_VERSION,
            "runtime_status": runtime.status,
            "session_id": runtime.session_id,
            "auth_required": True,
            "authenticated": authenticated(request),
            "server_time": int(time.time()),
        }, headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/session")
    async def login(body: SessionRequest, request: Request) -> JSONResponse:
        source_ip = request.client.host if request.client is not None else "unknown"
        try:
            replacement = auth.login_with_access_code(
                body.access_code,
                source_ip,
                body.session_id,
            )
        except AccessRateLimited as exc:
            error = ProtocolError(
                "AUTH_RATE_LIMITED",
                "尝试次数过多，请等待后再试。",
                True,
                {"retry_after_seconds": exc.retry_after_seconds},
            )
            return JSONResponse(
                http_error(error),
                status_code=429,
                headers={
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                    "Retry-After": str(exc.retry_after_seconds),
                },
            )
        except AccessCodeRejected:
            error = ProtocolError("AUTH_REQUIRED", "访问码错误，请重新输入。", True)
            return JSONResponse(
                http_error(error),
                status_code=401,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        if replacement.replaced_token:
            await ws_manager.close_token(replacement.replaced_token, 4409, "控制权已被新设备接管")
        return session_response(replacement)

    @app.post("/api/v1/pairing/redeem")
    async def redeem_pairing(body: PairingRequest, request: Request) -> JSONResponse:
        """兑换二维码中的一次性配对凭证并签发浏览器会话。"""
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > 4096:
            return JSONResponse(
                http_error(ProtocolError("PAIRING_INVALID", "二维码已失效。", True)),
                status_code=413,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        try:
            replacement = auth.login_with_pairing(body.pairing_token, body.session_id)
        except PairingRejected:
            error = ProtocolError(
                "PAIRING_INVALID",
                "二维码已失效，请在电脑端重新生成，或输入六位访问码。",
                True,
            )
            return JSONResponse(
                http_error(error),
                status_code=401,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        if replacement.replaced_token:
            await ws_manager.close_token(
                replacement.replaced_token,
                4409,
                "控制权已被新设备接管",
            )
        return session_response(replacement)

    @app.delete("/api/v1/session", status_code=204)
    async def logout(request: Request) -> Response:
        token = request.cookies.get(COOKIE_NAME)
        if not auth.logout(token):
            return JSONResponse(
                http_error(ProtocolError("AUTH_REQUIRED", "登录会话已失效。", True)),
                status_code=401,
            )
        await ws_manager.close_token(token, 4401, "已退出登录")
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.websocket("/api/v1/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await ws_manager.serve(websocket)

    @app.get("/api/v1/settings")
    async def get_settings(request: Request) -> JSONResponse:
        if not authenticated(request):
            return JSONResponse(
                http_error(ProtocolError("AUTH_REQUIRED", "登录会话已失效。", True)),
                status_code=401,
            )
        try:
            data = await asyncio.to_thread(runtime.settings_snapshot)
            return JSONResponse(data, headers={"Cache-Control": "no-store"})
        except ProtocolError as exc:
            return JSONResponse(http_error(exc), status_code=503)

    @app.patch("/api/v1/settings")
    async def update_settings(body: SettingsUpdateRequest, request: Request) -> JSONResponse:
        if not authenticated(request):
            return JSONResponse(
                http_error(ProtocolError("AUTH_REQUIRED", "登录会话已失效。", True)),
                status_code=401,
            )
        try:
            data = await asyncio.to_thread(
                runtime.update_settings,
                speech_speed=body.speech_speed,
                sentence_pause_seconds=body.sentence_pause_seconds,
                llm_choice_id=body.llm_choice_id,
            )
            return JSONResponse(data, headers={"Cache-Control": "no-store"})
        except ProtocolError as exc:
            status_code = 409 if exc.code == "CHAT_BUSY" else 400
            return JSONResponse(http_error(exc), status_code=status_code)

    @app.post("/api/v1/uploads/images")
    async def upload_image(request: Request, file: UploadFile = File(...)) -> JSONResponse:
        if not authenticated(request):
            return JSONResponse(
                http_error(ProtocolError("AUTH_REQUIRED", "需要登录后上传图片。", True)),
                status_code=401,
            )
        if runtime.status != "ready":
            return JSONResponse(
                http_error(ProtocolError("RUNTIME_NOT_READY", "后端仍在初始化，请稍后重试。", True)),
                status_code=503,
            )
        capabilities = runtime.capabilities() if hasattr(runtime, "capabilities") else {}
        if not capabilities.get("image_input", False):
            return JSONResponse(
                http_error(ProtocolError(
                    "IMAGE_INPUT_UNSUPPORTED",
                    "当前模型不支持图片输入，请在电脑端切换支持视觉的模型。",
                )),
                status_code=409,
            )

        data = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
        await file.close()
        if len(data) > MAX_IMAGE_UPLOAD_BYTES:
            return JSONResponse(
                http_error(ProtocolError("IMAGE_TOO_LARGE", "单张图片不能超过 12 MB。")),
                status_code=413,
            )
        try:
            item = uploads.add(data, file.filename or "image")
        except (OSError, ValueError) as exc:
            return JSONResponse(
                http_error(ProtocolError("INVALID_IMAGE", str(exc))),
                status_code=400,
            )
        return JSONResponse({
            "upload_id": item.upload_id,
            "original_name": item.original_name,
            "mime_type": item.mime_type,
            "size": item.size,
        }, headers={"Cache-Control": "no-store"})

    @app.delete("/api/v1/uploads/images/{upload_id}", status_code=204)
    async def delete_uploaded_image(upload_id: str, request: Request) -> Response:
        if not authenticated(request):
            return JSONResponse(
                http_error(ProtocolError("AUTH_REQUIRED", "登录会话已失效。", True)),
                status_code=401,
            )
        uploads.discard([upload_id])
        return Response(status_code=204)

    @app.get("/api/v1/media/{media_id}")
    async def media(media_id: str, request: Request) -> Response:
        if not authenticated(request):
            return JSONResponse(
                http_error(ProtocolError("AUTH_REQUIRED", "需要登录后访问媒体。", True)),
                status_code=401,
            )
        entry = assets.media(media_id)
        if entry is None or not entry.path.is_file():
            return JSONResponse(
                http_error(ProtocolError("MEDIA_NOT_FOUND", "请求的媒体文件不存在。")),
                status_code=404,
            )
        return FileResponse(
            entry.path,
            media_type=entry.media_type,
            headers={"Cache-Control": "private, max-age=3600", "Accept-Ranges": "bytes"},
        )

    @app.get("/api/v1/live2d/{model_id}/{asset_path:path}")
    async def live2d(model_id: str, asset_path: str, request: Request) -> Response:
        if not authenticated(request):
            return JSONResponse(
                http_error(ProtocolError("AUTH_REQUIRED", "需要登录后访问模型。", True)),
                status_code=401,
            )
        path = assets.live2d_file(model_id, asset_path)
        if path is None:
            return JSONResponse(
                http_error(ProtocolError("MODEL_NOT_FOUND", "请求的模型资源不存在。")),
                status_code=404,
            )
        return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})

    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    else:
        @app.get("/")
        async def root() -> dict[str, str]:
            return {"message": "前端尚未构建，请在 dsakiko_webui/frontend 运行 npm run build。"}

    return app
