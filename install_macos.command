#!/bin/bash

set -e  # Exit immediately if a command exits with a non-zero status.
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

# 4. Install Dependencies
echo "正在安装项目依赖..."
cd "$(dirname "$0")"

# Ensure uv uses the installed python 3.11 if possible, or downloads one.
# uv sync will create .venv and install dependencies from pyproject.toml
echo "正在运行 uv sync..."
uv sync

# 5. Handle macOS Gatekeeper for .so files
# ===========================================
#  自动修复 .so 文件的权限与签名 (关键步骤)
# ===========================================
echo "--> 正在验证组件安全签名..."
# 在脚本中临时关闭错误退出，以确保所有文件都能处理
set +e
SO_FILE="GPT_SoVITS/live2d_1.cpython-311-darwin.so"
# 移除 "com.apple.quarantine" 属性
# 这可以防止弹出 "无法验证开发者" 的拦截弹窗
# 2>/dev/null 这里的用意是如果文件本来就没属性，报错也不显示
xattr -d com.apple.quarantine "$SO_FILE" 2>/dev/null

# 重新进行 Ad-hoc 签名
# -s - 表示使用本地临时签名
# -f 表示强制覆盖原有签名
# 这解决了 Apple Silicon 上 "Killed: 9" 的崩溃问题
codesign -s - -f "$SO_FILE"

echo "✅ 组件验证完成"

# 一个包的数据文件需要从 GitHub 下载
# 为了避免访问 GitHub 过慢，这里改为手动下载并复制  
echo "正在复制数据文件与补丁..."
cp -r 'install_patch/open_jtalk_dic_utf_8-1.11' '.venv/lib/python3.11/site-packages/pyopenjtalk/open_jtalk_dic_utf_8-1.11'
# 腮红发黑的修复
cp "install_patch/draw_param_opengl.py" ".venv/lib/python3.11/site-packages/live2d/v2/core/graphics/draw_param_opengl.py"
echo "数据文件复制完成。"

echo "========================================="
echo "安装完成！"
echo "========================================="
echo "启动程序方法："
echo "双击目录下的 run_macos.command 脚本文件"
echo ""
