# 开发指南

:::tip 提示
本页面主要介绍如何搭建一个可以修改代码，开发数字小祥的环境。如果你只想使用软件而不想修改代码，请参考 [快速开始](../guide/start.md) 直接安装打包好的程序。
:::

本程序的资源主要分为三部分：
1. **程序代码**：程序自身的源代码。
2. **GPT-SoVITS 基础语音模型**：用于模拟合成角色声音的模型文件。
3. **live2d 模型与角色语音模型**：显示每个角色所需的 live2d 文件和语音模型文件。

接下来会详细介绍如何获得这些代码与资源。

## 完整环境配置流程

### 第一部分：获取源代码并安装依赖库

1. 打开 [数字小祥仓库](https://github.com/MacchaPafe/D_sakiko)，点击 fork 按钮，克隆一份仓库到你自己的 GitHub 账号下。
2. 克隆你的 fork 仓库到本地。例如，如果你的仓库网址是 https://github.com/example/D_sakiko ，你可以使用以下命令：
   ```bash
   git clone https://github.com/example/D_sakiko
   ```
3. 确保你已经安装了 uv；如果没有，请参照 [uv 安装教程](https://docs.astral.sh/uv/getting-started/installation/)（[中文版](https://uv.doczh.com/getting-started/installation/)）先安装 uv。
4. 在项目目录下新建一个终端。如果你在 Windows 上配置程序，且你的计算机有 Nvidia 显卡，请运行如下命令来安装依赖：
    ```bash
    uv sync --extra cu128
    ```
    否则，请使用如下命令：
    ```bash
    uv sync --extra cpu
    ```
5. **如果你在 Windows 上配置程序**，请继续执行下面的命令来强制修改部分依赖库的版本：
    ```bash
    uv pip install --force-reinstall "ctranslate2<4" "numpy>=2.3,<2.4"
    ```
    之后，每当你运行 `uv sync` 或 `uv run`，你都必须再运行一次上述命令，因为 Windows 上的 ctranslate2 版本兼容性问题会导致 uv 自动安装不兼容版本的依赖。

    > 如果在激活虚拟环境时用 `python GPT_SoVITS/main2.py` 启动程序，则不需要重新执行该命令，因为这种情况下 uv 不会对依赖做版本检查。

### 第二部分：获取 GPT-SoVITS 基础语音模型

如果你正在使用 Windows 系统，请确保你已经安装了 conda。如果没有，请参考 [miniconda 教程](https://www.anaconda.com/docs/getting-started/miniconda/main) 来安装 miniconda。安装后，请运行如下命令来下载基础语音模型：

```powershell
.venv/Scripts/activate.ps1
./gpt_sovits_install.ps1 ModelScope
```

如果你正在使用 macOS 系统，请确保你已经安装了 brew。如果没有，请参考 [Homebrew 教程](https://brew.sh/zh-cn/) 来安装 Homebrew。安装完成后，请在项目目录下运行：

```bash
chmod +x ./gpt_sovits_install.sh
source .venv/bin/activate
./gpt_sovits_install.sh --source ModelScope
```

这两个脚本做了相同的工作：它们会从 ModelScope（魔搭社区）下载 GPT_SoVITS 基础语音模型，并将其解压到对应的位置。

:::tip 提示
如果脚本无法下载模型或者下载速度太慢，你可以前往 [GPT-SoVITS ModelScope](https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/) 页面，手动下载 pretrained_models.zip、G2PWModel.zip 两个文件。

随后，将 pretrained_models.zip 的内容解压到 GPT_SoVITS/pretrained_models/ 目录下，将 G2PWModel.zip 的内容解压到 GPT_SoVITS/text/G2PWModel/ 目录下，即可完成模型的安装，不需要再运行脚本了。
:::

### 第三部分：获取 live2d 模型与角色语音模型

请下载默认的 live2d 模型和角色语音模型：

- live2d 模型：[下载链接](https://gh-release.xjtutoolbox.com/?file=static/live2d_related.zip)
- 角色语音模型：[下载链接](https://gh-release.xjtutoolbox.com/?file=static/reference_audio.zip)
- 程序自带字体：[下载链接](https://gh-release.xjtutoolbox.com/?file=static/font.zip)

下载后，你应该会得到三个文件，live2d_related.zip，reference_audio.zip 和 font.zip。请将这三个文件的内容分别解压到项目根目录下的 live2d_related/、reference_audio/ 和 fonts/ 目录下。

### 第四部分：程序内配置

由于你正在运行源代码版本的程序，你需要使用自己的 API Key 来调用大模型。请首先配置你的 API Key，以 DeepSeek API 为例：

> 在下面配置和运行程序前，请务必保证自己已经激活了程序的虚拟环境。
> Windows 用户运行：
> ```powershell
> .venv/Scripts/activate.ps1
> ```
> macOS 用户运行：
> ```bash
> source .venv/bin/activate
> ```

请运行如下命令来打开启动参数配置程序：

```bash
python GPT_SoVITS/dsakiko_configuration.py
```

在“大模型 API 配置”页面，选择下拉框中的 "DeepSeek"，并在输入框内填入你的 API Key。在模型名称部分，请填写“deepseek-v4-flash"或者"deepseek-v4-pro"（不包含引号）。
完成后点击“保存配置”按钮，程序会将你的 API Key 和模型名称保存到配置文件中。之后你就可以正常运行程序了。

### 第五部分：启动程序并测试

在激活了虚拟环境的情况下，运行如下命令来启动程序：

```bash
python GPT_SoVITS/main2.py
```

如果一切顺利，你应该会看到程序窗口弹出，并且小祥会说话了！你可以尝试和小祥进行对话，看看她的反应。