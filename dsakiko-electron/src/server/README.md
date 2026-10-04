# 浏览器宿主预留位置

当前尚未启动 HTTP 或 WebSocket 服务。

后续在此创建独立 Node 入口，复用 backend，通过 HTTP 和 WebSocket 提供与桌面通信语义一致的能力，并补充访问控制、重连与状态恢复。浏览器客户端适配器放在 renderer/src/runtime。
