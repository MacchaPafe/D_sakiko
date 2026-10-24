# 后端首批实现与调用方式

更新：2026-10-15。设计依据仍是 `core-interfaces.md`、`python-core-design-review.md` 和 `python-feature-requirements.md`。本文件记录实际实现边界，不能把构建成功或旧原型测试等同于全部需求验收。

## 入口与分层

正式装配入口为 [create-core-backend.js](../src/backend/create-core-backend.js)。业务代码写在原有 `src/backend/*/interface.js` 中，构造函数接收能力，返回值保持普通数据。旧装配入口、扁平对话与业务实现、演出 IPC 代理及对应脚本已经删除。公共文件工具迁入 `backend/core/files.js`，Python 传输整理为 `speech/python-transport.js`，通用错误位于 `shared/problems.js`；正式后端不再依赖旧桌面协议。

- 后端 Performance 拥有所有 sequence 的计划时钟、队列、guide 终态和模型计划目标；没有前台选择，没有客户端播放回报接口。
- Conversations 拥有 Chat、Turn、保存状态、受理门禁、async 组和提醒。查询与订阅交付同一份已发布快照，避免读取一次修改的中间状态。
- HTTP/WS 只映射白名单业务入口；凭据解析、资源本机路径、工具执行、提醒投递、交互资格和底层队列写接口不公开。
- 前端 `backend/client.js` 是框架无关的传输封装；`performance/interface.js` 是本地播放控制器。React 组件、舞台布局和 Live2D SDK 挂载留待 UI 阶段。
- Electron IPC 只提供 `dsakikoHost.getBackendConnection()`、运行信息和文件选择导入。Python 进程由主进程管理，随应用退出关闭。

宿主分别注入 `appDataRoot`、`resourceLibraryRoot` 和 `cacheRoot`。应用数据保存设置、聊天文本、图片与资源登记，资源库保存模型及音频，缓存保存临时材料、日志和 Chromium 会话数据。三个环境变量分别为 `DSAKIKO_DATA_DIR`、`DSAKIKO_RESOURCE_LIBRARY_DIR`、`DSAKIKO_CACHE_DIR`；Python 解释器仍使用 `DSAKIKO_PYTHON`。旧 `DSAKIKO_RESOURCE_ROOT` 已停用。具体布局与兼容限制见[数据目录设计](data-directories-and-resources.md)。

## 已实现

| 模块                        | 当前行为                                                                                                                                                                                 |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Settings / CharacterCatalog | JSON 持久化、版本比较、生成/语音选项校验、连接私有凭据、形态与模型选择、人格快照。凭据文件采用仅当前用户读写权限；尚未接系统钥匙串。                                                     |
| ChatStore / MediaAssets     | 不可变 Chat 版本文件与原子索引提交、提醒 ID 唯一性、摘要排序、损坏检测及保留原件恢复、JSON 资源归档；资源导入、引用集合和受控读取。                                                      |
| Conversations               | 嵌套历史、先持久化再受理、运行中保存故障保留内存、retrySave 不重跑、停止与当前句门禁、末轮重生成、历史编辑/截断/分支、摘要、音频重合成、提醒扫描。                                       |
| Agent                       | 实际 AI SDK 非流式请求、兼容 Chat Completions / DeepSeek、固定 Nunjucks 模板、响应分组、工具串行执行、有限格式修复、完整 Turn 上下文投影与估算、摘要生成。能力明确支持时可发送内联附件。 |
| Tools / Interactions        | 依赖闭包、宿主能力检查、sync/async 两模式；内置 ask_user、create_reminder。交互登记不等待客户端，JSON Schema 校验答案，第一个有效答案获胜，成功结果批量进入后续 Turn。                   |
| 后端 Performance            | 无客户端也推进；未 ready 的队首不跳过；多 sequence 独立；停止保留当前句；等待者取消不取消任务；目标状态动作排队、幂等与抢占处理。                                                        |
| Speech / Python             | 复用 Python 调度器，固定语言/声音/采样值、共享结果导入、有限通信重试；可信资源根与应用资源目录隔离检查，启动精度和结构配置传入 worker。                                                  |
| HTTP / WS                   | 仅监听 127.0.0.1；Bearer、Host/Origin 检查；单次短期 WS 票据；完整快照订阅；慢客户端断开后重新同步；短期资源地址与音频 Range 请求。                                                      |
| 前端封装                    | 分模块 Promise 方法、订阅重连、可供 useSyncExternalStore 使用的快照容器、本地总/BGM 音量、独立回放、受理成功后恢复实时、无 UI 的音频输出适配器。                                         |

后端模型状态是**计划目标**，不是任何客户端实际模型实例的确认值。具体渲染器须根据最新目标实现本地状态，不向后端报告“播放完成”。逐字进度由客户端按当前 guide 的 elapsedMs、durationMs 和本地单调时钟估算，不逐字请求后端。

