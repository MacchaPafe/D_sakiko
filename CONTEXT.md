# D_sakiko

本上下文描述 D_sakiko 中角色展示、语音和 Live2D 演出相关的项目语言。

## Language

**动作编辑器预览**:
在 `live2d_viewer.py` 动作编辑器中临时播放某个 motion 文件，用来让用户检查左侧动作文件或右侧动作组条目的实际表现。它不是角色正式动作组配置的一部分，不应要求持久修改 `model3.json`。
_Avoid_: 预览动作组持久化、导入动作

**Live2D 支持模块**:
项目自有的 Live2D 辅助代码，包括布局、模型规范化、runtime 适配、表情策略和动作选择策略。该概念不包含第三方 `live2d` runtime 包本身。
_Avoid_: live2d 包、runtime 包

**Live2D 模型导入**:
将项目外部的 Live2D 模型复制到某个角色的 `extra_model` 目录，并在复制后的模型上执行项目规范化，使其能被模型切换窗口选择。它不等同于切换已有模型，也不应直接改写用户原始模型目录。
_Avoid_: 选择模型、就地规范化、循环导入

**项目标准动作组**:
应用内部稳定使用的一组 Live2D motion group id，例如 `happiness`、`IDLE`、`text_generating`、`idle_motion`。模型导入、动作编辑器、普通聊天和小剧场都应围绕这些 id 协作，而不是各自发明动作组命名。
_Avoid_: 下载站点原始动作类别、表情 ID

**方向动作支持**:
Live2D V3 模型在 `model3.json` 中拥有显式、非空的项目标准动作组方向变体，例如 `{group}_L` 或 `{group}_R`。运行时把缺失方向变体回退到基础动作组只是兼容播放行为，不代表模型支持该方向动作。
_Avoid_: fallback 可播放、存在空方向组

**语音模型预加载**:
在用户实际触发语音合成前，提前加载当前角色的 GPT-SoVITS 语音模型，用来减少首次生成语音的等待。它不等同于启用语音合成；关闭预加载后，按需语音合成仍可加载模型并生成语音。
_Avoid_: 启用语音合成、纯文本模式

**更新结果记录**:
一次独立更新器执行结束后留给主程序读取的机器可读结果，用来说明更新是否成功、回滚状态、关联日志以及用户是否已经收到失败提示。它不是完整更新日志，也不是更新计划。
_Avoid_: 更新日志、更新状态、更新计划

**可修复程序资源**:
官方发布包中可被独立校验、独立下载、独立替换的小型程序文件，例如代码、更新器脚本、启动脚本、版本元数据、小型配置和小型界面资源。它不包含依赖运行时、虚拟环境、大模型、用户配置、聊天记录、自定义角色或用户 Live2D 资产。
_Avoid_: 完整安装包、运行环境、用户数据、模型资源

**修复结果记录**:
一次独立修复器执行结束后留给主程序读取的机器可读结果，用来说明程序资源修复是否成功、回滚状态、关联日志以及用户是否已经收到失败提示。它不是完整修复日志，也不是修复计划。
_Avoid_: 修复日志、修复状态、修复计划

**对话备份包**:
用户导出的可恢复对话集合，包含对话内容以及恢复这些对话所需的本地资源引用。它不是程序自动保存的完整聊天存档，也不是只供当前机器临时使用的调试 JSON。
_Avoid_: 全量聊天存档、临时导出 JSON

**备份资源身份**:
对话备份包中本地资源的可验证身份，由资源在备份包中的路径、原始文件名和内容摘要共同描述，用来在导入时判断资源能否安全复用。它不是运行时文件路径本身。
_Avoid_: 文件路径、文件名

**对话分叉**:
从某条既有消息创建一条新的可继续对话，新对话保留分叉点及之前的上下文，之后的内容与原对话独立发展。它不同于回溯，回溯会修改原对话。
_Avoid_: 回溯、重生成

## RAG Knowledge Context

本上下文描述角色扮演 RAG 中客观剧情、原子事实与角色主观认知之间的区别，用于避免把观众视角的事实泄露给角色。

### Language

**Story Event**:
作品中客观发生的一段相对完整、可独立检索的剧情。
_Avoid_: Fact, Thought

**Event Fact**:
从 Story Event 中按需拆出的、可独立判断真伪及角色是否知晓的客观原子事实。它只在角色观点链接、知情差异或知识泄漏风险需要时存在，不追求完整分解 Story Event。
_Avoid_: Knowledge Claim, Story Event

**Character Thought**:
某个角色在一段剧情时间内对 Story Event 或 Event Fact 持有的认知、推测、否认或主观解释；它可以不完整或不符合客观事实。该术语不泛指情绪、行动意图或与剧情认知无关的任意内心独白。
_Avoid_: Character Knowledge, Character Belief State

**Character Thought Update**:
有字幕证据支持的局部认知变化，例如角色获知、目击、怀疑、否认、确认或修正了关于 Story Event 或 Event Fact 的想法。它是构建 Character Thought 时间区间的证据，不是最终认知状态。
_Avoid_: Character Thought, Knowledge Update

**Thought Transition**:
Stage 3 根据 Character Thought Update 与既有 Thought Thread 解析出的观点状态变化，例如获得、重申、修正、撤回或披露既有观点。它只用于构建时间区间和复核，不属于最终注入角色 prompt 的 Character Thought 内容。
_Avoid_: Epistemic Status, Character Thought

**Thought Subject**:
Character Thought 所谈论的语义对象；它可以是 Story Event、Event Fact、无需事件链接的独立主题，或暂时无法判断的对象。Thought Subject 描述观点“关于什么”，不描述观点由什么原因产生。
_Avoid_: Thought Source, Reference Kind

**Thought Thread**:
同一角色围绕同一 Thought Subject 的某一个认知方面所形成的连续观点脉络，例如事件状态、原因或责任判断。不同 Thought Thread 可以同时有效，只有同一脉络中的明确修正或替代才会结束旧的 Character Thought。
_Avoid_: Story Event, Character Thought

**Evidence Time**:
字幕首次明确证明角色持有某个 Character Thought 的剧情时间。它描述证据何时出现，不保证该观点在此之前不存在。
_Avoid_: Thought Effective Time

**Thought Effective Time**:
有充分证据支持 Character Thought 已经生效的最早剧情时间；缺少可靠的先前时间锚点时，它等于 Evidence Time。
_Avoid_: Evidence Time

**Thought Evidence Strength**:
Character Thought Update 的字幕依据是明确表达还是由局部场景上下文合理推断。它描述证据的直接程度，不描述角色自身有多确定，也不等同于标注模型的置信度。
_Avoid_: Epistemic Status, Extraction Confidence

**Epistemic Status**:
角色自身对 Character Thought 所持的认知立场，第一版限定为 `knows`、`believes`、`suspects`、`uncertain` 或 `rejects`。它只描述角色的主观确信程度，不保证观点客观正确。
_Avoid_: Thought Evidence Strength, Extraction Confidence

**Extraction Confidence**:
标注模型对 Character Thought Update 抽取结果正确性的置信度。它不表示观点客观为真，也不表示角色自身确信。
_Avoid_: Epistemic Status, Thought Evidence Strength

**Character Relation**:
一个角色面对另一个角色时相对持续的互动态度、称呼方式与说话倾向。它不承载角色对具体事件、事实或责任归属的解释。
_Avoid_: Character Thought
