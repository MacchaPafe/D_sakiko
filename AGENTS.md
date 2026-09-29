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
## 角色描述标注

新的 AI roleplay 角色描述统一保存到项目根目录的 `角色描述/`，使用角色中文名称作为 `.txt` 文件名。语言风格用自然语言概括，不直接使用角色卡正文，不使用概率或频率规则，也不默认限定对话使用中文。

# Python Code Requirement for this Project

- Use Python syntax that supports Python 3.9 or later.
