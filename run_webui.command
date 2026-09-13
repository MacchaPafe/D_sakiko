#!/bin/zsh

cd "$(dirname "$0")" || exit 1
exec .venv/bin/python -m dsakiko_webui.backend.main --open-pairing
