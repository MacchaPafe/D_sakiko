# HTTP / WebSocket 适配

`http-server.js` 将正式后端模块映射到共享白名单 `src/shared/contracts/api.js`。由 Electron 主进程调用 `startHttpServer(backend)`，仅监听 `127.0.0.1`，不要求独立守护进程。

业务客户端位于 `src/renderer/src/backend/client.js`；页面和 Electron 使用同一协议。读写命令通过 HTTP，conversation / performance / interactions / settings 完整快照通过 WebSocket。私有凭据解析、任意方法调用、磁盘路径和工具执行入口不公开。

鉴权、去重、调用示例及实际实现边界见 [backend-implementation.md](../../docs/backend-implementation.md)。
