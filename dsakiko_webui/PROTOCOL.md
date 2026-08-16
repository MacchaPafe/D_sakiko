# D_sakiko WebUI V1 前后端协议

> 状态：V1 实现基线
>
> 协议版本：`1`
>
> 基础路径：`/api/v1`
> 适用范围：独立运行的 `dsakiko_webui`，桌面 Qt 主程序不使用本协议

本文档是 WebUI 前后端协议的唯一正式来源。总设计文档只保留原则与链接；实现、Mock、测试 fixture 和联调录制都应以本文档为准。

## 1. 目标与边界

V1 协议负责：

- 初始化 WebUI 并恢复当前会话状态。
- 获取会话列表、可用角色和用户人设。
- 创建和切换会话。
- 发送用户消息、取消当前回复。
- 按段接收角色文本、翻译、情绪和音频。
- 获取 Live2D 模型、头像、背景和生成音频。
- 同步运行状态、当前背景和结构化错误。
- 在 WebSocket 断线后重新获取权威快照。

V1 暂不负责：

- 文本 token 级流式输出。
- 浏览器音频播放完成 ACK。
- 事件历史回放。
- 消息编辑、删除、回溯重发和历史音频重新合成。
- 小剧场模式。
- 多个 Headless Runtime 同时写聊天记录。

## 2. 核心原则

1. 前端与后端正式部署时同源。
2. WebSocket 是命令和可变状态的唯一权威通道。
3. HTTP 只负责健康检查、登录会话、静态文件和二进制资源。
4. 浏览器只消费领域事件，不接触 Python 队列名、绝对路径或 `LABEL_0` 等内部表示。
5. 服务端拥有 `current_chat_id`、消息记录和生成状态的最终决定权。
6. 所有 ID 都是不透明字符串，客户端不得从 ID 中解析角色、时间或文件路径。
7. 每一段角色回复都是独立 `Message`，不得把同一轮的多段回复合并成一个气泡。
8. V1 不回放遗漏事件。重连后客户端发送 `sync`，用快照恢复。
9. 单个 Headless Runtime 同一时间最多生成一轮回复。
10. 同一时间只有一个已认证控制端；访问码正确的新设备会接管控制权，旧会话立即失效。

## 3. HTTP 与 WebSocket 接口

| 方法 | 路径 | 鉴权 | 用途 |
|---|---|---:|---|
| `GET` | `/api/v1/health` | 否 | 检查 HTTP 服务和 Runtime 状态，不触发初始化 |
| `POST` | `/api/v1/session` | 访问码 | 建立浏览器登录会话并写入 HttpOnly Cookie |
| `DELETE` | `/api/v1/session` | 是 | 注销浏览器登录会话 |
| `WS` | `/api/v1/ws` | 是 | 发送命令并接收状态、消息和错误事件 |
| `GET` | `/api/v1/media/{media_id}` | 是 | 获取头像、背景或生成音频等受控资源 |
| `GET` | `/api/v1/live2d/{model_id}/{asset_path:path}` | 是 | 获取已登记 Live2D 模型目录中的资源 |
| `GET` | `/` 及前端静态路径 | 否 | 提供打包后的 React 页面 |

开发环境由 Vite 代理 `/api` 和 WebSocket，浏览器仍只访问一个页面来源。正式协议中禁止出现 Vite 的 `/@fs/` 路径。

## 4. 通用约定

### 4.1 JSON、命名与空值

- JSON 使用 UTF-8。
- 字段使用 `snake_case`。
- 未知字段必须被接收方忽略，以便 V1 后续增加非破坏性字段。
- 固定实体中的可选值使用 `null`，不使用空字符串冒充缺失值。
- 命令不需要的可选输入可以省略。
- 文本允许 Unicode；服务端必须在日志中避免输出访问码、Cookie 和完整私密聊天内容。

### 4.2 时间与时长

- `timestamp`、`created_at`、`last_active_at`、`server_time` 均为 UTC Unix 秒整数。
- `audio_duration_ms` 使用毫秒整数。
- 展示时区由浏览器决定。

### 4.3 ID

下列字段都是非空、不透明字符串：

- `request_id`：一次客户端命令的关联 ID，在当前 WebSocket 连接内唯一。
- `event_id`：一次服务端事件的全局唯一 ID。
- `session_id`：一次后端进程生命周期的 ID，后端重启后改变。
- `client_message_id`：一次逻辑用户消息的幂等 ID。
- `chat_id`、`turn_id`、`message.id`、`character.id`、`media_id`、`model_id`。

客户端重发同一条逻辑用户消息时，可以创建新的 `request_id`，但必须复用原 `client_message_id`。

### 4.4 情绪

角色消息的 `emotion` 只能是以下七种之一：

```text
happiness
sadness
anger
disgust
like
surprise
fear
```

规则：

- 角色消息必须提供合法 `emotion`。
- 用户消息的 `emotion` 为 `null`。
- 后端将 `LABEL_0` 等内部标签转换为上述名称后才能发送。
- 旧消息没有情绪或出现未知情绪时，后端统一降级为 `happiness`。
- V1 不定义 `neutral`。现有 Mock 中的 `neutral` 在接入正式协议时需要改为合法值。

