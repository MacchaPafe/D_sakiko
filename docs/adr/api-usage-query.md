# 桌面 API 额度查询

## 使用方式

1. 打开「设置 → 大模型 API」，选择自己的 API，在「额度查询」中选择模板。
2. 按需填写查询基址、专用 Key、Access Token、User ID 和换算比例。普通模板默认复用当前聊天密钥；OpenRouter 账户余额需要独立管理密钥，New API 账户额度需要 Access Token 和 User ID。
3. 「测试查询」仅运行表单草稿，不保存，不切换聊天 API。勾选启用并保存后，聊天窗口立即查询，默认每 5 分钟刷新；间隔 0 表示只手动查询。
4. 点击聊天输入栏的「额度」查看各币种明细、最后成功时间和错误信息，并手动刷新。额度与上下文 Token 圆环独立。

首次请求其他域名时会显示目标地址，用户确认后才发送查询凭据。作者共用 API 默认关闭，且不显示共用账户余额。切换 API 地址、密钥或查询配置后，旧账户查询不会覆盖新账户。

## 模板与结果语义

| 模板 | 查询接口 | 凭据与结果 |
| --- | --- | --- |
| DeepSeek | `/user/balance` | 普通 API Key；按返回币种分别显示总余额、充值余额和赠金 |
| SiliconFlow | `/v1/user/info` | 国内/国际官方域名；总可用余额 |
| OpenRouter 密钥额度 | `/api/v1/key` | 普通聊天 Key；限额、剩余和用量，无限额明确标注 |
| OpenRouter 账户余额 | `/api/v1/credits` | 专用管理 Key；累计充值减累计用量 |
| New API 账户额度 | `/api/user/self` | Access Token + New-Api-User；默认每 500000 配额换算为 1 USD，可调整 |
| 通用余额 | `/user/balance` | `balance` 和可选 `is_active` |
| 自定义 | 脚本的 `request.url` | CC Switch 同步对象脚本 |

官方模板匹配精确主机名，模型名称不参与选择。查询失败时保留同一配置与凭据的上次成功结果并标注旧数据；缺失值不自动补零，多币种不合并。OpenRouter 的「未设置密钥限额」不表示账户余额无限。

## CC Switch 兼容脚本

```javascript
({
  request: {
    url: "{{baseUrl}}/user/balance",
    method: "GET",
    headers: { Authorization: "Bearer {{apiKey}}" }
  },
  extractor: function (response) {
    if (response.balance == null) throw Error("余额字段缺失");
    return { remaining: Number(response.balance), unit: "USD", planName: "账户余额" };
  }
})
```

支持 `{{apiKey}}`、`{{baseUrl}}`、`{{accessToken}}`、`{{userId}}`；占位符应位于 JS 字符串内。`extractor(response)` 可返回对象或对象数组。兼容字段：`isValid`、`invalidMessage`、`remaining`、`used`、`total`、`unit`、`planName`、`extra`，金额字段使用有限数字或空值，文本使用字符串。请求支持 GET/POST、字符串请求体，不执行 Node.js、浏览器或异步登录流程。

## 运行边界与凭据保存

- `quickjs==1.19.4` 在独立隐藏子进程中运行；不注册文件、进程或 Python 调用。
- 每个 JS 执行阶段限制 5 秒、16 MiB 内存，宿主网络超时 2—30 秒（默认 10 秒），响应上限 1 MiB；父进程限制整体截止时间并负责终止与回收。
- HTTP 重定向不自动跟随，认证头不跨域转发。测试预览和错误均脱敏。
- 配置按服务商及规范化端点保存；凭据摘要和配置版本参与缓存隔离。专用密钥只保存系统凭据引用，凭据库未启用时仅存当前进程会话；独立设置程序退出后需重新输入这类会话凭据。
- 独立设置程序只执行草稿测试，不启动第二套轮询；主聊天进程负责当前 API 的自动刷新。

## 参考

- [CC Switch 查询界面](https://github.com/farion1231/cc-switch/blob/0d0dd0a5487dd72b1d0b21c648d411d2cd99396a/src/components/UsageScriptModal.tsx)
- [CC Switch 余额适配](https://github.com/farion1231/cc-switch/blob/0d0dd0a5487dd72b1d0b21c648d411d2cd99396a/src-tauri/src/services/balance.rs)
- [CC Switch 脚本执行机制](https://github.com/farion1231/cc-switch/blob/0d0dd0a5487dd72b1d0b21c648d411d2cd99396a/src-tauri/src/usage_script.rs)
- [OpenRouter 管理密钥账户额度接口](https://openrouter.ai/docs/api/api-reference/credits/get-credits)
