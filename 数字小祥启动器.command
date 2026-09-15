#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "未找到 .venv/bin/python，请先安装 macOS 运行环境。"
    read -r -p "按回车关闭窗口..."
    exit 1
fi
source .venv/bin/activate
python launcher/launcher.py
