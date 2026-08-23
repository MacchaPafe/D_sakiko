# D_sakiko WebUI

WebUI 是一个不启动 Qt 与本地 Live2D 窗口的独立运行模式。电脑负责 LLM、Agent、GPT-SoVITS 和聊天记录，浏览器通过 HTTP 与 WebSocket 使用这些能力。

协议以 [`PROTOCOL.md`](./PROTOCOL.md) 为准，总体设计见 [`D_sakiko_WebUI设计方案.md`](./D_sakiko_WebUI设计方案.md)。

## 启动

macOS / Linux：

```bash
./run_webui.command
```

也可以从项目根目录直接运行：

```bash
.venv/bin/python -m dsakiko_webui.backend.main
```

Windows 可以双击 `run_webui.bat`。服务默认监听 `0.0.0.0:8000`，终端会打印本次启动的六位访问码。

前端默认连接真实后端。打开页面后输入终端中的访问码，前端会建立 WebSocket，并在 Runtime 准备完成后自动同步会话、消息、角色和背景状态。

前后端分开开发时，先在项目根目录启动后端，再在 `dsakiko_webui/frontend` 运行 `npm run dev`。Vite 会把 `/api` 和 WebSocket 代理到本机 `8000` 端口。

## 后端结构

- `backend/main.py`：最薄的启动入口。
- `backend/app.py`：FastAPI 生命周期、HTTP 路由和静态前端。
- `backend/auth.py`：单控制端访问码与 Cookie。新设备登录会接管旧设备。
- `backend/ws.py`：WebSocket 信封、响应、事件顺序和连接替换。
- `backend/runtime.py`：复用角色、聊天、LLM 与 TTS 的 Headless Runtime。
- `backend/assets.py`：受控媒体、背景和 Live2D 文件映射。
- `backend/protocol.py`：协议输入模型、错误和命令响应。

`HeadlessRuntime` 继续使用 `GPT_SoVITS/character.py`、`chat/chat.py`、`dp_local2.py` 与 `audio_generator.py`。它没有导入 `main2.py`，也不会创建 PyQt 或 Python Live2D 窗口。

## 鉴权

同一时间只有一个控制端。访问码正确的新登录会生成新的 HttpOnly Cookie，旧 Cookie 立即失效，旧 WebSocket 以 `4409` 关闭。正在进行的 LLM/TTS 任务不会因接管而取消，新控制端连接后发送 `sync` 即可恢复状态。

## 测试

```bash
.venv/bin/python -m unittest dsakiko_webui.backend.test.test_backend -v
```
