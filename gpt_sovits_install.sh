#!/bin/bash
# 来源于 https://github.com/RVC-Boss/GPT-SoVITS/blob/main/install.sh


# cd into GPT-SoVITS Base Path
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

cd "$SCRIPT_DIR" || exit 1

RESET="\033[0m"
BOLD="\033[1m"
ERROR="\033[1;31m[ERROR]: $RESET"
WARNING="\033[1;33m[WARNING]: $RESET"
INFO="\033[1;32m[INFO]: $RESET"
SUCCESS="\033[1;34m[SUCCESS]: $RESET"

set -eE
set -o errtrace

trap 'on_error $LINENO "$BASH_COMMAND" $?' ERR

# shellcheck disable=SC2317
on_error() {
    local lineno="$1"
    local cmd="$2"
    local code="$3"

    echo -e "${ERROR}${BOLD}Command \"${cmd}\" Failed${RESET} at ${BOLD}Line ${lineno}${RESET} with Exit Code ${BOLD}${code}${RESET}"
    echo -e "${ERROR}${BOLD}Call Stack:${RESET}"
    for ((i = ${#FUNCNAME[@]} - 1; i >= 1; i--)); do
        echo -e "  in ${BOLD}${FUNCNAME[i]}()${RESET} at ${BASH_SOURCE[i]}:${BOLD}${BASH_LINENO[i - 1]}${RESET}"
    done
    exit "$code"
}

run_brew() {
    brew install "$@" || {
        echo -e "${ERROR} Brew install failed"
        exit 1
    }
}

run_wget_quiet() {
    if wget --tries=25 --wait=5 --read-timeout=40 -q --show-progress "$@" 2>&1; then
        if [ "$WORKFLOW" = "false" ]; then
            tput cuu1 && tput el
        fi
    else
        echo -e "${ERROR} Wget failed"
        exit 1
    fi
}

if ! command -v brew &>/dev/null; then
    echo -e "${ERROR}Homebrew Not Found"
    exit 1
fi

WORKFLOW=${WORKFLOW:-"false"}

USE_HF=false
USE_HF_MIRROR=false
USE_MODELSCOPE=false
DOWNLOAD_UVR5=false

print_help() {
    echo "Usage: bash install.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --source   HF|HF-Mirror|ModelScope     Specify the model source (REQUIRED)"
    echo "  --download-uvr5                        Enable downloading the UVR5 model"
    echo "  -h, --help                             Show this help message and exit"
    echo ""
    echo "Examples:"
    echo "  bash install.sh --source HF --download-uvr5"
    echo "  bash install.sh --source ModelScope"
}

# Show help if no arguments provided
if [[ $# -eq 0 ]]; then
    print_help
    exit 0
fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
    --source)
        case "$2" in
        HF)
            USE_HF=true
            ;;
        HF-Mirror)
            USE_HF_MIRROR=true
            ;;
        ModelScope)
            USE_MODELSCOPE=true
            ;;
        *)
            echo -e "${ERROR}Error: Invalid Download Source: $2"
            echo -e "${ERROR}Choose From: [HF, HF-Mirror, ModelScope]"
            exit 1
            ;;
        esac
        shift 2
        ;;
    --download-uvr5)
        DOWNLOAD_UVR5=true
        shift
        ;;
    -h | --help)
        print_help
        exit 0
        ;;
    *)
        echo -e "${ERROR}Unknown Argument: $1"
        echo ""
        print_help
        exit 1
        ;;
    esac
done

if ! $USE_HF && ! $USE_HF_MIRROR && ! $USE_MODELSCOPE; then
    echo -e "${ERROR}Error: Download Source is REQUIRED"
    echo ""
    print_help
    exit 1
fi

# Install build tools
echo -e "${INFO}Detected system: $(uname -s) $(uname -r) $(uname -m)"
if [ "$(uname)" != "Darwin" ]; then
    echo -e "${WARNING} This script is optimized for macOS. Proceeding anyway..."
fi

if ! xcode-select -p &>/dev/null; then
    echo -e "${INFO}Installing Xcode Command Line Tools..."
    xcode-select --install
    echo -e "${INFO}Waiting For Xcode Command Line Tools Installation Complete..."
    while true; do
        sleep 20

        if xcode-select -p &>/dev/null; then
            echo -e "${SUCCESS}Xcode Command Line Tools Installed"
            break
        else
            echo -e "${INFO}Installing，Please Wait..."
        fi
    done
else
    XCODE_PATH=$(xcode-select -p)
    if [[ "$XCODE_PATH" == *"Xcode.app"* ]]; then
        echo -e "${WARNING} Detected Xcode path: $XCODE_PATH"
        echo -e "${WARNING} If your Xcode version does not match your macOS version, it may cause unexpected issues during compilation or package builds."
    fi
