#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "未找到虚拟环境。请先双击运行同文件夹下的 install_macos.command。"
    exit 1
fi

source .venv/bin/activate
python GPT_SoVITS/live2d_downloader_ui.py
