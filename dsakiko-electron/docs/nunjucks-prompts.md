# Nunjucks 提示词模板设计

确认日期：2026-10-09。关联需求：AGENT-01、AGENT-07/08、SET-02。

本文记录已确认的技术选择和实现约定。正式 [Agent](../src/backend/llm/interface.js) 已安装 Nunjucks 3.2.4 并使用固定模板；生产构建将模板复制到 out/main/prompts。当前实现边界见[实现说明](backend-implementation.md)。

## 选择与归属

采用 **Nunjucks + .njk 文件模板 + Agent 内部的独立 Environment**。模板使用变量、条件、循环、过滤器和 include 等通用语法，不自行实现占位符替换。选型验证基线为 Nunjucks 3.2.4；正式接入时将其作为运行依赖锁定，并通过锁文件固定依赖树。

选择 Nunjucks 是为了延续 Python Jinja2 模板的写法，并使用完整的通用文件模板引擎。LiquidJS 也是可用的通用引擎，但采用 Liquid 语法；`@huggingface/jinja` 面向模型聊天模板，当前需求不需要优先采用该方向。Nunjucks 发布节奏较慢，选型依据是现有功能及语法契合度，不承诺完整的 Python/Jinja2 兼容。

每种用途只有一份随应用发布的模板，不提供用户编辑器、模板选择列表或全局/Chat/单轮覆盖。角色的名称、description、用户人格及情景仍按原接口编辑，它们是模板的输入数据。模板文本和路径不进入 Settings、Chat、TurnOptions 或 AssetRef，也不随对话归档导出；生成模块内部的多个操作共用模板不改变其归属。

## 文件组织与职责

当前模板布局如下；环境管理和渲染函数暂时直接位于 Agent 接口实现中：

```text
src/backend/llm/
  prompts/
    templates/
      system.njk
      character-context.njk
      runtime-controls.njk
      scenario.njk
      output.njk
      summary.njk
```

| 文件                  | 用途与输入                                   | 请求中的位置                                       |
| --------------------- | -------------------------------------------- | -------------------------------------------------- |
| system.njk            | 固定角色扮演、上下文和输出原则               | 普通生成的 system，内容不随对话变化                |
| character-context.njk | 各参与角色当前人格的介绍、固定对话者身份     | 在使用该份资料的第一轮输入前追加                   |
| runtime-controls.njk  | 本轮自称与署名、演出能力、知识、工具和输出要求 | 当前请求末尾的 user 内容                           |
| scenario.njk          | 小剧场情境、风格、互动和篇幅                 | runtime-controls.njk 内包含                        |
| output.njk            | 语言、翻译、字段约束和本轮合法示例           | runtime-controls.njk 内包含                        |
| summary.njk           | 累计摘要应保留的信息及压缩要求               | 压缩请求单独使用的固定 system                      |

这些文件组成一套随应用发布的模板，不是用户可选的多套配置。模板编译缓存与模型服务的前缀缓存是两件事；固定 system 和保留历史前缀能减少无谓变化，但不保证服务商命中缓存。

## 与 Python 提示词的行为对齐

保留角色口吻、用户称呼、必要的括号描写、自然语义分段和适度主动使用工具。不引入 restr.rep 规则。v3 已选动作或表情能呈现的表现无需在台词中重复描写；无法由模型呈现且对情境必要的细节仍可保留。演出选择不代表播放成功。

小剧场约 15 条角色发言，允许同一角色连续发言。摘要保留 Python 风格的固定小节，并区分历史事实、附件发送记录、工具结果和未完成事项。

LLM 输出的 lines 数组逐行包含 speaker、text、translation、emotion。每个角色的「自我认知名称」决定自称及默认 speaker，可以取角色名或当前人格名。若两位演员署名相同，当轮 speaker 使用「角色名（人格名）」消歧。后端将署名映射到当轮的 speakerId 与 formId，并保存原署名供后续历史使用。v2 或无模型时不要求 performance；v3 使用 performance.motion 和 performance.expression。v2 忽略额外返回的演出选择。v3 缺省选择按 auto 处理，显式未知 ID 触发格式修复。开启翻译时必须非空，关闭时必须为 null，情绪限定为七个情绪标签。具体示例按当前角色、语言、翻译开关和模型版本生成。

## 人物资料与轮次生命周期

Conversation 在受理时冻结人物、模型能力、配置和工具。角色定义编辑、人格切换或换装影响下一轮的请求；正在执行的工具循环继续使用原快照。人物名称和描述只作为模板变量插入，不二次执行模板语法。

新对话只提供各参与角色当前选中人格的介绍。以祥子为例，先用黑祥连续聊两轮，开头只出现一次黑祥的介绍。第三轮切到白祥，白祥的介绍追加在第三轮输入前。第四轮切回黑祥，黑祥的介绍再追加一次。每次更新都从该位置起生效，此前的对话保留当时的资料。

