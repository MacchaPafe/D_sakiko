# 数字小祥 · Electron

正式后端已按接口设计实现首批能力：对话与存档、角色与设置、Agent、工具交互、后端演出计划、Python 语音及 HTTP/WebSocket 传输。具体完成范围与限制见 [实现说明](docs/backend-implementation.md)，设计依据见 [核心接口](docs/core-interfaces.md)。

**当前 React 页面仍是旧原型，尚未接入新后端，不能作为可用聊天界面。** 旧原型业务后端及对应验证脚本已清理；页面、显示层和所需的旧共享协议暂留，等待后续 UI 工作。正式前端封装位于 `src/renderer/src/backend/client.js`，不依赖 React。

## 本机开发

需要 Node.js 22.12+、pnpm 11。语音推理另需可运行 GPT-SoVITS 的 Python 环境；未配置语音环境时仍可开发、测试 Node 后端。

```sh
cd dsakiko-electron
pnpm install
# 可将 .env.example 复制为 .env.local，填写资源与 Python 解释器的绝对路径
pnpm dev
```

`pnpm dev` 启动 Electron 宿主、本机业务服务和保留的原型页面，不代表页面适配已经完成。开发环境分别使用 `.local/core-v1/`、`.local/library/` 和 `.local/cache/`。正式环境的应用数据位于 macOS Application Support 或 Windows LocalAppData 下，资源库与缓存单独配置，见[数据目录设计](docs/data-directories-and-resources.md)。旧单目录资源索引不会自动迁移或覆盖。

`DSAKIKO_DATA_DIR`、`DSAKIKO_RESOURCE_LIBRARY_DIR`、`DSAKIKO_CACHE_DIR` 分别覆盖三个根目录，均使用绝对路径且不能重叠。`DSAKIKO_PYTHON` 指定解释器。旧 `DSAKIKO_RESOURCE_ROOT` 已停用，仍配置该变量时启动会明确报错；更新配置需同时准备新资源库，程序不搬运原文件。具体依赖与限制见 [Python 运行时](python/README.md)。不要把 API 密钥放入 `.env.local` 或日志；模型连接通过正式 Settings 接口配置。

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

| 命令                              | 内容                                                                       |
| --------------------------------- | -------------------------------------------------------------------------- |
| `pnpm test`                       | 正式接口、HTTP/WS、前端封装及保留显示层的回归；不调用外部模型服务          |
| `pnpm lint` / `pnpm format:check` | 代码与格式检查                                                             |
| `pnpm build`                      | 构建 main、preload 和 renderer                                             |
| `pnpm test:python`                | 无需真实权重的 Python 调度与 HTTP 协议测试                                 |
| `pnpm test:smoke`                 | 构建并启动真实 Electron，验证新宿主 IPC、HTTP 和退出清理；不验收旧页面功能 |

`scripts/demos/` 是独立的技术验证材料，按各自说明运行，不是原型业务后端的运行依赖。构建脚本保留 Windows/macOS 打包入口，安装包和真实设备推理仍须单独验证。
