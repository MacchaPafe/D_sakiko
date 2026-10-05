# AGENTS.md

## Agent skills

### Issue tracker

Issues and PRDs are tracked as local markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-label triage vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This repo currently uses a single-context domain documentation layout. See `docs/agents/domain.md`. Use Chinese when writing or updating these docs.

### Git 提交与同步

提交前先获取远端 master，并以仅快进方式同步当前本地分支。出现冲突、分叉或本地改动阻止快进时，停止并告知用户，不自行解决冲突或变基。推送后保持当前分支、本地 master 与远端 master 一致。提交说明保持简短，几句话说明主要改动即可。

# Python Code Requirement for this Project

- 此项目需要适配最低 Python 3.9 版本：即使当前环境解释器版本更高，也要编写兼容 Python 3.9 的代码。

### 前端（React/JS）编码与规范要求

1. 代码可读性优先：
   - 避免使用过于隐晦的语法糖（如嵌套三元运算符、多层深解构）。
   - 优先使用具名函数和语义化变量，逻辑复杂时使用分步处理代替超长单行链式调用。
   - 禁止在 JSX 内部直接书写包含复杂业务判断的多行箭头函数，应抽取为顶层 Handler。

2. 类型与注释规范：
   - 核心数据结构（如消息对象、Live2D 状态、TTS 配置）必须提供 JSDoc `@typedef` 注释。
   - 关键导出函数必须包含 JSDoc（说明参数类型与返回值含义）。
   - 为设计中“为什么不这样做“的选择编写注释，为代码中存在的巧妙或不易察觉的地方编写注释。
   - 杜绝复述代码字面行为的无效注释。
   - 所有注释用中文编写。