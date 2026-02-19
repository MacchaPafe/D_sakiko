#!/bin/bash

set -e  # Exit immediately if a command exits with a non-zero status.
set -o pipefail
cd "$(dirname "$0")"

echo "========================================="
echo "   数字小祥 macOS 安装脚本"
echo "========================================="

# Function to prompt user
ask_yes_no() {
    while true; do
        read -p "$1 [y/n]: " yn
        case $yn in
            [Yy]* ) return 0;;
            [Nn]* ) return 1;;
            * ) echo "请输入 yes 或 no。";;
        esac
    done
}

die() {
    echo "错误：$*" >&2
    exit 1
}

# Enforce Apple Silicon only (arm64)
ARCH="$(uname -m)"
if [[ "$ARCH" != "arm64" ]]; then
    echo "错误：当前仅支持 Apple Silicon (arm64) 的 macOS。检测到架构：$ARCH"
    echo "如果你使用的是 Intel Mac（x86_64），当前版本不再提供支持。"
    exit 1
fi

# Detect region/Ask user
IS_CN=false
if ask_yes_no "您是否位于中国大陆？（这将配置镜像源以加速下载）"; then
    IS_CN=true
    echo "正在配置中国大陆镜像源..."
    
    # Set Homebrew mirrors for the current session
    export HOMEBREW_API_DOMAIN="https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles/api"
    export HOMEBREW_BOTTLE_DOMAIN="https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles"
    export HOMEBREW_BREW_GIT_REMOTE="https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/brew.git"
    export HOMEBREW_CORE_GIT_REMOTE="https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/homebrew-core.git"
    export HOMEBREW_PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
    
    # Set uv mirror for PyPI
    export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
    # export UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple" # Older uv versions might use this
    
    echo "当前会话镜像源已配置。"
fi

# 1. Check/Install Homebrew
if ! command -v brew &> /dev/null; then
    echo "未检测到 Homebrew。正在安装..."
    
    # 优先使用本地脚本
    if [ -f "install_brew.sh" ]; then
        echo "发现本地安装脚本 install_brew.sh，正在运行..."
        chmod +x install_brew.sh
        ./install_brew.sh
    else
        if [ "$IS_CN" = true ]; then
            echo "警告：在中国大陆，标准 Homebrew 安装可能会因网络问题失败。"
            echo "如果下方安装失败，请使用国内镜像脚本手动安装 Homebrew，"
            echo "例如：/bin/zsh -c \"\$(curl -fsSL https://gitee.com/cunkai/HomebrewCN/raw/master/Homebrew.sh)\""
            echo "按任意键尝试标准安装..."
            read -n 1
        fi
        
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    
    # Configure shellenv for immediate use
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -f /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
else
    echo "Homebrew 已安装。"
fi

# Update brew (optional, but good practice)
# echo "Updating Homebrew..."
# brew update

# 2. Install Python 3.11
echo "正在安装 Python 3.11..."
brew install python@3.11

# 3. Install uv
echo "正在安装 uv..."
brew install uv

# 4. Install Dependencies (prefer local build if toolchain exists)
echo "正在安装项目依赖..."
cd "$(dirname "$0")"

# Versions (can be overridden by env)
PYOPENJTALK_VERSION="${PYOPENJTALK_VERSION:-0.4.1}"
OPENCC_VERSION="${OPENCC_VERSION:-1.1.9}"

# Local wheels directory (optional)
WHEELS_DIR="${PYOPENJTALK_WHEEL_DIR:-$(pwd)/wheels}"

resolve_python311() {
    local py311_path
    py311_path="$(brew --prefix python@3.11 2>/dev/null)/bin/python3.11"
    if [[ -x "$py311_path" ]]; then
        echo "$py311_path"
        return 0
    fi
    if command -v python3.11 >/dev/null 2>&1; then
        command -v python3.11
        return 0
    fi
    return 1
}

PY311=""
if PY311="$(resolve_python311)"; then
    :
else
    die "未找到 python3.11，请确认 Homebrew 安装的 python@3.11 可用。"
fi

echo "正在创建/校验虚拟环境：.venv（Python 3.11）..."
uv venv -p "$PY311" .venv --allow-existing

