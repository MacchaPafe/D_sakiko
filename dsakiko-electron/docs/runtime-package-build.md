# 运行环境包构建与验收

入口：`node scripts/runtime/build.mjs`，也可以使用 `pnpm build:runtime --kind asr`。以下命令都在 `dsakiko-electron` 目录运行。脚本只构建本地文件，不上传 ModelScope。

## 构建机器

| 目标                 | 构建系统          | 硬件验收              |
| -------------------- | ----------------- | --------------------- |
| ASR macOS arm64      | Apple Silicon Mac | 本机 CPU              |
| GSV macOS arm64 CPU  | Apple Silicon Mac | 本机 CPU              |
| ASR Windows x64      | 原生 Windows x64  | CPU                   |
| GSV Windows x64 CPU  | 原生 Windows x64  | CPU                   |
| GSV Windows x64 CUDA | 原生 Windows x64  | NVIDIA GPU 与对应驱动 |

不使用 WSL 构建 Windows 包；脚本会拒绝 Linux、macOS x64、Windows arm64 和跨架构 Node。当前 GSV 锁定的 SciPy 1.16.3 macOS wheel 要求 macOS 14+；此版本不能宣称覆盖所有 arm64 macOS。

预装 Node.js >=22.12（包含 npm）、pnpm >=12、uv >=0.13.6，执行 `pnpm install --frozen-lockfile` 安装工程构建依赖。`packageManager` 固定为 pnpm 12.15.0，`engines.pnpm` 为 >=12.0.0。工程的 Electron 必须已完成安装，构建验收会启动它的 Node 子进程。

macOS 安装 Xcode Command Line Tools；Windows 安装 Visual Studio 2022 Build Tools 的“使用 C++ 的桌面开发”、Windows SDK 和 CMake。少数依赖没有 wheel 时只在构建机器编译，客户端完全离线安装预编译 wheels。建议 Windows CPU 构建预留 15 GiB，CUDA 预留 25 GiB；构建脚本会在关键步骤检查空间。

构建需要访问 npm、PyPI、Python 独立发行包源，以及 Windows 的 PyTorch wheel 源。最终用户仍然只从 ModelScope 下载完成的 ZIP，不需要访问这些构建源。

## macOS 命令

```sh
node scripts/runtime/build.mjs --kind asr
node scripts/runtime/build.mjs --kind gsv
```

## Windows PowerShell 命令

先确认 `node -p "process.platform + '-' + process.arch"` 输出 `win32-x64`，以及 `uv --version` 正常。

```powershell
node scripts/runtime/build.mjs --kind asr
node scripts/runtime/build.mjs --kind gsv --accelerator cpu
node scripts/runtime/build.mjs --kind gsv --accelerator cuda --cuda cu126
```

CUDA 默认使用 PyTorch 2.7.1 + cu126；也可明确选择 cu118 或 cu128。CPU 包锁定 `torch==2.7.1+cpu`、`torchaudio==2.7.1+cpu`。每次发布仅选择一个 CUDA 变体，固定文件名不会同时保留多个 CUDA 版本；支持哪些显卡和驱动需要在目标机器实际验证。

没有 NVIDIA GPU 的构建机可以生成候选包：

```powershell
node scripts/runtime/build.mjs --kind gsv --accelerator cuda --cuda cu126 --skip-gpu-test
```

候选 ZIP 和 `.build.json` 会生成，但不会加入 `release.json`。把两者放到 NVIDIA 验收机的同一工程 `dist/runtimes/` 下，再执行：

```powershell
node scripts/runtime/build.mjs --kind gsv --accelerator cuda --cuda cu126 --verify-existing
```

该命令直接验收现有归档，不重新收集 wheels；通过后才登记到本机 `release.json`。它检查 CUDA 可用性，真实语音合成仍应通过下述模型验收完成。

## 构建和校验过程

1. 自动准备独立 CPython 3.11.18，仅写入 `.runtime-build/python/`，不修改全局 Python、Windows 注册表或 PATH。
2. ASR 依据 `scripts/runtime/asr/package-lock.json` 执行 `npm ci`。明确允许 ONNX Runtime、Sharp、protobufjs 的必要安装脚本；仅保留当前目标的 ONNX Runtime 原生库，其他依赖及许可证随包保留。
3. GSV 导出 `python/uv.lock` 的完整依赖，在当前 Python 3.11/平台收集和构建 wheels。Windows 根据选项替换 Torch 变体；按交付 wheel 的真实 SHA-256 重新生成 `requirements.lock`，不沿用源码包哈希。
4. 加入独立 Python、uv 0.13.6、imageio-ffmpeg 0.6.0 发行材料中的 FFmpeg、许可证和来源说明。Python、环境与模型互不混装；包内没有预先创建的虚拟环境。
5. 生成普通文件 ZIP64，拒绝符号链接。使用客户端同一个解包器和离线安装器，在包含中文和空格的路径重建环境。ASR 使用工程 Electron 导入依赖；GSV 导入原生推理依赖、检查依赖闭包、执行 FFmpeg，CUDA 检查设备可用性。
6. macOS 扫描 Mach-O 引用，拒绝依赖 Homebrew、用户目录等非系统绝对路径。
7. 验收通过后生成归档哈希、展开大小和实测安装空间预算，最后更新发布声明。

