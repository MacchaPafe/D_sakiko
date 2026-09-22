# 对话反馈服务

本目录实现公开接收 Worker、独立 Access 管理 Worker 和 D1 迁移。客户端在 `GPT_SoVITS/feedback/`，界面集成在 Qt 的回复反馈入口、聊天右键菜单和“更多功能 → 反馈”。当前没有部署到任何 Cloudflare 账号；发布前需要确定接收方、联系方式和域名。

## 本地检查

需要 Node.js 22.16+（测试使用 `node:sqlite`）以及项目 Python 3.11 环境。

```sh
cd services/feedback
npm ci
npm test
npm run check
cd ../..
PYTHONPATH=GPT_SoVITS QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  GPT_SoVITS/test/test_feedback.py GPT_SoVITS/test/test_feedback_ui.py \
  GPT_SoVITS/test/test_worldbook_diagnostics.py GPT_SoVITS/test/test_chat_backup_ui.py \
  GPT_SoVITS/test/test_dp_local2_json_output.py
```

`npm test` 先从唯一的 `GPT_SoVITS/feedback/schema.json` 生成校验器，再执行真实 SQLite、workerd / D1、Access JWT 测试。Python 跨语言测试应在生成校验器之后运行。生成文件需要随代码一起提交；Worker 中不动态执行 AJV 编译，避免依赖 `eval`。Wrangler 和 Miniflare 固定为配套版本，测试通过官方 V4 配置转换器配置本地运行时。测试仅用合成反馈，不发送当前用户对话。

## 部署

1. 在协作者账号下创建 D1，取得 Account ID、Database ID，并把两个 Wrangler 配置的占位数据库 ID 替换成实际值。无需索取账号密码或全局 API Key。可由协作者部署，或给部署者相应账号的最小权限 API Token（Workers Scripts、D1；自行配置域名路由时再加入相应区域权限）。管理反馈用的 Access service token 与部署令牌分开。
2. 为公开 Worker 和管理 Worker 各配置一个自定义域名（Wrangler `routes`，`custom_domain: true`）。配置关闭了 `workers.dev` 和预览 URL；不要另开绕过入口。
3. 对管理域名建立 Cloudflare Access 自托管应用，只允许开发者身份 / 专用 service token；填写管理配置的 `ACCESS_TEAM` 和应用 `ACCESS_AUD`。管理 Worker 自行验证 JWT 签名、issuer、audience、有效期，并拒绝跨站 Origin。缺少配置时全部拒绝。
4. 应用迁移并部署两个 Worker：

```sh
npx wrangler d1 migrations apply dsakiko-feedback --remote
npx wrangler deploy
npx wrangler deploy --config wrangler.admin.jsonc
```

5. 使用本账号实际套餐核定 `limits` 表预算。初值为每日 200 份、原始 JSON 合计 20 MiB、gzip 合计 3 MiB、在库存储正文 300 MiB；每日最多新增 1000 个控制记录。此预算预留数据库元数据/索引空间，仍需监控数据库实际大小。`used_stored` 只统计 BLOB 字节，不是数据库物理大小。每日计数不会因撤回恢复。两个配置的 CPU 上限为 50 ms，使用免费 Workers 套餐时需按该套餐上限调整并压测；超过 CPU 上限的请求不会收到业务回执。
6. 完成接收方与联系方式说明后，将公开配置 `ACCEPTING` 改成 `true` 部署；设回 `false` 可暂停上传，撤回继续可用。核对 Cron 已启用，以及管理域名所有入口均受保护。不要在 CI 中打印令牌或上传正文。

## 客户端配置

发布包将 `GPT_SoVITS/feedback/service.example.json` 复制为同目录 `service.json`，填写公开根地址、接收方和联系方式。也可用 `DSAKIKO_FEEDBACK_URL` 覆盖公开地址。地址必须为 HTTPS，不含登录信息、查询参数或片段。未配置时 UI 明确告知服务未就绪，不发送请求。公开地址不是秘密。

普通用户只需要公开地址。控制秘密通过 `keyring` 放入系统钥匙串 / Windows 凭据管理器 / Linux Secret Service 或 KWallet；无安全后端时在发送前停止，不降级到明文。回执 SQLite 在 `platformdirs` 的 `D_sakiko/feedback/receipts.sqlite3`，只含标题、随机请求编号、回执编号、状态、时间和原服务地址。正文不落盘。删除本机回执库或系统凭据会失去对应撤回入口；重装或换设备不会自动迁移。

开发者本机配置以下环境变量后，“更多功能”出现“管理反馈”：

