# 数字小祥 · Electron

正式后端已按接口设计实现首批能力：对话与存档、角色与设置、Agent、工具交互、后端演出计划、Python 语音及 HTTP/WebSocket 传输。具体完成范围与限制见 [实现说明](docs/backend-implementation.md)，设计依据见 [核心接口](docs/core-interfaces.md)。

**当前已接通欢迎页 → 快速开始／旧版导入 → 可选资源 → 正式主页。** 快速开始在最终进入主页时才提交正式角色、连接和资源绑定；此前仍可返回并改选导入。主页支持角色与聊天导航、文字发送／停止、新建／删除、悬浮记录、Live2D 换装、舞台背景、整句字幕进度、口型、通知堆叠和后台资源任务。引导共用所选角色并保留鼠标追踪，主页关闭追踪。全窗口舞台入口仍禁用。正式通信封装位于 `src/renderer/src/backend/client.js`，范围与交互见[前端设计](docs/frontend-design.md)。

## 舞台背景

在单人对话或小剧场的舞台工具栏点击「切换背景」，即可搜索已下载图片。鼠标停留或用方向键聚焦 1.5 秒可在角色身后预览，点击或按回车应用。每段对话独立保存背景，回放台词使用当前背景。

「默认背景 · 内置」固定在快捷面板和「已下载」的第一项，首次打开即可预览、应用。

「管理背景」打开「舞台资源」的在线图库。下载完成后可以直接应用，也可以从「已下载」多选导入 PNG、JPEG 或 WebP，包括 Python 版 `live2d_related` 里的背景。改名后仍可按原文件名搜索。从图库移除图片，已经使用它的对话会保留背景。

开发与打包时，主进程优先使用 `DSAKIKO_DOWNLOAD_TOKEN`，也可读取同仓库 Python 下载器的私有 `_credentials.py`。下载凭据只用于项目自有资源域名。

## 本机开发

需要 Node.js 22.12+、pnpm 12 或更高版本。语音推理另需可运行 GPT-SoVITS 的 Python 环境；未配置语音环境时仍可开发、测试 Node 后端。附加运行环境包的构建与 Windows 操作说明见 [运行环境包构建](docs/runtime-package-build.md)。

```sh
cd dsakiko-electron
pnpm install
# 可将 .env.example 复制为 .env.local，填写资源与 Python 解释器的绝对路径
pnpm dev
```

`pnpm dev` 启动 Electron 宿主、本机业务服务和欢迎页。依赖已安装时也可运行 `npm run dev`，启动脚本直接使用本地 electron-vite。真实预览需要 `resources/builtin-characters/` 中的内置清单和归档；缺少资源时页面显示错误并允许重试。开发环境分别使用 `.local/core-v1/`、`.local/library/` 和 `.local/cache/`。正式环境的应用数据位于 macOS Application Support 或 Windows LocalAppData 下，资源库与缓存单独配置，见[数据目录设计](docs/data-directories-and-resources.md)。旧单目录资源索引不会自动迁移或覆盖。

`DSAKIKO_DATA_DIR`、`DSAKIKO_RESOURCE_LIBRARY_DIR`、`DSAKIKO_CACHE_DIR` 分别覆盖三个根目录，均使用绝对路径且不能重叠。`DSAKIKO_PYTHON` 指定解释器。旧 `DSAKIKO_RESOURCE_ROOT` 已停用，仍配置该变量时启动会明确报错；更新配置需同时准备新资源库，程序不搬运原文件。具体依赖与限制见 [Python 运行时](python/README.md)。不要把 API 密钥放入 `.env.local` 或日志；模型连接通过正式 Settings 接口配置。

### 调试首次引导

```sh
pnpm dev fresh         # 每次创建一个空档案，直接进入欢迎页
pnpm dev fresh 验证导入  # 首次创建命名档案，再次运行可继续上次的进度
pnpm dev fresh-temp    # 每次创建一个空档案，退出后自动清理
```

调试档案保存在 `.local/dev-fresh/`，启动时会打印目录。每套档案有独立的应用数据、资源库、缓存和 Electron 用户目录，可以和现有客户端并行启动。`fresh` 的匿名和命名档案退出后都保留。`fresh-temp` 在退出应用或按 `Ctrl+C` 结束本次开发进程后，自动清理本次的临时档案。在 macOS 上可用 `⌘Q` 退出应用。

依赖已安装时，也可以用 `npm run dev -- fresh [名称]` 或 `npm run dev -- fresh-temp` 启动。

## 代码入口

- `src/backend/create-core-backend.js` 装配正式后端；业务实现位于各模块的 `interface.js`。
- `src/backend/core/files.js` 提供串行队列与原子 JSON 发布；`src/shared/problems.js` 提供通用问题异常。
- `src/backend/speech/python-transport.js` 转换 Python 协议并交接音频文件，轮询与业务契约由 `speech/interface.js` 负责。
- `src/server/http-server.js` 提供仅监听 `127.0.0.1` 的鉴权 HTTP/WS，公开方法由 `src/shared/contracts/api.js` 限定。
- `src/main` 管理 Electron 和 Python 生命周期；preload 仅暴露连接 bootstrap、宿主运行信息和文件选择导入。
- `src/renderer/src/backend/client.js` 封装业务方法、订阅重连与 React 可用的快照容器；调用示例见实现说明。
- `src/backend/performance/interface.js` 推进所有演出计划；`src/renderer/src/performance/interface.js` 管本地跟随、回放和音量。

旧原型的历史验证记录保留在 [原型说明](docs/prototype.md)，不代表当前正式接口的验收结果。旧数据和外部模型资源不因代码清理而删除。

## 检查命令

| 命令                              | 内容                                                                                                  |
| --------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `pnpm test`                       | 正式接口、HTTP/WS、前端封装及保留显示层的回归；不调用外部模型服务                                     |
| `pnpm lint` / `pnpm format:check` | 代码与格式检查                                                                                        |
| `pnpm build`                      | 构建 main、preload 和 renderer                                                                        |
| `pnpm test:python`                | 无需真实权重的 Python 调度与 HTTP 协议测试                                                            |
| `pnpm test:smoke`                 | 构建并启动真实 Electron，验证宿主、通信与退出；内置资源存在时同时验证欢迎页模型、追踪、切换和失败恢复 |

`scripts/demos/` 是独立的技术验证材料，按各自说明运行，不是原型业务后端的运行依赖。构建脚本保留 Windows/macOS 打包入口，安装包和真实设备推理仍须单独验证。