fi

echo -e "${INFO}Installing system dependencies via Homebrew..."
run_brew ffmpeg cmake make wget unzip
echo -e "${SUCCESS}System dependencies Installed"

if [ "$USE_HF" = "true" ]; then
    echo -e "${INFO}Download Model From HuggingFace"
    PRETRINED_URL="https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/pretrained_models.zip"
    G2PW_URL="https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/G2PWModel.zip"
    UVR5_URL="https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/uvr5_weights.zip"
    NLTK_URL="https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/nltk_data.zip"
    PYOPENJTALK_URL="https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/open_jtalk_dic_utf_8-1.11.tar.gz"
elif [ "$USE_HF_MIRROR" = "true" ]; then
    echo -e "${INFO}Download Model From HuggingFace-Mirror"
    PRETRINED_URL="https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/pretrained_models.zip"
    G2PW_URL="https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/G2PWModel.zip"
    UVR5_URL="https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/uvr5_weights.zip"
    NLTK_URL="https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/nltk_data.zip"
    PYOPENJTALK_URL="https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/open_jtalk_dic_utf_8-1.11.tar.gz"
elif [ "$USE_MODELSCOPE" = "true" ]; then
    echo -e "${INFO}Download Model From ModelScope"
    PRETRINED_URL="https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/pretrained_models.zip"
    G2PW_URL="https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/G2PWModel.zip"
    UVR5_URL="https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/uvr5_weights.zip"
    NLTK_URL="https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/nltk_data.zip"
    PYOPENJTALK_URL="https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/open_jtalk_dic_utf_8-1.11.tar.gz"
fi

if [ ! -d "GPT_SoVITS/pretrained_models/sv" ]; then
    echo -e "${INFO}Downloading Pretrained Models..."
    rm -rf pretrained_models.zip
    run_wget_quiet "$PRETRINED_URL"

    unzip -q -o pretrained_models.zip -d GPT_SoVITS
    rm -rf pretrained_models.zip
    echo -e "${SUCCESS}Pretrained Models Downloaded"
else
    echo -e "${INFO}Pretrained Model Exists"
    echo -e "${INFO}Skip Downloading Pretrained Models"
fi

if [ ! -d "GPT_SoVITS/text/G2PWModel" ]; then
    echo -e "${INFO}Downloading G2PWModel.."
    rm -rf G2PWModel.zip
    run_wget_quiet "$G2PW_URL"

    unzip -q -o G2PWModel.zip -d GPT_SoVITS/text
    rm -rf G2PWModel.zip
    echo -e "${SUCCESS}G2PWModel Downloaded"
else
    echo -e "${INFO}G2PWModel Exists"
    echo -e "${INFO}Skip Downloading G2PWModel"
fi

if [ "$DOWNLOAD_UVR5" = "true" ]; then
    if find -L "tools/uvr5/uvr5_weights" -mindepth 1 ! -name '.gitignore' | grep -q .; then
        echo -e"${INFO}UVR5 Models Exists"
        echo -e "${INFO}Skip Downloading UVR5 Models"
    else
        echo -e "${INFO}Downloading UVR5 Models..."
        rm -rf uvr5_weights.zip
        run_wget_quiet "$UVR5_URL"

        unzip -q -o uvr5_weights.zip -d tools/uvr5
        rm -rf uvr5_weights.zip
        echo -e "${SUCCESS}UVR5 Models Downloaded"
    fi
fi


PY_PREFIX=$(python -c "import sys; print(sys.prefix)")
PYOPENJTALK_PREFIX=$(python -c "import os, pyopenjtalk; print(os.path.dirname(pyopenjtalk.__file__))")

echo -e "${INFO}Downloading NLTK Data..."
rm -rf nltk_data.zip
run_wget_quiet "$NLTK_URL" -O nltk_data.zip
unzip -q -o nltk_data -d "$PY_PREFIX"
rm -rf nltk_data.zip
echo -e "${SUCCESS}NLTK Data Downloaded"

echo -e "${INFO}Downloading Open JTalk Dict..."
rm -rf open_jtalk_dic_utf_8-1.11.tar.gz
run_wget_quiet "$PYOPENJTALK_URL" -O open_jtalk_dic_utf_8-1.11.tar.gz
tar -xzf open_jtalk_dic_utf_8-1.11.tar.gz -C "$PYOPENJTALK_PREFIX"
rm -rf open_jtalk_dic_utf_8-1.11.tar.gz
echo -e "${SUCCESS}Open JTalk Dic Downloaded"


echo -e "${SUCCESS}Installation Completed"
