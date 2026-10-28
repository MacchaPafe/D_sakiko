# 内置工具与对话工作目录

更新：2026-10-26。本次只实现后端与共享契约，前端工具设置、文件列表、抽签动画仍待接入。

## 工具目录与启用方式

`src/backend/tools/builtins.js` 汇总内置工具，各文件负责参数声明和执行。`create-core-backend.js` 只装配依赖。工具通过已有 `conversations.setTools(chatId, { enabled: [...] })` 按对话启用；默认全部关闭。本轮已解析的工具集合保持不变，修改选择从下一轮生效，依赖自动补齐。

| 工具                         | 行为                                                                                                   |
| ---------------------------- | ------------------------------------------------------------------------------------------------------ |
| `get_current_datetime`       | 返回后端 UTC、本地时间、时区与偏移                                                                     |
| `get_system_hardware_status` | 通过 Node OS 和 systeminformation 查询后端 CPU、内存、可选 GPU；传感器单项失败或超时不会使整个结果失败 |
| `list_directory`             | 分页浏览目录，默认当前 workspace；每页 100 项                                                          |
| `read_file_content`          | 读取 UTF-8 文本，按行与列继续分页，每页至多 200 行、12000 字符                                         |
| `grep_search_in_file`        | 搜索字面关键词，返回行号与片段；每页至多 30 条                                                         |
| `export_document`            | 向当前 workspace 写入 UTF-8 内容，返回可下载 AssetRef；已有文件必须显式 `overwrite: true`              |
| `get_weather`                | 明确城市优先，其次全局城市，最后可用 IP 自动定位                                                       |
| `web_search`                 | 使用 Bocha，返回标题、URL、摘要；预留 provider 配置，未实现的厂商明确报错                              |
| `fetch_all_live2d_models`    | 列出当前对话角色、当前形态中可解析的服装                                                               |
| `change_character_live2d`    | 保存当前形态的服装选择并更新后端场景目标；依赖服装查询工具                                             |
| `start_lottery`              | 创建持久抽签请求，等待用户开始，由后端确定结果                                                         |
| `ask_user`                   | 保留已有问题交互流程                                                                                   |
| `create_reminder`            | 保留已有持久提醒流程；自动依赖时间查询工具                                                             |

除问题与抽签返回异步受理外，其余工具在本轮返回执行结果。这里的同步工具仍使用可取消的异步网络或文件 I/O，不阻塞 Node 事件循环。服装工具成功代表后端状态已保存，前端按场景快照加载模型；不等待渲染确认。抽签动画与角色回应使用各自的展示流程。

## 文件输入、存储和下载

文件先通过现有导入接口登记为 AssetRef。正式发送时，图片作为本地消息附件保存，再按现有规则选择内联或厂商 File API；非图片复制到 `<appDataRoot>/assets/workspaces/<chatId>/<filename>`，登记到 `chat.meta.workspaceFiles`。消息保留文件引用，模型只看到工作文件说明，必须用文件工具读取。上下文预览不复制文件。图片检查以已登记资源的 MIME 为准，厂商限制继续由 RemoteFiles 处理。

上传时同名文件自动增加编号；模型写文件必须指定单个文件名，禁止绝对路径、目录分隔符、上级路径和符号链接。写工具不提供删除目录、执行命令、任意路径写入等能力。索引、聊天存档和设置位于 workspace 之外。

工作文件是可变内容：覆盖时保留 AssetRef，历史下载引用也读到新内容，不保存各次修改快照。图片原附件仍保存在原资源位置。工作文件列表通过 `conversations.listWorkspaceFiles(chatId)` 查询，再用现有 `assets.access(asset)` 获得短期下载 URL。文件列表和抽签状态也随 Chat 快照发布。

分支复制与归档导入为工作文件创建独立副本，不共享可写路径；复制不继承未完成抽签。归档只含内容和引用，不携带本机可读目录授权。删除对话解除资源引用，物理文件沿用 `collectUnused` 回收流程，不自动删除仍有其他持有者的资源。