```text
DSAKIKO_FEEDBACK_ADMIN_URL=https://feedback-admin.example.com
DSAKIKO_FEEDBACK_ACCESS_ID=<Access service token client id>
DSAKIKO_FEEDBACK_ACCESS_SECRET=<Access service token secret>
```

不要将这些开发者变量加入普通发布包、源码或普通配置导出。面板支持按评价/处理状态筛选、分页、纯文本详情、处理状态修改、删除、JSON 和文本对话备份导出。管理 API 还支持 `since` / `until` Unix 秒范围过滤。查看不需要安装反馈中的角色，不执行 HTML、模板、工具、资源链接；导出备份只有 `manifest.json`，Static 提示词不改写用户原聊天。用户本机历史列表不提供正文查看。

## 协议和一致性

- `POST /v1/feedback`，`Content-Type: application/json`，UTF-8 紧凑 JSON，实际读取上限 1 MiB。不接收 gzip 请求、ZIP、附件或音频；入库压缩使用 gzip BLOB。
- 约定头 `X-DSakiko-Feedback: v1:<uuid-v4>:<hex-sha256>`，摘要输入精确为 UTF-8 `d_sakiko.feedback/v1\n<uuid-v4>`。其中 `\n` 表示一个 LF 字符。算法公开，只过滤随机扫描。
- 缺失或不正确的约定头先假成功，不读取正文、不访问限流器或 D1。有效头之后可返回 `429` / `413`。格式不符假成功；合法但停用的 schema 和具有有效公共信封的未知版本返回 `400`。JSON schema 是客户端与服务端共用的文件，避免有效客户端被静默丢弃。
- `X-DSakiko-Control` 为客户端生成的 32 字节随机数的小写十六进制（64 字符）。服务端只保存 SHA-256 摘要。控制秘密用于重试和撤回，不允许读取内容。没有公开的“检查是否保存”接口。
- 每次逻辑提交使用新 UUID。重试必须带原秘密和完全相同的冻结 UTF-8 字节；服务端使用字节哈希，空白或字段顺序变化也视为正文改变。合法重试返回相同回执，不扣第二次成功额度。编号相同但正文改变返回 `409`，秘密不匹配返回 `403`。
- 首次发送前已保存最小回执和控制秘密。网络失败在当前弹窗内可主动重试；关闭弹窗或退出进程释放正文，之后只保留撤回能力，不自动重建上传副本。
- `DELETE /v1/feedback?request_id=<uuid>` 使用相同两个请求头。即使首次上传响应丢失，也无需服务器回执编号。未知编号的撤回创建 8 天无正文墓碑；有效请求最多创建/重试 7 天（容许 5 分钟客户端时钟超前），配合原子事务避免迟到上传复活。已有提交撤回清空正文和业务字段，保留控制信息至创建后第 97 天。
- 并发配额在 SQLite 触发器与 D1 原子 batch 中扣除；不同编号始终存独立正文，包括相同诊断。IP 限流仅为地点局部的粗保护，不能代替精确配额。
- 暂停、数据库故障和存储失败返回真实失败，不伪装成功。Cloudflare 在 Worker 外的边缘拦截不受假成功逻辑控制。

## 数据范围与保留

对话反馈上传全部普通消息的说话者、正文、翻译和情绪，以及反馈时渲染的 Static system prompt。当前世界书启用时上传该对话现存诊断，不重新检索。快照、检索候选、选中 ID、注入知识及工具返回知识均使用字段白名单；不透传任意 `payload` / 工具 `result` / 错误文本，检索失败仅保留允许的错误代码。聊天 `meta`、附件、音频路径、API 配置和普通日志不上传。文本本身不承诺自动去除个人信息。

每份云端正文在接收后 90 天到期，管理 API 立即停止提供到期内容；每小时 Cron 清空正文和业务字段，之后清理有限期控制记录与计数。撤回会立即清空当前 D1 正文。D1 平台的 Time Travel / 备份保留应按实际账号配置纳入运营说明；恢复数据库后须先重新应用撤回记录并运行过期清理，再开放管理读取和接收。

管理面板导出集中在本机 `D_sakiko/feedback/admin_exports/`，保留来源服务和到期时间索引。重新打开/刷新面板会清理过期或云端已撤回的本工具副本，面板删除也会清理对应本机文件。未运行的程序无法定时清理本机文件；手工复制、另行导入聊天、系统备份等副本由开发者按保留规则管理。不要把导出文本提交到 Git 或公开 Issue。

参考：[D1 batch 事务](https://developers.cloudflare.com/d1/worker-api/d1-database/)、[Rate Limiting API](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/)、[Access JWT 验证](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)。