保存版本采用单个后端进程作为唯一写者；不支持两个进程同时打开同一组数据目录。聊天只保留当前已提交 revision：写完新版本并发布索引后清理旧文件，启动时清理中断残留；损坏恢复的 `.backup` 保留。持久引用由存储模块统一交接，运行使用由临时 owner 或租约保护，具体见[资源生命周期](data-directories-and-resources.md#引用生命周期与聊天版本清理)。`collectUnused` 仍明确返回不可用，不删除无引用的资源文件；目录迁移、容量查看、配额和资源自动回收暂缓。

## 前端调用示例

下面只展示未来页面如何使用封装，本次没有接入现有 React 原型：

```js
import { createBackendClient, watchConversation } from './backend/client.js'
import { Performance } from './performance/interface.js'
import { createAudioOutput } from './performance/audio-output.js'

const connection = await window.dsakikoHost.getBackendConnection()
const client = createBackendClient(connection)
const playback = new Performance({ client, output: createAudioOutput(client) })
const chatStore = watchConversation(client, chatId)

// 后续组件可使用 useSyncExternalStore(chatStore.subscribe, chatStore.getSnapshot)。
const snapshot = await client.conversations.getSnapshot(chatId)
playback.follow(snapshot.presentation.sequenceId)

// requestId 在同一次业务发送重试时复用。只有受理成功才退出本地回放。
const requestId = crypto.randomUUID()
await playback.createTurn(chatId, { text: '你好', attachments: [] }, { requestId })

const materials = await client.conversations.getReplayMaterials(chatId, {
  kind: 'turn',
  turnId
})
playback.replay(materials, { silent: false })
playback.setPlaybackOptions({ volume: 0.8, bgmVolume: 0.4 })

// 回放不因其他客户端的新消息而中断，用户可主动恢复。
playback.resumeLive()
chatStore.dispose()
playback.close()
client.close()
```

WebUI 使用同一个 `createBackendClient({ baseUrl, token })`，不依赖 `window.dsakikoHost`。当前尚未实现浏览器登录/配对页面；可信宿主须提供 token，并将实际 WebUI Origin 加入服务白名单。不得把永久 token 放在查询参数或永久资源引用里。

直接调用通常不需要关心传输身份。需要恢复一个结果不确定的非 Turn 命令时，显式保存 `call` 的 requestId：

```js
await client.call(
  'interactions',
  'complete',
  [{ requestId: interactionId, status: 'succeeded', value: 'A' }],
  { requestId: commandId, signal }
)
```

这里 `commandId` 是传输命令身份；`interactionId` 是问题身份，两者用途不同。HTTP 写命令缓存同 ID/材料的结果，异材料拒绝；缓存仅在一次后端运行期内有效，上限 10000 条，满后明确拒绝，不悄悄淘汰后重放副作用。Turn 另有随 Chat 保存的受理记录，重启、重生成或截断历史后相同业务请求也不重新执行。取消 fetch/订阅只取消客户端等待，停止业务须显式 `stopTurn`。

上传字节使用 `client.assets.upload({ name, mediaType, base64, purpose })`，请求 JSON 上限 8 MiB。`purpose` 必须是已支持的用途，如 `chat-image`、`avatar`、`audio` 或 `model`；不接受目录类别或本机路径。大权重使用 `dsakikoHost.importFiles({ purpose: 'model' })`。模型目录可使用 `{ purpose: 'live2d', directory: true, entry: 'model.model3.json' }` 整包复制，保留包内相对结构；这不包含 ZIP 解包与角色模型配置生成。读取资源使用 `client.assets.access(asset)`，当前地址有效 5 分钟，不持久化 URL。模型包成员可在该地址附加 `file` 查询参数，值为经 URL 编码的包内相对路径；后端校验实际路径仍在包内。

## 尚未接入的模块与验收边界

- 世界书真实检索与知识权限过滤、厂商 Files API、Node 识别引擎、Python 旧存档/模型包导入适配仍未实现。启用世界书或选择本轮不支持的附件方案会明确拒绝，不伪装成已检索或已上传。
- Live2D 实际渲染、桌宠/托盘窗口切换、输入草稿/录音组件、WebUI 配对与页面均不在这次 UI 之外的首批实现里；已提供播放输出接点，尚未证明实际模型动作与口型正确。
- 上下文 token 数为估算，含附件时返回 unknown；目前超预算支持按完整历史压缩，仍超预算则明确拒绝，没有实现进一步按预算自动裁掉完整旧 Turn 的策略。
- 未调用付费模型、未加载真实 GPT-SoVITS 权重，不能据协议测试宣称供应商能力、设备精度兼容性或实际合成品质已验收。
- 原型 React 文件保持原状。主进程/preload 已切换到新 bootstrap，因此旧页面不能作为新后端使用界面；仅保留仍存在的显示层和草稿测试。旧业务集成测试、旧页面 smoke 与外部模型验收脚本已移除；新的宿主 smoke 仅验证 bootstrap、HTTP 和退出清理，页面测试待 UI 阶段补充。

验证使用 `pnpm test`、`pnpm lint`、`pnpm build`、`pnpm test:python`。新增 `tests/core` 直接验证正式接口，包含真实本机 HTTP/WS 和 AI SDK 对模拟模型服务的请求，不依赖原型页面。

2026-10-15 多目录接入验证：35 项 JS 测试、11 项 Python 测试通过，包含真实 Python 进程握手；lint、构建和 Python 3.9 语法检查通过。宿主冒烟同时验证图片、音频与模型导入的位置。macOS ARM64 临时 `.app` 也通过同一冒烟测试，运行前后 715 个包内文件、目录及链接记录无变化。测试包使用本地 ad-hoc 签名并仅在打包命令中关闭 Hardened Runtime，发布配置未改动；这不代表正式签名、公证、Windows 实机或真实模型推理已验收。打包所需的既有 `zod` 版本已显式列入运行依赖。