### 4.5 生成阶段

```text
idle
thinking
tts
```

- `idle`：当前没有生成任务。
- `thinking`：正在执行 LLM 或 Agent。
- `tts`：LLM 已产生结构化段落，正在生成或整理语音。
- 主程序内部的 `llm` 阶段由 HeadlessRuntime 映射为协议层 `thinking`。

### 4.6 轮次完成状态

```text
success
cancelled
error
```

`assistant_turn_complete` 只表示服务端不再为该轮生成内容，不表示手机已经播放完所有音频。

## 5. 领域实体

本节示例展示实体的完整 V1 字段。除命令输入中明确标为可省略的字段外，实现应保持这些字段稳定。

### 5.1 Character

```json
{
  "id": "anon",
  "name": "爱音",
  "avatar_url": "/api/v1/media/media_avatar_anon",
  "model_url": "/api/v1/live2d/model_anon/3.model.json",
  "accent": "#168779",
  "accent_soft": "#DCEFEC"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `string` | 可用于 `create_chat.character_id` 的稳定角色 ID |
| `name` | `string` | 展示名称 |
| `avatar_url` | `string \| null` | 同源头像 URL |
| `model_url` | `string \| null` | 完整 Live2D 模型 JSON URL；无模型展示时为 `null` |
| `accent` | `string` | `#RRGGBB` 主题色 |
| `accent_soft` | `string` | `#RRGGBB` 浅主题色 |

### 5.2 UserPersona

```json
{
  "id": "persona_default",
  "name": "默认身份"
}
```

V1 只向浏览器暴露选择所需的 `id` 和 `name`。完整人设内容留在服务端。

### 5.3 ChatSummary

```json
{
  "chat_id": "chat_anon_daily",
  "name": "放学后的闲聊",
  "character": {
    "id": "anon",
    "name": "爱音",
    "avatar_url": "/api/v1/media/media_avatar_anon",
    "model_url": "/api/v1/live2d/model_anon/3.model.json",
    "accent": "#168779",
    "accent_soft": "#DCEFEC"
  },
  "user_persona": {
    "id": "persona_default",
    "name": "默认身份"
  },
  "last_message_preview": "今天状态相当不错哦。",
  "last_active_at": 1784851200,
  "status": "idle"
}
```

`status` 使用 `idle | thinking | tts`。由于 V1 全局只能生成一轮，最多一条会话处于非 `idle` 状态。

### 5.4 Message