if [[ ! -x ".venv/bin/python" ]]; then
    die "虚拟环境创建失败：未找到 .venv/bin/python"
fi

has_xcode_clt() {
    # Xcode Command Line Tools are considered available if xcode-select path exists and clang is in PATH.
    xcode-select -p >/dev/null 2>&1 && command -v clang >/dev/null 2>&1
}

wheel_available() {
    local pattern="$1"
    compgen -G "$pattern" >/dev/null 2>&1
}

# Detect whether required local wheels exist
PYOPENJTALK_WHEEL_GLOB_ARM64="$WHEELS_DIR/pyopenjtalk-${PYOPENJTALK_VERSION}-*cp311*-macosx_*_arm64.whl"
PYOPENJTALK_WHEEL_GLOB_UNIVERSAL2="$WHEELS_DIR/pyopenjtalk-${PYOPENJTALK_VERSION}-*cp311*-macosx_*_universal2.whl"

OPENCC_WHEEL_GLOB_ARM64="$WHEELS_DIR/[oO]pen[Cc][Cc]-${OPENCC_VERSION}-*cp311*-macosx_*_arm64.whl"
OPENCC_WHEEL_GLOB_UNIVERSAL2="$WHEELS_DIR/[oO]pen[Cc][Cc]-${OPENCC_VERSION}-*cp311*-macosx_*_universal2.whl"

PYOPENJTALK_WHEEL_OK=false
OPENCC_WHEEL_OK=false

if [[ -d "$WHEELS_DIR" ]]; then
    if wheel_available "$PYOPENJTALK_WHEEL_GLOB_ARM64" || wheel_available "$PYOPENJTALK_WHEEL_GLOB_UNIVERSAL2"; then PYOPENJTALK_WHEEL_OK=true; fi
    if wheel_available "$OPENCC_WHEEL_GLOB_ARM64" || wheel_available "$OPENCC_WHEEL_GLOB_UNIVERSAL2"; then OPENCC_WHEEL_OK=true; fi
fi

WHEELS_OK=false
if [[ "$PYOPENJTALK_WHEEL_OK" == "true" && "$OPENCC_WHEEL_OK" == "true" ]]; then
    WHEELS_OK=true
fi

# Control: set FORCE_WHEELS=1 to always prefer bundled wheels when available.
FORCE_WHEELS="${FORCE_WHEELS:-0}"

USE_WHEELS=false
USE_LOCAL_BUILD=false

if [[ "$FORCE_WHEELS" == "1" && "$WHEELS_OK" == "true" ]]; then
    USE_WHEELS=true
elif has_xcode_clt; then
    # Prefer local compilation when toolchain exists.
    USE_LOCAL_BUILD=true
elif [[ "$WHEELS_OK" == "true" ]]; then
    # No toolchain, but wheels exist.
    USE_WHEELS=true
else
    # Neither toolchain nor wheels. Offer installing Xcode Command Line Tools.
    echo "未检测到可用的编译环境（Xcode 命令行工具），且本地 wheels 不完整。"
    echo "- 需要的 wheels："
    echo "  - $PYOPENJTALK_WHEEL_GLOB_ARM64"
    echo "  - $PYOPENJTALK_WHEEL_GLOB_UNIVERSAL2"
    echo "  - $OPENCC_WHEEL_GLOB_ARM64"
    echo "  - $OPENCC_WHEEL_GLOB_UNIVERSAL2"
    if ask_yes_no "是否现在安装 Xcode 命令行工具？（安装完成后可直接本地编译）"; then
        echo "正在触发 Xcode 命令行工具安装（将弹出系统安装窗口）..."
        xcode-select --install || true
        echo "请完成弹窗中的安装。安装完成后按回车继续检查。"
        read -r
        if has_xcode_clt; then
            USE_LOCAL_BUILD=true
        else
            echo "仍未检测到 Xcode 命令行工具。你可以："
            echo "完成安装后重新运行本脚本"
            exit 1
        fi
    else
        echo "已取消安装。"
        exit 1
    fi
fi

