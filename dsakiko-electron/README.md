# 数字小祥 · Electron

Windows / macOS 桌面客户端的初始化工程，使用 pnpm、Electron、React、JavaScript 和 shadcn/ui。

目前提供空白工作区、浅色/深色外观切换，以及 React → preload → Electron IPC → Node 后端的运行状态查询。尚未实现聊天、存档、语音或 Live2D 业务。

## 开发

需要 Node.js 22.12+ 和 pnpm 11；项目通过 packageManager 固定 pnpm 11.24.0。当前 Electron 要求 Windows 10+ 或 macOS 13+。

在本目录执行：

```sh
pnpm install
pnpm dev
```

请通过 Electron 启动应用。直接用浏览器打开开发服务器不具备 preload 提供的桌面接口。

## 常用命令

| 命令                | 用途                                                   |
| ------------------- | ------------------------------------------------------ |
| `pnpm dev`          | 启动开发服务器和 Electron 窗口                         |
| `pnpm build`        | 构建主进程、preload 和 React，输出到 out               |
| `pnpm start`        | 启动已经构建的程序，需要先执行 build                   |
| `pnpm lint`         | 检查 JavaScript 和 React 代码                          |
| `pnpm format`       | 格式化工程文件                                         |
| `pnpm format:check` | 只检查格式                                             |
| `pnpm test`         | 运行模块和集成测试；当前业务测试为空                   |
| `pnpm test:smoke`   | 构建并打开真实 Electron，检查页面、IPC、隔离与外观切换 |
| `pnpm build:unpack` | 生成当前平台可直接运行的应用目录                       |
| `pnpm build:mac`    | 构建 macOS 安装包                                      |
| `pnpm build:win`    | 构建 Windows 安装包                                    |

桌面冒烟测试需要图形桌面环境，运行结束后关闭测试窗口，截图保存在忽略提交的 `test-results/startup.png`。测试使用独立的临时用户数据目录。

Windows 与 macOS 安装包建议分别在对应系统构建；默认使用当前机器架构。当前保留模板图标，尚未配置签名、公证和自动更新，正式发布时再补齐。

## 目录

- `src/main`：Electron 生命周期、窗口、系统集成和 IPC。
- `src/preload`：向页面暴露少量明确的桌面能力。
- `src/backend`：不依赖 Electron 的 Node 业务模块。
- `src/shared/contracts`：跨进程数据约定与通道名称。
- `src/renderer`：React 前端，业务界面按 features 划分。
- `src/server`：后续浏览器访问入口，目前仅预留说明。
- `python`：后续 Python 子进程接入说明。
- `resources`：随应用发布的静态默认资源。
- `build`：安装包素材。
- `scripts`：开发与维护脚本。
- `tests`：集成测试和桌面端测试。

完整职责与依赖方向见 [目录与开发约定](docs/architecture.md)。空目录用 `.gitkeep` 保留，不代表已经实现对应模块。

核心业务的审查入口见 [核心模块接口原型](docs/core-interfaces.md)，包含核心模块的方法签名、输入输出与职责约束。原型仅包含 JSDoc 和空方法，尚未接入运行流程。

## 添加 shadcn/ui 组件

已配置 Tailwind CSS、JavaScript/JSX、样式变量和路径别名：

```sh
pnpm dlx shadcn@latest add button
```

组件写入 `src/renderer/src/components/ui`，业务组件放在各自的 `features` 中。新增组件后补充项目要求的中文 JSDoc，并运行格式与代码检查。

## 与旧项目共存

本工程具有独立的依赖、锁文件和构建配置，不导入父目录的 Python 或旧 WebUI 代码。后续需要调用旧资源时，通过显式配置传入资源位置。

聊天数据、用户设置和大型模型不写入源码目录或安装包。将来由桌面宿主解析用户数据路径，再传给 Node 后端。

pnpm 11 的项目设置位于 `pnpm-workspace.yaml`；本工程仍然只有一个 JavaScript 包。安装脚本仅为 Electron、esbuild 和 Tailwind 原生依赖开放。