当同一角色前后两次记录的人格 ID 不同时，新介绍前会提醒模型记住已有的对话和约定，并用当前人设的口吻、态度和视角接续。小剧场按演员分别判断。首次介绍和同一人格的资料编辑沿用普通介绍。

每轮 `TurnMeta.characterContext` 保存各参与角色当时的人格、角色名、自称设置和对话者身份。切换人格、修改当前介绍或自称设置会产生一份更新；只编辑尚未选中的人格，对话会在选用它时才看到新介绍。按角色名自称时，介绍标题只显示角色名；按人格名自称且两者不同时，标题显示「角色名（人格名）」。用户写在介绍正文中的文字会原样发送。

从 Python 导入的历史可能缺少人物快照。这段历史按原来的顺序发送。首次续聊时，所选人格的介绍放在导入历史之后、本轮新输入之前。以后切换人格时，先前的历史前缀保持稳定。

普通请求依次提供固定 system、历史中的人物资料与对话、工具结果、必要的最新人物资料、本轮输入和 runtime_controls。工具调用循环只追加调用与结果，本轮的 runtime_controls 在下一轮重建时移除。

摘要使用独立的 system。压缩输入沿用普通对话的人物介绍模板和当时的台词署名，保留旧摘要、历史发言、附件记录及工具结果。应用内部使用的角色与人格 ID、自称设置字段和模型推理片段不会作为人物资料或台词送入摘要。继续对话时，从保留轮次的快照重建介绍。

run 与 inspectContext 共用渲染规则，预算估算计入人物资料、控制消息、工具和附件。人物资料与模型目录不放进 system。工具 Schema 的变化仍可能影响服务商前缀缓存，这是工具协议自身的变化。

## Live2D 目录与演出

角色能力和演出模块共用 characters/model-catalog.js。解析器以模型入口确认 v2/v3，从既有 .performance.json 读取字符串描述，也兼容 description 对象；支持共享 series、bindings、组合 presets、DSakiko.MotionAssets 完整索引和方向变体。只有真实存在且具有描述的候选进入 LLM 目录，未标注资源仍可用于自动动作组回退。状态专用动作从普通 LLM 候选和自动动作池中排除。

presentation.mapping 可以直接引用原来的 performance.json；为空时自动读取同包内的同名文件（例如 model.model3.performance.json）。共享标注按 Python 的角色目录规则查找 performance.shared.json。需要共享文件时应导入包含该文件的角色目录作为资源包，并把模型 JSON 设为入口；不读取包外的父目录，不另建 Electron 标注文件。仅导入模型入口单文件不足以提供纹理、动作或相邻标注。

实际演出以当前场景模型为准：v2 忽略旧 v3 动作和表情，按 emotion 选择动作组；v3 分别解析两个通道，无效动作回退到情绪组、待机组和有效普通动作，无效表情按 Python 语义候选回退到默认表情。不存在可用资源的通道返回 null。方向由宿主指定，依次选择指定方向、无方向资源、C，不随意拿其他侧面替代。

实时快照和回放 Guide 增加 resolvedPerformance，包含逻辑 ID、包内 member 路径和回退说明；原 performance 保持不变，不把磁盘绝对路径发送给客户端。待机、思考、语音输入通过 performance.setActivity 分别选择 idle_motion、text_generating、talking_motion，v2/v3 共用。生成开始和结束由 Conversation 设置思考/待机；语音输入由宿主调用该接口。前端渲染适配不在本次后端变更范围内。

## Nunjucks 使用约定

在 llm 内部显式创建 Environment，使用 FileSystemLoader 加载固定文件。避免依赖顶层 configure/render 的共享全局环境。初始化时加载并编译已知模板及片段，之后复用；所有过滤器使用同步、无外部副作用的逻辑，模板渲染不发网络请求、不检索知识、不读取业务目录。

| 所属对象         | 选项             | 固定值 | 原因                                                     |
| ---------------- | ---------------- | ------ | -------------------------------------------------------- |
| Environment      | autoescape       | false  | 输出是提示文本，保留 &、尖括号等字符，不转换为 HTML 实体 |
| Environment      | throwOnUndefined | true   | 直接输出缺失字段时尽早报错，配合渲染前的数据校验         |
| Environment      | trimBlocks       | true   | 去除块标签后的多余换行                                   |
| Environment      | lstripBlocks     | true   | 去除块标签前的缩进空白                                   |
| FileSystemLoader | watch            | false  | 模板随应用版本发布，不在运行中热更新                     |
| FileSystemLoader | noCache          | false  | 复用已加载和编译的模板                                   |

模板正文的段落换行仍需人工设计并验收；空白控制不保证任意模板都会生成可读排版。不启用实验性的 installJinjaCompat。原 Python 模板中的字符串 `.strip()` 改用 `| trim`，其他 Python 方法或特殊语法逐项迁移，不假定复制 .jinja 文件即可完全兼容。