```json
{
  "id": "msg_assistant_0002",
  "role": "assistant",
  "text": "今日はかなりいい感じだったよ。",
  "translation": "今天状态相当不错哦。",
  "created_at": 1784851200,
  "turn_id": "turn_0001",
  "client_message_id": null,
  "sequence": 0,
  "emotion": "happiness",
  "audio_url": "/api/v1/media/media_audio_0002",
  "audio_duration_ms": 2840,
  "status": "ready"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `string` | 服务端消息 ID，在消息生命周期内稳定 |
| `role` | `"user" \| "assistant"` | 消息角色 |
| `text` | `string` | 原文 |
| `translation` | `string` | 无翻译时为 `""` |
| `created_at` | `integer` | UTC Unix 秒 |
| `turn_id` | `string \| null` | 所属生成轮次；无法迁移的旧历史可为 `null` |
| `client_message_id` | `string \| null` | 用户消息的幂等 ID；角色消息为 `null` |
| `sequence` | `integer` | 角色分段从 `0` 递增；用户消息为 `0` |
| `emotion` | `Emotion \| null` | 角色消息必填；用户消息为 `null` |
| `audio_url` | `string \| null` | 无音频、语音关闭或 TTS 失败时为 `null` |
| `audio_duration_ms` | `integer \| null` | 未知或无音频时为 `null` |
| `status` | `"ready"` | V1 只传输已经可以展示的消息 |

同一轮角色回复的每个分段都有独立 `id` 和 `sequence`，前端据此生成多个气泡。

### 5.5 Background

```json
{
  "id": "school",
  "name": "校舍",
  "image_url": "/api/v1/media/media_background_school",
  "color": "#CFD9DC"
}
```

`image_url` 可以为 `null`。加载图片失败或没有图片时，前端使用 `color` 作为稳定回退。

### 5.6 Error

```json
{
  "code": "CHAT_BUSY",
  "message": "当前回复完成后才能切换会话。",
  "retryable": true,
  "details": {
    "active_chat_id": "chat_anon_daily",
    "active_turn_id": "turn_0001"
  }
}
```

- `code` 供程序判断。
- `message` 是可直接展示的中文短句。
- `retryable` 表示在外部状态变化后重试是否可能成功，不表示前端应自动重试。
- `details` 仅放结构化调试信息，不放 Python traceback 和绝对路径。

### 5.7 StateSnapshot

`state_snapshot.data` 的完整结构：

```json
{
  "current_chat_id": "chat_anon_daily",
  "chat_name": "放学后的闲聊",
  "character": {
    "id": "anon",
    "name": "爱音",
    "avatar_url": "/api/v1/media/media_avatar_anon",
    "model_url": "/api/v1/live2d/model_anon/3.model.json",
    "accent": "#168779",
    "accent_soft": "#DCEFEC"
  },
  "user_persona": {
    "id": "persona_default",
    "name": "默认身份"
  },
  "messages": [],
  "phase": "idle",
  "turn_id": null,
  "background": {
    "id": "school",
    "name": "校舍",
    "image_url": "/api/v1/media/media_background_school",
    "color": "#CFD9DC"
  },
  "backgrounds": []
}
```

`messages` 是当前会话的完整 V1 消息数组。生成中重连时，`phase` 和 `turn_id` 必须反映仍在运行的轮次，已经完成的角色分段必须出现在 `messages` 中。

### 5.8 ChatListSnapshot

`chat_list_snapshot.data` 的完整结构：

```json
{
  "current_chat_id": "chat_anon_daily",
  "chats": [],
  "characters": [],
  "user_personas": [
    {
      "id": "persona_default",
      "name": "默认身份"
    }
  ]
}
```

- `chats` 是 `ChatSummary[]`，按 `last_active_at` 降序。
- `characters` 是 `Character[]`，用于新建会话。
- `user_personas` 是 `UserPersona[]`。没有独立人设功能时，服务端至少提供一个默认身份。

## 6. HTTP 详细协议

### 6.1 `GET /api/v1/health`

该接口不能触发 LLM、TTS 或 Live2D 模型初始化。

成功响应：

```http
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store
```

```json
{
  "status": "ok",
  "protocol_version": 1,
  "runtime_status": "starting",
  "session_id": "session_01J2XYZ",
  "auth_required": true,
  "authenticated": false,
  "server_time": 1784851200
}
```

`runtime_status` 为 `starting | ready | error | stopping`。HTTP 服务可用但 Runtime 初始化失败时仍返回 `200`，并将 `runtime_status` 设为 `error`，以便前端展示明确状态。

### 6.2 `POST /api/v1/session`

请求：

```json
{
  "access_code": "用户从电脑端启动日志或二维码中获得的访问码",
  "session_id": "浏览器可选提供的稳定设备会话标识"
}
```

成功响应：

```http
HTTP/1.1 200 OK
Set-Cookie: dsakiko_session=opaque_value; HttpOnly; SameSite=Strict; Path=/
Content-Type: application/json
Cache-Control: no-store
```

```json
{
  "authenticated": true
}
```

规则：

- 发布版本绑定 `0.0.0.0` 时必须启用鉴权。
- 开发模式可以显式关闭鉴权，但默认只应绑定 `127.0.0.1`。
- 使用 HTTPS 时 Cookie 必须增加 `Secure`。
- 访问码只用于换取会话 Cookie，不放入 WebSocket URL、媒体 URL 或 Live2D URL。
- 登录成功会生成新的不透明 Cookie，并原子替换当前控制端；任何知道访问码的新设备都可以接管。
- 接管后旧 Cookie 访问 HTTP 资源会得到 `401`，旧 WebSocket 以 `4409` 关闭。

### 6.3 `DELETE /api/v1/session`

清除登录 Cookie，成功返回 `204 No Content`。已建立的 WebSocket 应由服务端以 `4401` 关闭。

### 6.4 `GET /api/v1/media/{media_id}`

要求：

- `media_id` 只能映射服务端登记的文件，不能直接解释为本地路径。
- 支持 `Range` 请求和 `206 Partial Content`，保证浏览器可播放和跳转音频。
- 返回正确的 `Content-Type`、`Content-Length`、`Accept-Ranges: bytes` 和 `ETag`。
- 同一个仍存在的历史音频在服务重启后应继续得到可用 URL。
- 文件不存在时返回 `404` 和统一 Error JSON。
- 禁止通过错误信息暴露绝对路径。

完整音频响应示例：

```http
HTTP/1.1 200 OK
Content-Type: audio/wav
Accept-Ranges: bytes
ETag: "media-audio-0002-v1"
Cache-Control: private, max-age=3600
```

### 6.5 `GET /api/v1/live2d/{model_id}/{asset_path:path}`

要求：

- `model_id` 映射配置中已登记的单个模型根目录。
- `asset_path` 必须在 URL 解码和路径规范化后仍位于该模型根目录内。
- 拒绝 `..`、绝对路径、符号链接越界和双重编码绕过。
- 返回正确 MIME、`ETag` 和合理缓存头。
- `model_url` 由服务端完整返回，例如 `/api/v1/live2d/model_anon/3.model.json`。
- 模型 JSON 内的相对纹理、动作和物理文件路径会自然落到同一个模型路由。

### 6.6 HTTP 错误

HTTP 错误使用与 WebSocket 相同的 Error 实体：

```json
{
  "error": {
    "code": "MEDIA_NOT_FOUND",
    "message": "请求的媒体文件不存在。",
    "retryable": false,
    "details": {}
  }
}
```

## 7. WebSocket 信封

### 7.1 客户端命令

```json
{
  "protocol_version": 1,
  "kind": "command",
  "type": "send_message",
  "request_id": "req_01J2XYZ",
  "payload": {}
}
```

约束：

- 每条命令都必须包含上述五个字段。
- `request_id` 在当前连接内唯一。
- 单条文本帧最大 `64 KiB`。
- 二进制 WebSocket 帧不属于 V1。
- `protocol_version` 不受支持但仍能读取 `request_id` 时，先返回 `UNSUPPORTED_PROTOCOL`，再以 `4406` 关闭连接。
- Runtime 进入 `ready` 前只允许 `ping`；其他业务命令返回 `RUNTIME_NOT_READY`。

### 7.2 命令成功响应

```json
{
  "protocol_version": 1,
  "kind": "response",
  "type": "command_result",
  "request_id": "req_01J2XYZ",
  "ok": true,
  "data": {},
  "error": null
}
```

### 7.3 命令失败响应

```json
{
  "protocol_version": 1,
  "kind": "response",
  "type": "command_result",
  "request_id": "req_01J2XYZ",
  "ok": false,
  "data": null,
  "error": {
    "code": "CHAT_BUSY",
    "message": "当前回复完成后才能切换会话。",
    "retryable": true,
    "details": {}
  }
}
```

每条可解析出 `request_id` 的命令必须得到且只得到一个 `command_result`。成功结果表示命令已被服务端接受并完成同步校验，不表示异步 LLM/TTS 已完成。

### 7.4 服务端事件

```json
{
  "protocol_version": 1,
  "kind": "event",
  "type": "assistant_segment_ready",
  "event_id": "evt_01J2XYZ",
  "session_id": "session_01J2XYZ",
  "sequence": 42,
  "timestamp": 1784851200,
  "request_id": "req_01J2XYZ",
  "chat_id": "chat_anon_daily",
  "turn_id": "turn_0001",
  "data": {}
}
```

规则：

- `sequence` 在当前 WebSocket 连接内从 `1` 开始严格递增，重连后重新从 `1` 开始。
- `session_id` 标识后端进程生命周期，不是 `sequence` 的命名空间。
- 改变共享 Runtime 状态的事件广播给当前所有已认证连接。
- 仅用于建立或恢复某条连接的 `runtime_status`、`runtime_ready` 和 `sync` 快照可以只发给目标连接。
- 没有关联命令时 `request_id` 为 `null`。
- 没有关联会话或轮次时 `chat_id`、`turn_id` 为 `null`。
- 前端按 `event_id` 去重，按 `sequence` 判断先后，不按到达时间或 `timestamp` 排序。
- 后端重启后 `session_id` 改变。

## 8. 客户端命令

### 8.1 `sync`

用途：连接或重连后获取所有页面恢复所需的快照。

请求：

```json
{
  "protocol_version": 1,
  "kind": "command",
  "type": "sync",
  "request_id": "req_sync_001",
  "payload": {}
}
```

成功结果：

```json
{
  "protocol_version": 1,
  "kind": "response",
  "type": "command_result",
  "request_id": "req_sync_001",
  "ok": true,
  "data": {
    "accepted": true
  },
  "error": null
}
```

随后服务端依次发送：

1. `chat_list_snapshot`
2. `state_snapshot`

如果当前还没有会话，`chat_list_snapshot.chats` 为空，且不发送 `state_snapshot`。前端停留在会话主页。

### 8.2 `get_chat_list`

请求：

```json
{
  "protocol_version": 1,
  "kind": "command",
  "type": "get_chat_list",
  "request_id": "req_list_001",
  "payload": {}
}
```

成功 `command_result` 后发送一条 `chat_list_snapshot`。该命令在生成期间也允许执行。

### 8.3 `send_message`

请求：

```json
{
  "protocol_version": 1,
  "kind": "command",
  "type": "send_message",
  "request_id": "req_send_001",
  "payload": {
    "chat_id": "chat_anon_daily",
    "client_message_id": "client_msg_01J2XYZ",
    "text": "今天练习怎么样？"
  }
}
```

成功结果：

```json
{
  "protocol_version": 1,
  "kind": "response",
  "type": "command_result",
  "request_id": "req_send_001",
  "ok": true,
  "data": {
    "chat_id": "chat_anon_daily",
    "turn_id": "turn_0001",
    "client_message_id": "client_msg_01J2XYZ",
    "deduplicated": false
  },
  "error": null
}
```

规则：

- `chat_id` 必须等于服务端当前 `current_chat_id`。
- 去除首尾空白后的 `text` 长度必须为 `1..10000` 个 Unicode 字符。
- 服务端保存去除首尾空白后的文本。
- 当前 `phase` 必须为 `idle`。
- 成功响应后进入标准消息事件流。
- 同一 `chat_id + client_message_id` 已经接受过时，不新增消息、不新建第二轮，返回原 `turn_id` 且 `deduplicated=true`。
- 返回 `deduplicated=true` 后再发送当前 `state_snapshot`，让未收到原 ACK 的客户端完成对账；不重复发送原轮次事件。
- 幂等映射必须至少覆盖当前服务进程；推荐随用户消息一起持久化，以正确处理进程重启边缘情况。

### 8.4 `cancel_turn`

请求：

```json
{
  "protocol_version": 1,
  "kind": "command",
  "type": "cancel_turn",
  "request_id": "req_cancel_001",
  "payload": {
    "chat_id": "chat_anon_daily",
    "turn_id": "turn_0001"
  }
}
```

成功结果的 `data`：

```json
{
  "chat_id": "chat_anon_daily",
  "turn_id": "turn_0001",
  "cancellation_requested": true
}
```

规则：

- 必须同时校验 `chat_id` 和 `turn_id`。
- 取消是协作式操作，成功响应不保证任务已经退出。
- 任务实际停止后发送 `assistant_turn_complete(status=cancelled)`。
- 已经发出的 `assistant_segment_ready` 仍是正式消息，不撤回。

### 8.5 `switch_chat`

请求：

```json
{
  "protocol_version": 1,
  "kind": "command",
  "type": "switch_chat",
  "request_id": "req_switch_001",
  "payload": {
    "chat_id": "chat_arisa_study"
  }
}
```

成功结果的 `data`：

```json
{
  "current_chat_id": "chat_arisa_study"
}
```

随后依次发送：

1. `chat_list_snapshot`
2. 目标会话的 `state_snapshot`

规则：

- 生成期间返回 `CHAT_BUSY`。
- 切换到当前会话也返回成功并重新发送快照。
- 前端收到目标 `state_snapshot` 后才能提交页面上的 `current_chat_id`。

### 8.6 `create_chat`

请求：

```json
{
  "protocol_version": 1,
  "kind": "command",
  "type": "create_chat",
  "request_id": "req_create_001",
  "payload": {
    "character_id": "arisa",
    "name": "概念学习",
    "user_persona_id": "persona_default"
  }
}
```

输入规则：

- `character_id` 必填。
- `name` 可省略或为 `null`；服务端使用角色名生成默认名称。
- `user_persona_id` 可省略或为 `null`；服务端使用默认身份。
- 非空名称去除首尾空白后最多 `80` 个 Unicode 字符。
- 生成期间返回 `CHAT_BUSY`。

成功结果的 `data`：

```json
{
  "chat_id": "chat_arisa_new",
  "current_chat_id": "chat_arisa_new"
}
```

服务端创建会话并将其设为当前会话，随后依次发送：

1. `chat_list_snapshot`
2. 新会话的 `state_snapshot`

### 8.7 `next_background`

请求：

```json
{
  "protocol_version": 1,
  "kind": "command",
  "type": "next_background",
  "request_id": "req_background_001",
  "payload": {}
}
```

成功响应后发送 `background_changed`。该命令在生成期间允许执行。

### 8.8 `ping`

请求：

```json
{
  "protocol_version": 1,
  "kind": "command",
  "type": "ping",
  "request_id": "req_ping_001",
  "payload": {
    "client_time": 1784851199
  }
}
```

成功响应后发送 `pong`。`client_time` 可省略，服务端原样返回，不参与业务逻辑。

## 9. 服务端事件

### 9.1 `runtime_status`

连接建立后服务端立即发送一次，初始化阶段变化时继续发送。

```json
{
  "state": "starting",
  "stage": "loading_tts",
  "message": "正在加载语音模型。",
  "progress": 0.6
}
```

- `state`：`starting | ready | error | stopping`
- `stage`：稳定机器字段，例如 `loading_config`、`loading_llm`、`loading_tts`
- `message`：可展示中文
- `progress`：`0.0..1.0` 或 `null`

### 9.2 `runtime_ready`

Runtime 可接受业务命令时发送。已完成初始化的服务在新连接建立后立即发送。

```json
{
  "mode": "web",
  "capabilities": {
    "tts": true,
    "translation": true,
    "backgrounds": true,
    "cancel_turn": true
  }
}
```

前端收到该事件后发送 `sync`。`runtime_ready` 本身不携带页面快照。

### 9.3 `chat_list_snapshot`

事件 `data` 使用第 5.8 节 `ChatListSnapshot`。客户端收到后整体替换本地会话摘要、角色选项和用户人设选项，不做数组增量合并。

### 9.4 `state_snapshot`

事件 `data` 使用第 5.7 节 `StateSnapshot`。它是当前会话的权威状态：

- 首次进入、切换、新建和重连都使用同一结构。
- 客户端整体替换当前会话消息数组。
- 切换会话时，只有目标 `current_chat_id` 的快照才是页面切换提交点。

### 9.5 `user_message_ack`

```json
{
  "message": {
    "id": "msg_user_0001",
    "role": "user",
    "text": "今天练习怎么样？",
    "translation": "",
    "created_at": 1784851200,
    "turn_id": "turn_0001",
    "client_message_id": "client_msg_01J2XYZ",
    "sequence": 0,
    "emotion": null,
    "audio_url": null,
    "audio_duration_ms": null,
    "status": "ready"
  }
}
```

该事件表示用户消息已正式写入当前聊天状态。前端按 `message.id` 去重。

### 9.6 `assistant_turn_phase`

```json
{
  "phase": "thinking"
}
```

标准轮次最多发送：

1. `thinking`
2. `tts`

进入 `idle` 不再发送单独 phase 事件，而由 `assistant_turn_complete` 表达终态。若 LLM 在产生任何合法段落前失败，可以只有 `thinking`。

### 9.7 `assistant_segment_ready`

```json
{
  "message": {
    "id": "msg_assistant_0002",
    "role": "assistant",
    "text": "うん、ちゃんと聞いてるよ。",
    "translation": "嗯，我有认真听。",
    "created_at": 1784851202,
    "turn_id": "turn_0001",
    "client_message_id": null,
    "sequence": 0,
    "emotion": "happiness",
    "audio_url": "/api/v1/media/media_audio_0002",
    "audio_duration_ms": 2840,
    "status": "ready"
  }
}
```

规则：

- 一条事件只包含一个段落。
- 同一 `turn_id` 的 `message.sequence` 从 `0` 连续递增。
- 文本已经可以展示，音频存在时已经可以请求和播放。
- `audio_url=null` 时仍必须展示文本，不等待音频。
- 前端根据 `emotion` 选择动作组，根据音频驱动口型。
- 前端按 `message.id` 去重，不按文本内容去重。

### 9.8 `assistant_turn_complete`

成功：

```json
{
  "status": "success",
  "segment_count": 3,
  "error": null
}
```

失败：

```json
{
  "status": "error",
  "segment_count": 1,
  "error": {
    "code": "TTS_FAILED",
    "message": "部分语音生成失败，已保留文本回复。",
    "retryable": true,
    "details": {}
  }
}
```

取消：

```json
{
  "status": "cancelled",
  "segment_count": 1,
  "error": null
}
```

收到该事件后，前端将对应活动轮次设为 `idle`，但继续播放已经进入本地音频队列的片段。

### 9.9 `background_changed`

```json
{
  "background": {
    "id": "school",
    "name": "校舍",
    "image_url": "/api/v1/media/media_background_school",
    "color": "#CFD9DC"
  },
  "backgrounds": []
}
```

`backgrounds` 是当前完整可用背景数组。客户端整体替换背景列表。

### 9.10 `error`

用于命令已经成功接受后出现的异步错误，或没有对应命令的 Runtime 错误。

```json
{
  "error": {
    "code": "LLM_FAILED",
    "message": "角色回复生成失败，请稍后重试。",
    "retryable": true,
    "details": {}
  }
}
```

LLM/TTS 导致当前轮失败时，服务端发送顺序为：

1. `error`
2. `assistant_turn_complete(status=error)`

命令的即时校验失败只返回 `command_result(ok=false)`，不再额外发送 `error` 事件。

### 9.11 `pong`

```json
{
  "client_time": 1784851199,
  "server_time": 1784851200
}
```

## 10. 标准时序

### 10.1 首次连接

```text
WebSocket connect
<- runtime_status
<- runtime_ready
-> sync
<- command_result(ok=true)
<- chat_list_snapshot
<- state_snapshot（存在当前会话时）
```

### 10.2 发送消息并正常完成

```text
-> send_message
<- command_result(ok=true, turn_id)
<- user_message_ack
<- assistant_turn_phase(thinking)
<- chat_list_snapshot（可选，用于刷新列表中的运行状态）
<- assistant_turn_phase(tts)
<- assistant_segment_ready(sequence=0)
<- assistant_segment_ready(sequence=1)
<- ...
<- assistant_turn_complete(status=success)
<- chat_list_snapshot
```

服务端可以在分段写入后额外发送 `chat_list_snapshot` 来刷新最近消息预览。客户端不得依赖这些可选快照的数量。

### 10.3 取消

```text
-> cancel_turn(chat_id, turn_id)
<- command_result(ok=true)
<- assistant_turn_complete(status=cancelled)
<- chat_list_snapshot
```

若底层调用无法立刻中止，取消完成事件可以延后，但服务端不得继续发布取消生效后才产生的新分段。

### 10.4 切换会话

```text
-> switch_chat(chat_id)
<- command_result(ok=true)
<- chat_list_snapshot
<- state_snapshot(target chat_id)
```

前端可以在等待期间显示加载状态，但不得先行替换当前会话消息。

### 10.5 异步失败

```text
-> send_message
<- command_result(ok=true)
<- user_message_ack
<- assistant_turn_phase(thinking)
<- error
<- assistant_turn_complete(status=error)
<- chat_list_snapshot
```

已经成功发布的分段继续保留在聊天记录中。

## 11. 状态机与命令准入

```text
idle
  | send_message
  v
