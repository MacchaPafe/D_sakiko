#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "未找到虚拟环境。请先双击运行同文件夹下的 安装程序.command。"
    exit 1
fi

source .venv/bin/activate
python tools/apply_update_patch.py --app-root "$PWD"