description、情景、知识文本等只作为 context 的值传入。不得把这些值拼到模板源码后编译，也不得对渲染结果再次执行 renderString。即使值中含有 `{{ ... }}` 或 `{% ... %}`，也只作为文字输出。

Nunjucks 不提供模板执行沙箱，因此模板源码及 include 目标仅来自应用内置文件；入口文件名由 llm 内部常量选择，不接受模型或页面提供的路径。此规则保护模板执行过程，不意味着角色文本中的任意指令会被模型自动忽略。

## Electron 加载与发布

模板源文件留在 llm 内维护。实现时由构建流程将 templates 目录原样复制到打包资源中的固定 prompts 目录，保留 include 使用的相对布局。发布包必须包含这些文件；不能假定 electron-vite 会自动复制源码旁的 .njk 文件。

宿主装配负责区分开发目录和安装后的资源目录，并将解析出的绝对目录用于构造 Agent 内部渲染环境；backend 不直接导入 Electron，也不依赖 process.cwd()、父仓库路径或开发机绝对路径。目录属于内部装配材料，不加入 GenerationRequest、Settings 或 IPC 参数。

初始化时发现缺文件、语法错误或未知过滤器应报告明确错误；需要该模板的操作不能带着空提示继续。渲染输入错误不触发模型请求重试或格式修复。执行期间若仍出现渲染失败，遵守已有阶段规则：保留已受理输入和过程，不重放已完成工具；摘要失败保留原文及旧摘要。

更新应用可更新模板。重生成使用当前应用内置模板和当前有效业务材料，不承诺复现旧版本模板的逐字输出。历史已有台词与摘要保持不变，不因升级被重新渲染写回；反馈提示词快照的独立设计不在本次展开。

## 接口与旧数据衔接

- 删除 GenerationOptions.prompts，GlobalSettings、ChatSettings、TurnOptions 和 TurnMeta 引用该类型的地方随之不再接受模板覆盖。
- GenerationRequest 保留现有业务输入，不增加模板路径、文本、AssetRef 或模板选择 ID。
- CompressionRequest.options 使用 ResolvedGenerationOptions，与普通生成一致；连接及模型只由 connection 指定，摘要模板由 Agent 内部提供。
- 正常设置写入对已删除的 prompts 字段按未知业务字段拒绝。若旧配置或导入数据携带自定义提示，由设置迁移或导入适配器明确报告不再生效；需要保留的原文只作迁移诊断，不作为新核心的可执行模板或隐藏覆盖。

## 验证证据与后续验收

本次选型讨论已在临时目录用 **Node v26.11.0、Nunjucks 3.2.4** 完成 8 项断言，覆盖文件加载/循环/include/条件、特殊字符原样保留、输入值不二次解析、缺失输出变量抛错、缺失条件变量不抛错，以及现有 Python 单角色模板替换 `.strip()` 后的样例渲染。没有修改项目依赖，也未据此宣称所有 Jinja2 语法兼容。

回归验收项目：

1. 单角色、小剧场、无用户人格、空描述、开关翻译和累计摘要的实际模板输出。
2. 必需输入缺失、模板缺失或损坏、未知过滤器能在模型请求前明确失败。
3. 特殊字符和模板标记作为数据保持原样，预览与实际准备使用相同的提示文本。
4. Windows/macOS 打包产物离开源码目录后仍能加载模板及 include，且运行中不读取新的模板或设置。
5. 项目支持的 Node/Electron 运行环境及最终锁定依赖组合通过验证。

现有证据尚未覆盖 Electron 打包资源加载、最低 Node 版本、真实模型回复质量或性能基准。

## 依据

- [Nunjucks 官方项目](https://github.com/mozilla/nunjucks)：Jinja2 风格的通用 JavaScript 模板引擎。
- [模板语法](https://mozilla.github.io/nunjucks/templating.html)：变量、控制流、include 与 trim 等过滤器。
- [Environment 与 API](https://mozilla.github.io/nunjucks/api.html)：独立环境、配置、文件加载及兼容性限制。
- [模板执行范围说明](https://mozilla.github.io/nunjucks/api.html#user-defined-templates-warning)：不提供用户模板沙箱。
- [版本记录](https://github.com/mozilla/nunjucks/blob/master/CHANGELOG.md)：选型基线 3.2.4 的发布记录。
- [LiquidJS 文件模板](https://liquidjs.com/tutorials/render-file.html)、[Hugging Face Jinja](https://github.com/huggingface/huggingface.js/tree/main/packages/jinja)：候选定位。
- [现有 Python 模板](../../GPT_SoVITS/templates/single_character.jinja)及[渲染入口](../../GPT_SoVITS/chat/chat.py)：沿用文件模板和数据渲染方式，按 JS 契约重组输入。