if [[ "$USE_LOCAL_BUILD" == "true" ]]; then
    echo "检测到编译环境：将优先本地编译安装（无需依赖本地 wheels）。"
    echo "正在确保构建依赖（cmake）已安装..."
    brew install cmake ninja
    echo "正在运行 uv sync（可能会编译 C/C++ 扩展，耗时较长）..."
    # 使用已创建的 .venv，避免 uv 选到其它 Python/环境
    source .venv/bin/activate
    uv sync --active
elif [[ "$USE_WHEELS" == "true" ]]; then
    echo "将使用本地 wheels 加速安装（避免本机编译）..."
    if [[ ! -d "$WHEELS_DIR" ]]; then
        echo "错误：未找到本地 wheels 目录：$WHEELS_DIR"
        exit 1
    fi
    echo "正在运行 uv sync..."
    source .venv/bin/activate
    uv sync --active --find-links "$WHEELS_DIR"
else
    echo "内部错误：未选择安装策略。"
    exit 1
fi

# 5. Handle macOS Gatekeeper for .so files
# ===========================================
#  自动修复 .so 文件的权限与签名 (关键步骤)
# ===========================================
echo "--> 正在验证组件安全签名..."

if compgen -G "GPT_SoVITS/*.so" >/dev/null 2>&1; then
    for SO_FILE in GPT_SoVITS/*.so; do
        # 移除 "com.apple.quarantine" 属性
        # 这可以防止弹出 "无法验证开发者" 的拦截弹窗
        xattr -d com.apple.quarantine "$SO_FILE" 2>/dev/null || true

        # 重新进行 Ad-hoc 签名
        # -s - 表示使用本地临时签名
        # -f 表示强制覆盖原有签名
        # 这解决了 Apple Silicon 上 "Killed: 9" 的崩溃问题
        codesign -s - -f "$SO_FILE"
    done
else
    echo "⚠️  未找到需要签名的 .so 文件（GPT_SoVITS/*.so），跳过签名步骤。"
fi

echo "✅ 组件验证完成"

# 一个包的数据文件需要从 GitHub 下载
# 为了避免访问 GitHub 过慢，这里改为手动下载并复制  
echo "正在复制数据文件与补丁..."

VENV_PY=".venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    die "未找到虚拟环境 Python：$VENV_PY"
fi

PYOPENJTALK_DIR=""
if PYOPENJTALK_DIR="$($VENV_PY -c 'import pyopenjtalk, os; print(os.path.dirname(pyopenjtalk.__file__))' 2>/dev/null)"; then
    :
else
    die "无法定位 pyopenjtalk 安装路径，请确认依赖已成功安装。"
fi

if [[ ! -d "install_patch/open_jtalk_dic_utf_8-1.11" ]]; then
    die "缺少补丁数据目录：install_patch/open_jtalk_dic_utf_8-1.11"
fi

rm -rf "$PYOPENJTALK_DIR/open_jtalk_dic_utf_8-1.11"
cp -R "install_patch/open_jtalk_dic_utf_8-1.11" "$PYOPENJTALK_DIR/"

# 腮红发黑的修复
LIVE2D_DIR=""
if LIVE2D_DIR="$($VENV_PY -c 'import live2d, os; print(os.path.dirname(live2d.__file__))' 2>/dev/null)"; then
    :
else
    die "无法定位 live2d 安装路径，请确认依赖已成功安装。"
fi

TARGET_DRAW_PY="$LIVE2D_DIR/v2/core/graphics/draw_param_opengl.py"
if [[ ! -f "install_patch/draw_param_opengl.py" ]]; then
    die "缺少补丁文件：install_patch/draw_param_opengl.py"
fi
if [[ ! -f "$TARGET_DRAW_PY" ]]; then
    die "未找到目标文件（live2d 包结构可能变化）：$TARGET_DRAW_PY"
fi
cp "install_patch/draw_param_opengl.py" "$TARGET_DRAW_PY"
echo "数据文件复制完成。"

echo "========================================="
echo "安装完成！"
echo "========================================="
echo "启动程序方法："
echo "双击目录下的 运行主程序.command 脚本文件"
echo ""