thinking
  | LLM produced valid segments
  v
tts
  | success / cancel / error
  v
idle
```

| 命令 | `idle` | `thinking` / `tts` |
|---|---:|---:|
| `sync` | 允许 | 允许 |
| `get_chat_list` | 允许 | 允许 |
| `send_message` | 允许 | `CHAT_BUSY` |
| `cancel_turn` | `TURN_NOT_FOUND` | 校验 chat/turn 后允许 |
| `switch_chat` | 允许 | `CHAT_BUSY` |
| `create_chat` | 允许 | `CHAT_BUSY` |
| `next_background` | 允许 | 允许 |
| `ping` | 允许 | 允许 |

服务端必须串行处理会改变 Runtime 状态的命令。多个浏览器同时发送命令时，以服务端实际接收并校验的顺序为准。

## 12. 顺序、幂等与断线恢复

### 12.1 事件顺序

- WebSocket 单连接内按服务端发送顺序消费。
- `sequence` 只保证当前 WebSocket 连接上的事件严格递增，不提供历史回放。
- 收到重复 `event_id` 时忽略重复事件。
- 收到当前连接内小于等于已处理 `sequence` 的未知事件时忽略并记录警告。
- WebSocket 重连时客户端清空旧 sequence 基线；`session_id` 改变还表示服务端已经重启。

### 12.2 消息幂等

- `request_id` 解决一次连接内的命令关联。
- `client_message_id` 解决用户消息跨重试、跨重连的幂等。
- `message.id` 解决消息事件与快照重复到达时的前端去重。
- `turn_id + message.sequence` 只用于顺序检查，不替代 `message.id`。

### 12.3 断线恢复

1. 前端停止发送新命令并显示离线状态。
2. 本地输入草稿继续按 `chat_id` 保留。
3. 使用指数退避重新连接。
4. 收到 `runtime_ready` 后发送 `sync`。
5. 整体应用 `chat_list_snapshot` 和 `state_snapshot`。
6. 对未收到结果的 `send_message`，先在快照中查找相同 `client_message_id`。
7. 已存在则视为成功，不再重发；不存在时才使用原 `client_message_id` 重发。

V1 不自动重放其他未确认命令。切换背景、切换会话等操作由用户或页面状态重新触发。

快照恢复时，前端不得自动播放全部历史音频。只对本次页面生命周期内新观察到、且尚未入队的 `message.id` 自动播放。

## 13. 错误码

| 错误码 | 场景 | 可重试 |
|---|---|---:|
| `INVALID_ENVELOPE` | JSON 信封缺字段或字段类型错误 | 否 |
| `INVALID_COMMAND` | 未知命令或 payload 不合法 | 否 |
| `UNSUPPORTED_PROTOCOL` | `protocol_version` 不受支持 | 否 |
| `AUTH_REQUIRED` | 未登录或会话已失效 | 是 |
| `ORIGIN_NOT_ALLOWED` | WebSocket Origin 不允许 | 否 |
| `CHAT_NOT_FOUND` | 会话不存在 | 否 |
| `CHAT_MISMATCH` | 命令 chat 与当前 chat 不一致 | 是 |
| `CHAT_BUSY` | 生成期间发送、切换或创建会话 | 是 |
| `TURN_NOT_FOUND` | 当前没有可取消轮次 | 否 |
| `TURN_MISMATCH` | `chat_id` 或 `turn_id` 与活动轮次不一致 | 是 |
| `CHARACTER_NOT_FOUND` | 新建会话的角色不存在 | 否 |
| `USER_PERSONA_NOT_FOUND` | 用户人设不存在 | 否 |
| `INVALID_MESSAGE` | 消息为空、过长或格式错误 | 否 |
| `RUNTIME_NOT_READY` | Runtime 尚未初始化完成 | 是 |
| `LLM_FAILED` | LLM 或 Agent 调用失败 | 是 |
| `TTS_FAILED` | GPT-SoVITS 生成失败 | 是 |
| `MEDIA_NOT_FOUND` | 媒体不存在 | 否 |
| `MODEL_NOT_FOUND` | 模型或模型资源不存在 | 否 |
| `INTERNAL_ERROR` | 未分类服务端错误 | 视情况 |

服务端日志可以记录 traceback，但发送给浏览器的错误不得包含 API Key、Cookie、访问码、系统提示词或绝对路径。

## 14. WebSocket 关闭码

| 关闭码 | 含义 | 客户端行为 |
|---:|---|---|
| `1000` | 正常关闭 | 不自动重连，除非页面仍需连接 |
| `1001` | 服务端退出 | 延迟重连 |
| `1008` | 持续发送非法帧等通用策略违规 | 停止自动重试并提示 |
| `1009` | 帧超过大小限制 | 停止自动重试并提示 |
| `1011` | 未处理的内部错误 | 退避重连 |
| `1012` | 服务正在重启 | 快速退避后重连 |
| `4401` | 未认证或会话失效 | 回到登录页 |
| `4403` | Origin 被拒绝 | 停止重连 |
| `4406` | 协议版本不支持 | 提示前后端版本不匹配 |
| `4409` | 控制权已被新设备或新页面接管 | 回到登录页或提示已被接管 |
| `4429` | 连接数或速率超过限制 | 延迟后重试 |
| `4500` | Runtime 启动失败且无法继续 | 展示启动错误 |

单个命令格式错误优先返回结构化失败响应，不应立即关闭连接。只有无法可靠解析、持续违规或协议根本不兼容时才关闭。

## 15. 安全要求

- 发布版本绑定 `0.0.0.0` 时默认启用访问码和会话 Cookie。
- 后端只保留一个活动会话 Cookie和一个控制 WebSocket；新登录会替换旧会话。
- 校验 `Host` 与 WebSocket `Origin`，默认只允许当前服务来源。
- 不启用宽泛 CORS；Vite 开发环境使用代理。
- 所有资源 URL 都由服务端生成，前端不提交本地绝对路径。
- Live2D 路由执行路径穿越和符号链接越界检查。
- 媒体路由只读取登记文件，禁止任意文件下载。
- WebSocket 限制帧大小、命令频率和同时连接数。
- Cookie 使用 `HttpOnly; SameSite=Strict`；HTTPS 下增加 `Secure`。
- API Key、模型供应商凭证和系统提示词永远不进入协议。

## 16. 建议后端文件边界

```text
dsakiko_webui/backend/
├── main.py                         # 最薄启动入口：uvicorn + create_app
├── app.py                          # FastAPI 生命周期、Runtime 启停、路由注册
├── auth.py                         # 访问码、会话 Cookie、Origin 校验
├── ws.py                           # WebSocket 连接、命令分发、广播和 sequence
├── media_routes.py                 # media_id 注册、Range 与 MIME
├── live2d_routes.py                # model_id 注册和安全资源读取
├── protocol/
│   ├── __init__.py
│   ├── models.py                   # Pydantic 信封、实体、命令和事件模型
│   └── errors.py                   # 错误码与 Error 构造
└── runtime/
    ├── __init__.py
    └── headless_runtime.py         # 复用主程序能力并发布领域事件
