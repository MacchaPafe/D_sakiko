# D_sakiko WebUI Frontend

独立 WebUI 的 Phase 1A Mock 前端。当前版本不连接 Python 服务端，用本地 Mock Runtime 演示移动端的会话主页、聊天模式、角色模式、Live2D、分段回复和音频播放。

## 开发

要求 Node.js 20.19+ 或 22.12+。

```bash
npm install
npm run dev -- --host 0.0.0.0
```

Vite 会同时输出本机地址和局域网地址。手机与电脑处于同一局域网时，可在 Android Chrome 中打开对应的 Network 地址。

## 验证与构建

```bash
npm run lint
npm run build
npm run preview -- --host 0.0.0.0
```

开发服务器与生产构建都会从项目根目录读取 Mock 所需的 Live2D 模型、头像、背景和音频。`node_modules/`、`dist/` 以及复制后的 Mock 资源不会提交到仓库。

## 当前边界

- 仅面向 Android Chrome 设计和验证。
- 所有聊天、生成时序和协议事件均来自 Mock Runtime。
- 多段回复按 segment 显示为多个独立气泡，每段保留自己的音频入口。
- 草稿按 `chat_id` 保存；回复生成期间可查看会话列表，但不能切换或新建会话。
- Python 后端、WebSocket、鉴权、真实 LLM/TTS 和历史记录持久化将在后续阶段接入。
