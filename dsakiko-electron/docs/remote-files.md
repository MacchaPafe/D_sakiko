# 远端文件后端

2026-10-26 更新：应用的对话请求当前只把图片作为模型附件发送。非图片进入对话 workspace，由文件工具读取或修改；底层 File API 适配能力仍保留。详见[内置工具与工作目录](backend-tools.md)。

实现日期：2026-10-25。只修改后端与共享契约，前端附件输入仍待接入。

## 本地附件与远端副本

导入附件先保存本地原文件并分配 AssetRef；草稿 ready 只代表本地导入完成。提交时先保存 Chat、建立资源持有关系，再开始上传与模型生成。上传或生成失败不回滚已保存的消息。Chat 及备份只保存本地附件引用，不保存厂商 ID、密钥或 SDK 对象。

RemoteFiles 管理应用数据目录下的 `remote-files/index.json`，按 `SHA256({ provider, baseUrl, protocol, apiKey, purpose }) + assetId` 复用文件。摘要采用有字段边界的稳定 JSON；地址遵循连接规范化规则，保留有意义的路径。连接名称和每次请求生成的 credentialScope 不参与身份。同地址同凭据同协议可以跨连接、跨模型复用，但每次使用仍检查当前输入限制。

实际连接尚不支持独立组织、项目或自定义鉴权头；以后引入此类会改变文件归属的参数时，必须同时扩充作用域摘要。当前不推断不同密钥是否属于同一账号，不合并不同协议作用域，也不对不同 AssetRef 的同内容文件做去重。

## 规则与自动选择

`remote-files/rules.json` 首次初始化从内置 JSON 创建；之后每次请求准备读取一次，不覆盖用户修改。格式错误会报告 `invalid_attachment_rules`。本次请求中的历史、新附件以及受理检查和正式生成共用规则快照；后续请求重新读取。

```json
{
  "version": 1,
  "rules": [
    {
      "match": { "provider": "deepseek" },
      "mediaTypes": ["image/jpeg", "image/png", "image/gif", "image/webp"],
      "transports": {
        "inline": { "maxBytes": 33554432 },
        "file-api": { "maxBytes": 67108864 }
      }
    }
  ]
}
```

`match` 可使用 provider、protocol、modelIds；省略的条件不限制匹配，空对象匹配所有连接。每条规则及各传送路径可设置 enabled、mediaTypes、maxBytes；MIME 支持精确类型及 `image/*` 形式。所有匹配规则取交集；空数组 mediaTypes 禁止全部类型。没有匹配规则就没有额外限制；文件类型、大小和处理方式仍受 SDK 与实际服务约束。

附件共同限制不能通过切换传送方式绕过。明确的 imageInput/fileInput unsupported 拒绝；unknown 或未声明允许尝试。fileUpload unsupported 只禁止上传路径。当前 mediaTypes 的精细限制统一以规则 JSON 为准，不从模型目录的模态标签自动生成完整 MIME 允许表。

连接支持 auto、inline、file-api，新连接省略时默认 auto，已有显式值保留。auto 每个附件分别决策：

1. 检查共同限制，计算两条路径是否可尝试。
2. 上传可尝试时优先走 RemoteFiles，先查找有效引用，否则上传。
3. SDK 无上传实现或配置禁止该路径时，若内联可尝试则内联。
4. 上传阶段只对明确的 SDK 上传功能不支持错误回退一次；普通 HTTP 错误、限流、鉴权和超时不回退。

上下文检查不上传、不读取完整文件内容，使用空字节占位并报告 token 预算 unknown。`attachmentDecisions` 给出资源 ID、选择路径及原因，不泄露远端 ID。SDK 缺乏通用的完整能力查询，上传成功不保证模型可直接读取文件；需要代码执行等工具的文件不自动开启工具或转换格式。

## 上传、失败与删除

上传复用 AI SDK 的 provider.files()/uploadFile 与 providerReference。DeepSeek、Anthropic、Google 及原生 OpenAI 的具体上传由安装的 SDK 决定；通用 openai-compatible 适配器没有上传实现时走内联。Google 上传处理状态轮询由 SDK 完成。第一版不会自行猜测兼容端点的上传协议。

同作用域同资源的并发上传共享一个任务，单个调用方取消只取消等待。任务本身有 5 分钟上限并持有资源，迟到成功仍登记；生成等待另受本轮 timeoutMs 限制。资源在实际 Agent 执行结束后释放，不能因为上层先结束等待就提前回收。

索引在上传前记录 uploading，成功后记录引用及厂商报告的过期时间；临近过期 30 秒内重新上传。上传错误保留 failed 意图。若远端已创建文件，但响应丢失或进程在登记前崩溃，可能只剩没有远端 ID 的记录；无法保证外部操作与本地落盘原子一致，也不扫描账号来猜测对应文件。此类记录保留供诊断。

第一版精确识别 DeepSeek 无效文件引用并调用 invalidate；多个副本且错误没有指出 ID 时不批量失效。下一次生成重新上传，不自动重复整轮工具执行。鉴权、限流错误不当作文件失效。

只有 DeepSeek 原生 Chat 协议接入删除。其他 provider 返回 unsupported，并保留记录；即使 SDK 另有可选删除实现，本版也不自动扩大删除范围。删除必须得到厂商确认，或明确的 file_not_found；普通 404 不算成功。失败保留 pending-delete，缺少当前可用的原作用域凭据则记录 credential_unavailable，不持久保存旧密钥。

删除 Chat 后异步回收、每 30 秒维护时再次检查。只清理已登记且无本地持有者的文件；草稿、其他对话、运行任务均会阻止自动删除。检查及自动删除与新资源引用接纳互斥；显式删除不受此保护，调用者应自行协调使用中的请求。清理只处理远端副本，不删除本地原文件，不回滚已删除的 Chat。

## 验证范围

`tests/core/remote-files.test.js` 使用临时本地资源、真实 AI SDK 序列化和替换的 fetch 验证上传、生成引用、规则快照、隔离、并发取消、过期、删除竞态及完整后端消息保存流程。没有调用真实厂商服务，不能据此宣称特定账号、代理、模型或文件类型已通过线上验收。
