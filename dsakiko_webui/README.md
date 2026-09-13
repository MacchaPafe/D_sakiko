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

Windows 可以双击 `run_webui.bat`。服务默认监听 `0.0.0.0:8000`。两个启动脚本都会在电脑浏览器中打开仅本机可访问的配对页；使用手机扫描二维码即可连接。终端同时保留普通局域网地址和本次启动的六位访问码作为备用方式。

直接运行 Python 模块时默认不打开浏览器。需要自动打开配对页时使用：

```bash
.venv/bin/python -m dsakiko_webui.backend.main --open-pairing
```

扫码链接携带五分钟有效的一次性随机配对凭证。前端会立即从地址栏清除凭证并兑换 HttpOnly Cookie，然后建立 WebSocket。二维码过期或使用后，可以在电脑配对页重新生成。普通 `http://电脑IP:8000/` 加六位访问码的旧流程继续可用。

前后端分开开发时，先在项目根目录启动后端，再在 `dsakiko_webui/frontend` 运行 `npm run dev`。Vite 会把 `/api` 和 WebSocket 代理到本机 `8000` 端口。

## 后端结构

- `backend/main.py`：最薄的启动入口。
- `backend/app.py`：FastAPI 生命周期、HTTP 路由和静态前端。
- `backend/auth.py`：统一管理访问码限速、一次性配对、Cookie 和单控制端接管。
- `backend/pairing_ui.py`：仅绑定 loopback 的电脑配对展示页与二维码。
- `backend/networking.py`：默认路由和私有 IPv4 地址发现。
- `backend/ws.py`：WebSocket 信封、响应、事件顺序和连接替换。
- `backend/runtime.py`：复用角色、聊天、LLM 与 TTS 的 Headless Runtime。
- `backend/assets.py`：受控媒体、背景和 Live2D 文件映射。
- `backend/protocol.py`：协议输入模型、错误和命令响应。

`HeadlessRuntime` 继续使用 `GPT_SoVITS/character.py`、`chat/chat.py`、`dp_local2.py` 与 `audio_generator.py`。它没有导入 `main2.py`，也不会创建 PyQt 或 Python Live2D 窗口。

## 鉴权

同一时间只有一个控制端。访问码正确的新登录会生成新的 HttpOnly Cookie，旧 Cookie 立即失效，旧 WebSocket 以 `4409` 关闭。正在进行的 LLM/TTS 任务不会因接管而取消，新控制端连接后发送 `sync` 即可恢复状态。

六位访问码具有按实际 TCP 来源 IP 的令牌桶、渐进冷却和全局防御模式。被限制时前端会显示服务端给出的等待倒计时。高强度一次性配对凭证不参与六位码失败统计。

## 安全边界

WebUI 使用明文 HTTP，设计目标是可信家庭或私人局域网。不要在公共 Wi-Fi、宿舍访客网络或其他不可信网络中直接暴露端口；同一网络中具备中间人能力的参与者仍可能观察 Cookie、聊天和媒体内容。

电脑配对页绑定随机 `127.0.0.1` 端口，并使用每次启动随机的 UI nonce、Host/Origin 检查、无 CORS 和严格安全响应头。局域网设备不能直接读取二维码、配对链接或备用访问码。

## 测试

```bash
.venv/bin/python -m unittest dsakiko_webui.backend.test.test_backend -v
```

完整 WebUI 后端测试：

```bash
.venv/bin/python -m unittest discover -s dsakiko_webui/backend/test -v
```
