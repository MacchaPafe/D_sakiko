# 数字小祥 · Electron 开发原型

本机可启动的单角色/二人对话原型，包含有限 Agent Loop、时间工具、格式纠正、GPT-SoVITS 语音、双版本 Live2D、多聊天后台推进和用户人格。本轮交付不包含安装包。

范围、状态规则与验收标准见 [原型说明](docs/prototype.md)。原有 [核心接口设计](docs/core-interfaces.md) 继续保留，便于后续比较设计与原型实现。

## 本机启动

需要 Node.js 22.12+、pnpm 11，以及能运行 GPT-SoVITS 的 Python 环境。当前机器使用已有 Python 3.11 虚拟环境完成了 CPU 推理验证。

```sh
cd dsakiko-electron
pnpm install
# 首次配置时将 .env.example 复制为 .env.local，并填写路径
pnpm dev
```

本轮已为当前机器准备忽略提交的 `.env.local`，指向现有资源目录和 Python 解释器。它只包含本机路径，不包含 API 密钥。直接打开开发服务器网页无法使用 Electron 的受控接口。

进入「设置」，点击「从旧配置读取模型连接」，或填写兼容 Chat Completions 的服务地址、模型和密钥，保存后新建对话。服务地址一般填写到 `/v1`；也支持已包含 `/chat/completions` 的地址。旧配置中的 DeepSeek/OpenAI 默认地址及显式配置的兼容地址可导入；其他供应商可能需要手动填写兼容地址。

新建聊天选择一名或两名角色，可选用户人格。生成及演出期间可以编辑草稿、切换聊天，不能向同一聊天追加消息。「停止」保留当前播放句，撤销后续任务。打开设置会让所有聊天进入后台。关闭主窗口会退出程序与语音进程。

尚未开始演出的台词不显示气泡或占位文字；轮到该句后，原文与翻译按演出进度逐字出现。历史内容继续完整保留，回放不会重新隐藏历史消息。退出时并行清理对话和语音；如果渲染端失去响应，最长 12 秒后结束程序，重启时按中断任务处理。

侧栏「角色装扮」或舞台右上角「切换装扮」可选择各角色的 Live2D 模型。列表读取共用角色目录中的默认模型、额外服装与旧版演出服，显示装扮名称和 V2/V3 标记。点击「应用装扮」后更新舞台并记住选择，适用于该角色的所有对话。涉及该角色的任一对话正在生成或演出时暂时不能换装，其他角色的后台任务可继续运行。新增外部模型文件后重启原型以重新扫描。

## 数据与资源

开发启动默认把数据写入 `.local/data/`，可通过 `DSAKIKO_DATA_DIR` 改为其他绝对路径。直接 `pnpm start` 默认使用 Electron 用户数据目录，若希望与开发启动共用记录，请显式传入同一个 `DSAKIKO_DATA_DIR`。

- `settings.json`：本机设置及模型凭据，默认权限为仅当前用户可读写，原型未接入系统钥匙串。
- `chats/`、`personas/`：带格式版本和修订号的 JSON 存档。
- `character-models/`：每个角色独立保存的模型选择；不修改旧程序的服装配置。
- `assets.json`、`audio/`：稳定资源引用与已经导入的音频。
- `speech-temp/`、`logs/speech.log`：本次推理临时输出和诊断信息。

外部资源根目录只读，按现有目录布局识别：

```text
资源根目录/
├── d_sakiko_config.json              # 可选，读取模型连接及当前服装
├── live2d_related/<角色>/             # 名称、描述、头像、Live2D
├── reference_audio/<角色>/           # GPT/SoVITS 权重和参考音频
└── GPT_SoVITS/
    ├── pretrained_models/           # Hubert、BERT、语种检测及相应版本权重
    └── text/G2PWModel/               # 中文读音模型
```

缺少语音资源的角色可以使用文字演出。模型或合成运行失败有界面提示，并降级为无模型/无语音演出。数据目录不自动清理，删除聊天也暂不回收持久音频。重启只恢复记录，永不续跑中断的 Agent、合成或演出。

## 检查命令

| 命令                                         | 内容                                         |
| -------------------------------------------- | -------------------------------------------- |
| `pnpm build`                                 | 构建主进程、preload 和 React                 |
| `pnpm lint` / `pnpm format:check`            | 代码与格式检查                               |
| `pnpm test`                                  | Node、演出、存档、人格、Agent 与并发边界测试 |
| `pnpm test:python`                           | 无需模型的 Python 调度测试                   |
| `pnpm test:smoke`                            | 真实 Electron + 本机模型响应测试服务器       |
| `node scripts/verify-speech.mjs sakiko anon` | 两个真实 GPT-SoVITS 模型的并发验收           |
| `node scripts/verify-agent.mjs sakiko anon`  | **显式调用外部模型服务**，消耗该服务额度     |

桌面测试使用临时数据，不调用外部模型服务。可设置 `DSAKIKO_E2E_RESOURCES` 来额外验证本机的 v2/v3 混合同屏、换装和重启后恢复；再设置 `DSAKIKO_E2E_SPEECH_PYTHON` 可验证真实语音合成、播放与完整演出。截图保存在忽略提交的 `test-results/`。

2026-10-05 已获授权并实测 DeepSeek `deepseek-flash`：一次时间工具调用后成功返回祥子、爱音两名角色的有效台词。详细结果见原型说明；外部服务实测命令不会加入默认测试套件。

## 工程边界

`src/backend` 持有聊天、设置、存档与角色目录；`src/main` 负责 Electron 和 Python 生命周期；`src/preload` 只开放具名命令。`src/renderer/src/performance` 独立管理前后台演出，React 页面只观察状态。`python/speech` 独占语音队列与资源预算。

需要的旧 WebUI 适配代码和 GPT-SoVITS 推理依赖已复制，工程没有导入父目录业务代码。复制来源和差异见 [依赖说明](python/vendor/README.md)。Python 独立依赖与锁文件见 [Python 启动说明](python/README.md)。大型权重、SDK 使用条件和运行环境不会因为复制代码而消失。

已保留初始化工程的打包脚本，但本轮没有制作或验证安装包。移动端、桌宠、托盘、ASR、附件、资源下载、更新等入口不在本原型范围。
