from __future__ import annotations

import asyncio
import logging
import queue
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .assets import PROJECT_ROOT, AssetRegistry
from .auth import COOKIE_NAME, SingleControllerAuth
from .protocol import PROTOCOL_VERSION, ProtocolError, SessionRequest, http_error
from .runtime import HeadlessRuntime
from .ws import WebSocketManager
from GPT_SoVITS.runtime.runtime_lock import acquire_runtime_lock


logger = logging.getLogger(__name__)
FRONTEND_DIST = PROJECT_ROOT / "dsakiko_webui" / "frontend" / "dist"


def create_app(
    runtime: Any | None = None,
    auth: SingleControllerAuth | None = None,
    *,
    initialize_runtime: bool = True,
) -> FastAPI:
    assets = runtime.assets if runtime is not None else AssetRegistry()
    runtime = runtime or HeadlessRuntime(assets)
    auth = auth or SingleControllerAuth()
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
        logger.info("WebUI 访问码：%s", auth.access_code)
        print(f"访问码：{auth.access_code}，请在控制端输入。", flush=True)
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

    app = FastAPI(title="数字小祥 WebUI", version="1.0", lifespan=lifespan)
    app.state.runtime = runtime
    app.state.auth = auth
    app.state.ws_manager = ws_manager
    app.state.runtime_lease = None

    def authenticated(request: Request) -> bool:
        return auth.is_authenticated(request.cookies.get(COOKIE_NAME))

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
    async def login(body: SessionRequest) -> JSONResponse:
        try:
            replacement = auth.login(body.access_code, body.session_id)
        except ValueError:
            error = ProtocolError("AUTH_REQUIRED", "访问码错误，请重新输入。", True)
            return JSONResponse(http_error(error), status_code=401)
        if replacement.replaced_token:
            await ws_manager.close_token(replacement.replaced_token, 4409, "控制权已被新设备接管")
        response = JSONResponse({"authenticated": True}, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            COOKIE_NAME,
            replacement.token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return response

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
