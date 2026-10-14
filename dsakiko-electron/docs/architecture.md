# 目录与开发约定

## 运行环境

业务通信统一使用 HTTP/WebSocket。Electron 的 main 装配正式 Node 后端并监听 `127.0.0.1`；前端通过 `backend/client.js` 调用业务方法和订阅完整快照。WebUI 复用同一封装，由自己的授权入口提供连接与凭证。

preload 仅通过 IPC 暴露 `dsakikoHost.getBackendConnection()`、`getRuntimeInfo()` 和 `importFiles()`。主进程验证调用来自当前主窗口顶层页面。文件选择等桌面能力由宿主管理，不为了统一 HTTP 而让后端承担窗口上下文。

- main 可以导入 backend、server 和 shared。
- preload 可以导入 shared，并使用受限的 Electron 能力。
- backend 不导入 Electron、React 或前端代码。
- renderer 不运行导入 Electron、Node、main、preload 或 backend 的实现；JSDoc 类型引用不产生运行时依赖。
- shared 的运行时代码不导入 Electron、Node、React 或其他层的实现。

preload 构建为独立 CommonJS 文件，窗口启用 contextIsolation 和 sandbox，禁用 nodeIntegration。页面不能自行打开新窗口或跳转到其他页面。业务后端随 Electron 退出关闭，不要求退出后仍在后台运行。

## 模块位置

| 目录或入口                            | 当前职责                                          |
| ------------------------------------- | ------------------------------------------------- |
| backend/create-core-backend.js        | 正式模块装配与提醒扫描、关闭生命周期              |
| backend/core                          | 公共文件工具、快照通知、固定默认值与输入校验      |
| backend/conversation                  | 对话运行状态、受理、历史、保存、async 结果和提醒  |
| backend/performance                   | 无客户端也推进的演出计划，不区分前后台            |
| backend/characters                    | 角色、形态与人格目录                              |
| backend/llm                           | 固定 Nunjucks 模板、模型请求和工具循环            |
| backend/settings                      | 共享设置、模型连接及后端私有凭据                  |
| backend/storage                       | 原子存档索引、不可变版本文件、归档导入导出        |
| backend/resources                     | 受管理资源与引用交接                              |
| backend/speech                        | 正式语音接口及 Python 协议适配器                  |
| server                                | 鉴权 HTTP/WS、命令去重与短期资源读取              |
| shared                                | 可跨环境使用的数据契约和问题异常                  |
| main/windows、main/processes          | 窗口与 Python 子进程生命周期                      |
| renderer/src/backend                  | Electron/WebUI 共用的业务封装、订阅恢复和快照容器 |
| renderer/src/performance/interface.js | 本客户端跟随、回放、字幕进度与音量                |
| python/speech                         | 唯一语音调度器、推理执行与模型资源预算            |

其他尚未实现的接口声明继续保留，具体边界见 [后端实现说明](backend-implementation.md)。不要根据存在接口文件就推断模块已实现。

`renderer/src/app`、`components`、`features`、`runtime` 和原有 Live2D/舞台显示代码仍用于未替换的 React 原型；其中旧通信协议不再有后端接收者。保留这些文件是为了后续页面工作，正式业务封装不依赖它们。

## 生命周期与维护

Conversation 是每个 Chat 的唯一运行时修改所有者。运行材料在受理时固定，页面切换和断线不决定生成是否存活。后端 Performance 只确认计划进度；实际音频、模型实例、回放与音量属于各客户端。

普通业务方法返回数据，不透传后端实例给页面。需要跨层访问时注入窄能力；不为单纯转发重复建立第二套业务实现。核心数据结构使用中文 JSDoc `@typedef`，公开入口说明参数和返回值，内部关键位置解释去重、时序、取消和资源所有权。

原型业务后端已经移除。串行队列与 JSON 工具迁入 `backend/core/files.js`；Python 传输整理为 `backend/speech/python-transport.js`；通用错误与旧桌面协议分离到 `shared/problems.js`。旧 `JsonStore`、扁平对话实现和演出 IPC 代理不再保留。

## 验证与构建

`tests/core` 验证正式接口与传输，`tests/integration` 保留仍存在的显示层和草稿回归。`tests/e2e/host.test.js` 启动构建后的 Electron 验证宿主 bootstrap、renderer 发起 HTTP 请求以及退出清理，不以旧页面功能作为通过条件。默认 `pnpm test` 不启动图形界面，`pnpm test:smoke` 单独运行宿主冒烟测试。

electron-vite 分别构建 main、preload 和 renderer，输出到 out。固定 Nunjucks 模板复制到构建资源目录；用户数据、大型模型与运行日志位于安装目录外。Python 解释器、工作目录和资源目录由宿主显式提供，不依赖父仓库隐式导入。安装包及不同设备上的真实推理需另行验收。
