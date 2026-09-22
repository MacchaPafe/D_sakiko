# Stage2 数据集复核编辑器独立发布方案

## 目标

把现有 `GPT_SoVITS/rag/pipeline/stage2_dataset_editor.py` 改造成只依赖轻量 GUI 与数据校验库的独立编辑器，并提供可在 macOS、Windows 分别本机构建的 PyInstaller 发布流程。外部复核者无需 Python 环境，只需解压发布目录、放入或接收预置的 `epXX_stage2_input.json`，再启动可执行文件。

本次确认的输入是 `epXX_stage2_input.json`，不是 `epXX_prepared.json`。该文件已合并字幕原文和 LLM 的 Stage 1 说话人标注，适合作为人工复核及后续 Stage 2 的直接输入。

## 当前状态结论

- 编辑器的业务功能已经能够加载、编辑、校验和保存 `Stage2InputArtifact`。
- 源码当前不能脱离仓库直接运行：
  - 它导入 `rag.pipeline.schemas`，后者又导入项目级 `rag.models`。
  - 默认数据目录写死为仓库内的 `pipeline/data/annotations_stage2`。
  - 即使 PyInstaller 能收集 Python 模块，冻结后的默认路径也会落到临时解包目录或包内目录，不适合作为用户可写的数据目录。
- 当前文件选择器始终扫描仓库默认目录；传入其他目录的 `--input` 后，不能自然切换该目录中的其他集数。
- 当前界面可复核 `speaker_name`、`addressee_candidates`、`mentioned_characters`、`emotion_hint`，并可切分/合并场景和保存备份。它没有暴露 `speaker_confidence` 与 `is_inner_monologue` 的编辑控件，因此还不能称为完整的 Stage 1 字段复核闭环。

## 实施设计

### 1. 解耦运行时依赖

- 在编辑器同目录增加仅覆盖 `Stage2InputArtifact` 所需字段的轻量 Pydantic schema。
- 编辑器优先使用包内相对导入，直接脚本运行时使用同目录导入，不再导入 `rag.models` 或其他主项目模块。
- 保留现有 JSON 格式与 Pydantic 校验行为，保证复核结果仍可直接进入现有 Stage 2 流程。

### 2. 修正独立运行路径

- 源码模式默认仍兼容仓库现有 `annotations_stage2` 目录。
- 冻结模式默认使用可执行文件旁的 `data/` 目录，确保 JSON 和 `.bak` 都写在用户可见、可回传的位置。
- `--input` 改为可选：
  - 指定时加载该文件，并扫描它的同级目录；
  - 未指定时从默认 `data/` 中选择第一个 `*.json`。
- 无输入文件时给出明确中文错误和放置位置，不静默退出。
- 使用可用端口作为默认端口，并保留 `--host`、`--port`、`--native`；增加测试/自动化所需的不自动打开浏览器选项。
- 在主入口调用 `multiprocessing.freeze_support()`，满足 NiceGUI/PyInstaller 冻结运行要求。

### 3. 补齐复核字段与可用性

- 增加 `is_inner_monologue` 开关。
- 增加 `speaker_confidence` 数值控件，范围为 0–1。
- 保留现有说话人、对象、提及角色、情绪、场景切分/合并、撤销和备份保存。
- 文件切换以当前文件所在目录为准，使多集 JSON 可放在同一个 `data/` 目录连续复核。

### 4. 独立发布工作区

在根目录 `publish_pipeline/` 中提供：

- `requirements.txt`：仅包含固定版本的 NiceGUI、Pydantic、PyInstaller；
- `build.py`：跨平台构建脚本，始终检查并使用 `publish_pipeline/.venv`，调用该环境内的 PyInstaller，并按当前平台动态生成 spec；
- 默认采用 `onedir` 浏览器模式，避免 onefile 每次解包、包内数据不可持久写回等问题；
- `data/`：发布时放置待复核的 `epXX_stage2_input.json`；
- 中文 `README.md`：面向无 Python 基础的构建者和复核者；
- 目录级 `.gitignore`：忽略 `.venv`、构建缓存、发布产物和测试数据副本。

PyInstaller 产物必须在目标操作系统上构建；macOS 产物只用于本机验收，Windows 需在 Windows/Python 3.11 环境重新运行同一脚本。

### 5. 验证

自动化与人工浏览器验证覆盖：

1. Python 语法检查与轻量单元测试；
2. 在 `publish_pipeline/.venv` 中确认只安装独立依赖，不引用项目根 `.venv`；
3. 用该环境构建 macOS `onedir` 产物；
4. 从发布目录启动冻结后的可执行文件；
5. 浏览器打开 GUI，确认场景、字幕和标注控件正常显示；
6. 在临时输入副本上修改说话人、内心独白、置信度等字段并保存；
7. 进程外重新读取 JSON，确认修改持久化、schema 校验通过、原文件 `.bak` 已生成；
8. 确认发布进程不依赖仓库 `PYTHONPATH`。

## 交付边界

- 不把真实标注数据纳入 Git 或固定打包进公共产物；本机验收使用被忽略的临时副本。
- 不在 macOS 生成或声称生成 Windows 可执行文件；只提供同一套 Windows 可运行的构建脚本和说明。
- 不改动 Stage 1 LLM 标注、Stage 2 抽取或 Stage 3 世界书主流程。
