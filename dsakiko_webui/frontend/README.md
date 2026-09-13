# D_sakiko WebUI Frontend

独立 WebUI 的 React 前端。页面优先通过二维码中的一次性配对凭证登录 Python 后端，六位访问码作为备用方式；认证后使用 WebSocket 同步会话、Live2D、分段回复和音频状态。

## 开发

要求 Node.js 20.19+ 或 22.12+。

```bash
npm install
npm run dev -- --host 0.0.0.0
```

先在项目根目录启动 WebUI 后端。Vite 会把 `/api` 和 WebSocket 代理到本机 `8000` 端口，并同时输出本机地址和局域网地址。手机与电脑处于同一局域网时，可在 Android Chrome 中打开对应的 Network 地址。

## 验证与构建

```bash
npm run lint
npm test
npm run build
npm run preview -- --host 0.0.0.0
```

`node_modules/` 和 `dist/` 不提交到仓库。发布前执行 `npm run build`，并将生成的 `dist/` 放入软件包；角色模型、头像、背景和音频由后端接口按需提供，不会复制进前端构建产物。

## 当前边界

- 仅面向 Android Chrome 设计和验证。
- 所有聊天、生成时序和协议事件均来自真实 WebSocket Runtime。
- 多段回复按 segment 显示为多个独立气泡，每段保留自己的音频入口。
- 草稿按 `chat_id` 保存；回复生成期间可查看会话列表，但不能切换或新建会话。
- 同一时间只有一个控制端；持有访问码的新设备可以接管旧设备。
- 配对凭证只在内存中短暂存在，页面会在首次异步请求前清除 URL fragment。
- 六位访问码被限速时，页面按服务端 `Retry-After` 显示倒计时且不会自动重试旧访问码。
