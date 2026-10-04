# 目录与开发约定

## 运行环境

React 通过 runtime 客户端调用 preload 暴露的具名方法。preload 使用 Electron IPC；主进程核对请求来自当前主窗口的顶层页面，再调用 Node 后端。共享协议只包含能跨环境使用的数据与常量。

当前只有运行状态查询，用于验证工程连接，不承载业务流程。

- main 可以导入 backend 和 shared。
- preload 可以导入 shared，并使用受限的 Electron 能力。
- backend 不导入 Electron、React 或前端代码。
- renderer 不导入 Electron、Node、main、preload 或 backend 的实现。
- shared 不导入 Electron、Node 专用模块、React 或其他层的实现。

preload 输出为独立 CommonJS 文件，并启用 contextIsolation、sandbox、禁用 nodeIntegration。页面不能自行打开新窗口或跳转到其他页面。

## 模块位置

| 目录                       | 后续职责                               |
| -------------------------- | -------------------------------------- |
| main/windows               | 主窗口与后续的桌宠窗口                 |
| main/system                | 托盘、系统路径、文件选择               |
| main/processes             | Python 等子进程的启动、就绪与退出管理  |
| backend/conversation       | 对话、轮次、生成、取消与消息持久化协调 |
| backend/characters         | 角色身份、角色描述及可选能力           |
| backend/llm                | 模型请求、Agent Loop 与工具调用        |
| backend/settings           | 设置校验、读取与保存                   |
| backend/storage            | 存储机制与数据版本迁移                 |
| backend/speech             | 合成请求与结果转换、Python 客户端      |
| backend/resources          | 附件、语音文件与模型资源管理           |
| backend/remote-files       | 厂商文件上传复用、引用失效与远端删除   |
| python/speech              | 合成调度、模型复用、资源上限与推理     |
| renderer/src/app           | 应用级布局、Provider 与页面组装        |
| renderer/src/features      | 按聊天、角色、设置等用户功能组织的界面 |
| renderer/src/components/ui | shadcn 通用基础组件                    |
| renderer/src/runtime       | 前端通信实现与统一调用入口             |
| renderer/src/performance   | 演出序列、音频、字幕及 Live2D 协调     |
| renderer/src/hooks         | 多个功能确实复用的 hooks               |
| renderer/src/lib           | 少量与业务无关的前端工具               |
| renderer/src/assets        | 参与前端构建的资源                     |
| renderer/public            | 原样复制到前端产物的公开静态资源       |

业务功能内部可以按需加入 components、hooks 或状态文件；不为尚未出现的需求创建多层抽象。业务状态属于 Node 后端，页面切换不应决定生成任务是否存活。

角色身份保持独立于 Live2D 和语音能力。演出进度与聊天记录分别管理，回放不重复生成或写入消息。

## 测试

模块测试靠近源码，文件名为 *.test.js 或 *.test.jsx；跨模块测试放在 tests/integration。

tests/e2e 验证真正构建后的 Electron 窗口。默认 pnpm test 不启动图形界面，pnpm test:smoke 单独运行桌面冒烟测试。

当前 Vitest 默认使用 Node 环境；实现 React 交互测试时，再按需引入 DOM 测试环境和 Testing Library。

## 构建与资源

electron-vite 分别构建 main、preload 和 renderer，输出到 out。electron-builder 从构建结果和运行依赖生成应用，目标平台只有 Windows 和 macOS。

resources 随应用发布；build 只供打包使用；用户数据与大型模型使用安装目录外的位置。当前模板图标只是占位素材。

未来启动 Python 时，解释器、工作目录和模型目录都必须显式配置，不依赖父仓库固定路径。Python 可执行文件不能直接在 ASAR 内运行。
