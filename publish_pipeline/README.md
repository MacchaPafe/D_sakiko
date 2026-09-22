# Stage2 数据集复核编辑器发布说明

这个目录用于把 `stage2_dataset_editor.py` 构建成无需安装 Python 的独立程序。复核输入必须是现有流水线生成的 `epXX_stage2_input.json`。

## 给构建者

PyInstaller 不能跨系统生成可执行文件：macOS 产物必须在 macOS 构建，Windows 产物必须在 Windows 构建。两边都使用 Python 3.11。

### macOS

```bash
cd publish_pipeline
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python build.py \
  --data ../GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json
```

### Windows

在 PowerShell 或 `cmd.exe` 中运行：

```bat
cd publish_pipeline
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe build.py --data C:\path\to\ep01_stage2_input.json
```

发布目录为 `publish_pipeline/dist/Stage2DatasetEditor/`。建议把整个目录压缩后发送，不要只发送其中的可执行文件。需要分配多集时，可重复传入 `--data`：

```bash
.venv/bin/python build.py --data ep01_stage2_input.json --data ep02_stage2_input.json
```

也可以在构建后手动把更多 JSON 放入发布目录的 `data/` 文件夹。

## 给复核者

1. 完整解压 `Stage2DatasetEditor` 文件夹。
2. 确认 `data` 文件夹中包含一个或多个 `epXX_stage2_input.json`。
3. Windows 双击 `Stage2DatasetEditor.exe`；macOS 在终端运行 `./Stage2DatasetEditor`。
4. 程序会自动打开浏览器。终端窗口必须保持打开。
5. 逐句检查说话人、置信度、内心独白、说话对象、提及角色和情绪。
6. 点击右上角“保存”或按 `Ctrl+S` / `Cmd+S`。
7. 完成后关闭浏览器，并在终端按 `Ctrl+C` 结束程序。
8. 把 `data` 中复核后的 JSON 发回。第一次保存时程序还会生成 `.json.bak` 原始备份。

如需切换集数，使用页面右上角的 JSON 选择框；它会列出当前 `data` 文件夹中的文件。

## 源码方式快速检查

以下命令同样只使用这个目录的隔离环境，不需要安装主项目依赖：

```bash
.venv/bin/python ../GPT_SoVITS/rag/pipeline/stage2_dataset_editor.py \
  --input ../GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json
```

运行轻量回归测试：

```bash
.venv/bin/python -m unittest test_editor.py
```
