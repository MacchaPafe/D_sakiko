@echo off
cd /d "%~dp0"

if exist "runtime\python.exe" (
    "runtime\python.exe" -c "import os, runpy, sys; sys.path.insert(0, os.getcwd()); runpy.run_module('dsakiko_webui.backend.main', run_name='__main__')" --open-pairing
) else (
    ".venv\Scripts\python.exe" -m dsakiko_webui.backend.main --open-pairing
)

pause