```

职责约束：

- `protocol/models.py` 是 Python 侧协议结构唯一来源。
- `ws.py` 不直接操作 LLM、TTS 或聊天文件，只调用 HeadlessRuntime。
- HeadlessRuntime 不返回 FastAPI/WebSocket 对象，只接收领域命令并发布领域事件。
- `media_routes.py` 与 `live2d_routes.py` 不接受任意磁盘路径。
- `main.py` 不复制 `main2.py` 的业务循环。

前端真实接入时建议增加：

```text
dsakiko_webui/frontend/src/runtime/
├── protocol.js                     # 字段校验、枚举与开发期断言
└── webSocketRuntimeClient.js       # 连接、request_id、重连和 sync
```

`MockRuntimeClient` 与 `WebSocketRuntimeClient` 必须向 reducer 发出相同结构的正式事件。

## 17. 最小实现顺序

1. 实现 `health`，确认启动 FastAPI 不会隐式加载模型。
2. 实现 WebSocket 信封解析、`ping/pong` 和统一 `command_result`。
3. 实现 `runtime_status`、`runtime_ready`。
4. 用固定内存数据实现 `sync + chat_list_snapshot + state_snapshot`。
5. 实现 `switch_chat` 和 `create_chat`，先不接真实聊天文件。
6. 用定时器实现固定的 `send_message` 事件流，验证多段回复产生多个气泡。
7. 实现 `cancel_turn` 与 `CHAT_BUSY`。
8. 实现 Live2D 和 media 受控资源路由。
9. 实现访问码、Cookie、Origin 与路径安全检查。
10. 最后接入真实 ChatManager、LLM/Agent 和 GPT-SoVITS。

这样可以先独立验证协议、状态机和前端 reducer，再处理现有主程序的复杂队列适配。

## 18. 联调验收清单

- 前后端只使用 `protocol_version=1`。
- 首次连接能通过 `runtime_ready -> sync` 恢复会话主页。
- 没有会话时可以选择服务端返回的角色创建第一条会话。
- 切换会话只在目标 `state_snapshot` 到达后提交。
- 同一轮两个分段显示为两个独立气泡。
- 角色消息只出现七种正式 emotion，用户消息 emotion 为 `null`。
- 音频 URL 不包含本地路径，支持 Range 播放。
- Live2D 模型 JSON 的相对资源可以从同一模型路由加载。
- 重复发送同一 `client_message_id` 不产生重复用户消息或第二轮回复。
- 生成期间可打开会话列表，但切换、创建和再次发送得到 `CHAT_BUSY`。
- 取消后不再产生新分段，已经生成的分段继续存在。
- LLM/TTS 失败依次产生 `error` 和 `assistant_turn_complete(error)`。
- 断线重连后不依赖事件回放，通过快照恢复。
- 重连快照不会导致全部历史语音重新播放。
- 新设备使用正确访问码登录后，旧设备立即失去 HTTP 与 WebSocket 控制权。
- 未认证访问、错误 Origin、路径穿越和超大 WebSocket 帧均被拒绝。
