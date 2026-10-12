# Nunjucks 提示词模板设计

确认日期：2026-10-09。关联需求：AGENT-01、AGENT-07/08、SET-02。

本文记录已确认的技术选择和实现约定。当前只更新[生成接口](../src/backend/llm/interface.js)、[设置契约](../src/shared/contracts/settings.js)及文档；尚未安装项目依赖、创建正式模板或实现渲染器。旧快速原型不是本设计的实现。

## 选择与归属

采用 **Nunjucks + .njk 文件模板 + Agent 内部的独立 Environment**。模板使用变量、条件、循环、过滤器和 include 等通用语法，不自行实现占位符替换。选型验证基线为 Nunjucks 3.2.4；正式接入时将其作为运行依赖锁定，并通过锁文件固定依赖树。

选择 Nunjucks 是为了延续 Python Jinja2 模板的写法，并使用完整的通用文件模板引擎。LiquidJS 也是可用的通用引擎，但采用 Liquid 语法；`@huggingface/jinja` 面向模型聊天模板，当前需求不需要优先采用该方向。Nunjucks 发布节奏较慢，选型依据是现有功能及语法契合度，不承诺完整的 Python/Jinja2 兼容。

每种用途只有一份随应用发布的模板，不提供用户编辑器、模板选择列表或全局/Chat/单轮覆盖。角色的名称、description、用户人格及情景仍按原接口编辑，它们是模板的输入数据。模板文本和路径不进入 Settings、Chat、TurnOptions 或 AssetRef，也不随对话归档导出；生成模块内部的多个操作共用模板不改变其归属。

## 文件组织与职责

以下是实现阶段的源码布局约定，本次不创建占位模板或渲染实现：

```text
src/backend/llm/
  prompts/
    render.js
    templates/
      system.njk
      scenario.njk
      output.njk
      summary.njk
```

| 文件         | 用途与输入                                                                              | 请求中的位置                                       |
| ------------ | --------------------------------------------------------------------------------------- | -------------------------------------------------- |
| system.njk   | 通用角色扮演规则；接收 mode、角色名称/description、固定用户人格和允许直接使用的补充知识 | 普通生成的 system 提示                             |
| scenario.njk | 小剧场情境、角色说话风格和互动设定                                                      | 情景生成的 User Prompt，不伪造存档 UserMessage     |
| output.njk   | 生成语言、翻译开关及目标语言、合法发言者和本轮演出目录对应的输出要求                    | 由 system.njk 通过 include 复用                    |
| summary.njk  | 累计摘要应保留的信息及压缩要求                                                          | 压缩请求的 system 提示；待压缩历史和旧摘要另行提供 |
| render.js    | 校验渲染数据、管理固定模板环境并生成所需文本                                            | llm 内部 helper，不新增跨模块公共接口              |

单角色和小剧场共有规则放在同一 system.njk 中，必要差异由明确的 mode 和角色数组表达。用途不同的几个文件组成一套固定模板，不是供用户选择的多套配置。

模板只组织提示文本。历史的 assistant 分组、reasoning、工具调用/结果及附件仍由 Agent 和供应商适配层按协议组装，不能将整个历史渲染成一个字符串替代结构化消息。工具 JSON Schema、输出 Schema 和响应业务校验继续由程序维护；提示中的说明不能代替校验。

## 渲染输入与生命周期

Conversation 继续解析并冻结角色、配置、工具和知识范围，向 Agent 提供 GenerationRequest。Agent 从中投影普通数据供模板使用，不向渲染环境暴露完整 Chat、SDK 对象、凭据或可调用的工具函数。

- 角色材料来自 request.characters，用户身份来自 request.userPersona；渲染时不重新读取角色目录或可变全局设置。
- 情景来自 request.scenario；补充知识只使用 request.supplementalContext 中已经允许提供给模型的内容，不传检索诊断。
- 语言和翻译等语义选项可影响输出要求；temperature、topP、超时和重试上限仍是程序参数，不拼入提示词。
- 摘要模板只参与压缩，不进入普通角色回复。待压缩内容来自 CompressionRequest.history，不在模板中决定哪些轮次应被覆盖或删除。
- run 与 inspectContext 共用提示渲染及请求准备规则。同一份输入材料应生成相同提示；预览计入渲染后的文本，同时继续计算工具、Schema、历史和附件开销。
- 一次运行期间使用固定的模板集合和输入快照。后续工具结果按原 Agent Loop 规则追加，不因重新渲染而读取新的用户设置。

输入必须在渲染前校验。必需字段缺失时报错；允许为空的 description 仍可为空字符串；可选人格和翻译语言明确使用 null，并由条件分支处理。不能只依赖 throwOnUndefined：它会拒绝输出 null/undefined，但条件表达式中不存在的变量仍可能被视为假。

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

例如，角色及人格描述可这样组织：

```jinja
{% for character in characters %}
[{{ character.displayName }}的人物设定]
{{ character.description }}
{% endfor %}
{% if userPersona and (userPersona.description | trim) %}
[{{ userPersona.displayName }}的人物设定]
{{ userPersona.description }}
{% endif %}
```

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

后续正式实现需要验收：

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