文本工具默认上限 2 MiB，只解析 UTF-8 文本和代码。PDF、Word、PPT、Excel 可以保存和下载，当前没有文档解析器。单个本地工作文件接纳上限 64 MiB；HTTP 上传仍受既有 8 MiB JSON 请求体上限约束，不能把内部上限理解成远程上传上限。

## 全局配置与联网服务

配置保存在应用 `settings.json` 的 `settings.tools`，使用 `settings.update` 部分更新。读取策略是全局配置，不保存在对话或归档中。

```js
await backend.settings.update({
  tools: {
    files: {
      readMode: 'all',
      readableRoots: [],
      maxReadBytes: 2097152
    },
    webSearch: {
      provider: 'bocha',
      baseUrl: 'https://api.bocha.cn',
      timeoutMs: 12000,
      maxResults: 5
    },
    weather: { city: '', ipLocation: true }
  }
})
await backend.settings.saveToolCredential('bocha', {
  kind: 'replace',
  secret: '用户提供的密钥'
})
```

`readMode: 'all'` 不增加应用层目录限制，仍受操作系统权限约束。设为 `roots` 后，只允许 workspace 及 `readableRoots` 内的真实路径；空列表表示仅允许 workspace。此策略不会放宽写入范围。工具配置每次执行读取，不能被模型参数改写。

搜索密钥单独存在后端私有 `secrets` 中，通过 `getToolCredentialStatus('bocha')` 只返回是否配置，不通过公开设置、对话归档或工具结果返回密钥。`keep`、`replace`、`remove` 分别保留、替换、删除。搜索 `provider` 当前只实现 Bocha，填入 `deepseek` 会报告尚未实现。

天气默认使用后端出口 IP 估计位置，缓存位置 30 分钟；远程部署或代理环境中可能不同于用户所在地。自动定位失败会要求明确城市，不猜测城市。接口分别采用 [IPWhois](https://ipwhois.io/documentation)、[Open-Meteo 天气](https://open-meteo.com/en/docs) 和 [地理编码](https://open-meteo.com/en/docs/geocoding-api)。Bocha 请求格式依据其[官方接口说明](https://github.com/Bocha-Labs/bocha-skills/blob/main/bocha-web-search/SKILL.md)。联网响应限制为 2 MiB，禁止自动跳转，超时、取消或响应异常会返回有界错误；不转发远端错误正文和凭据。

## 抽签交付

`start_lottery` 接受标题和 2–30 个互不相同的候选项。前端从 `listLotteries(chatId)` 或 Chat 快照展示请求，用户点击开始时调用 `drawLottery(chatId, lotteryId)`。后端在对话串行队列内使用 `crypto.randomInt` 抽取并保存，再返回 `{ result: { index, option }, ... }`。客户端只按该结果播放动画，不再提交随机结果。

多个客户端重复开始均返回第一次已保存的结果。后端用稳定请求 ID 受理自动回复，交付标记与新 Turn 一起保存；动画完成不是交付前提。已有生成或角色演出尚未结束时排队，恢复后继续。正常重启保留已完成创建的请求及未交付结果；创建工具尚未完成的中断记录不能继续开始。手动停止、修改历史或开始新的手动轮次会取消未交付请求；已抽取结果仍可查询，不重新抽取。存档失败时先通过既有 `retrySave` 处理，失败期间不交付。

普通 `interactions.complete` 不能决定抽签结果。新增公开方法仅为列表与开始，实际工具执行、写文件回调、服装切换工具回调、结果交付和密钥读取仍不在 HTTP 白名单内。

## 验证边界

`tests/core/builtin-tools.test.js` 覆盖实际后端的工作文件输入、重复受理、可变下载、分支/归档隔离、读取限制、越界和符号链接、工具选择、抽签重启/并发/保存故障，以及替换网络和硬件查询后的返回行为。`remote-files.test.js` 使用真实 AI SDK 检查图片上传引用，保留底层上传适配器的文件能力验证。

天气与搜索自动测试使用模拟 HTTP 响应，未使用真实 Bocha 密钥或调用付费模型。构建与后端测试不代表前端动画、实机 GPU、厂商线上能力已验收。