`--keep-work` 保留解包安装结果便于排查，默认清理可重建的阶段目录。下载缓存仍保留，可在没有构建进程时删除 `.runtime-build/` 释放空间。异常强制终止可能留下 `build.lock`；先确认其中 PID 已退出，再删除该锁，不能并行运行两个构建任务。

若归档已经生成、后续验收失败，可以保留工作目录，使用同一组目标/版本参数追加 `--verify-candidate`，只重试工作目录中的候选归档。该选项与 `--verify-existing` 不同时使用。重新验收会先核对 SHA-256，再检查归档内的目标与版本。

## 真实模型验收

基础构建不把“依赖导入成功”当作模型推理成功；`.build.json` 的 `validation.realInference` 会明确记录是否做过真实推理。

ASR 可在构建或 `--verify-existing` 时追加：

```sh
node scripts/runtime/build.mjs --kind asr --verify-existing --asr-model "本地 Whisper 模型目录" --asr-audio "用于验收的语音.wav" --asr-language zh
```

此步骤禁止远端模型下载，使用 CPU q8 推理，必须返回非空文本。`--asr-language` 与音频语言匹配，例如 `zh` 或 `ja`；省略时使用当前 Transformers/Whisper 的默认语言行为。

GSV 可以重复提供 `--gsv-config`，分别指定 v4、v2ProPlus 的现有真实模型测试清单；清单格式与 `python/tests/test_gpt_sovits_inference.py` 使用的 `DSAKIKO_TEST_SPEECH_CONFIG` 相同，模型路径全部为绝对路径。通过 `--nltk-data` 和 `--openjtalk-dict` 显式指定词典：

```sh
node scripts/runtime/build.mjs --kind gsv --verify-existing --gsv-config "v4.json" --gsv-config "v2proplus.json" --nltk-data "NLTK 数据目录" --openjtalk-dict "open_jtalk_dic_utf_8-1.11 目录"
```

现有真实模型测试会生成中日文音频并检查采样率、非空内容；它使用 CPU。Windows CUDA 还需在应用中确认实际 CUDA 合成。签名、公证、干净机器首次启动和最低系统版本验收，仍是正式发布前的额外检查。

## 输出和上传

输出统一位于 `dist/runtimes/`：

```text
asr-darwin-arm64.zip
gsv-darwin-arm64-cpu.zip
asr-win32-x64.zip
gsv-win32-x64-cpu.zip
gsv-win32-x64-cuda.zip
*.build.json
release.json
```

只上传该次已验收的 ZIP 与合并后的 `release.json` 到 `Jerry4787/custom-gsv-model/runtimes/`。`.build.json` 是构建记录，建议保留，客户端不读取。Mac、Windows 的声明要按 `(kind, target)` 合并，不可用 Windows 的文件覆盖掉 Mac 的条目。先上传 ZIP，再上传合并声明，不建立 revision 目录。

可以把两台机器的产物分别放到 `mac-packages/` 和 `windows-packages/`，运行：

```sh
node scripts/runtime/merge-releases.mjs mac-packages/release.json windows-packages/release.json --output dist/runtimes
```

合并脚本先校验所有 ZIP 的大小和 SHA-256，再复制归档并写入合并声明。同一目标的不同构建会报错，需要明确保留哪个版本，不静默覆盖。

发布前确认第三方许可证和相应源码提供义务已经满足，尤其是 FFmpeg 发行构建。脚本保留其许可证、构建来源信息，不自动代替发布者完成源码分发。

## 2026-11-02 本机结果

- 已在当前 Apple Silicon Mac 生成 ASR、GSV CPU 两个包，位于 `dist/runtimes/`；随后通过 ModelScope CLI 上传到 `Jerry4787/custom-gsv-model/runtimes/`。两个 ZIP 经后端下载入口完整回读并通过大小、SHA-256 校验后，最后上传 `release.json`。Windows 包尚未发布。
- ASR 使用工程 Electron 与现有 Whisper Small q8 模型，指定日语完成真实离线识别。
- GSV 在含中文、空格的路径由客户端安装器离线安装 101 个依赖；v2ProPlus、v4 均通过中日文真实音频合成测试。原生库路径检查覆盖 298 个 Mach-O 文件。
- 新环境 Python 测试 39 项，38 项通过、1 项条件跳过；上述真实模型测试另行显式执行并通过。资源后端 13 项测试、ESLint、Python 3.9 语法与差异检查通过。
- 精确大小、SHA-256 与构建记录见产物旁的 `release.json`、`*.build.json` 和 `BUILD-RESULTS.md`。Windows 脚本尚未在 Windows 实机执行；签名/公证应用、最低系统版本仍需正式发布验收。
