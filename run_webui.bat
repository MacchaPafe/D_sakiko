@echo off
cd /d "%~dp0"

if exist "runtime\python.exe" (
    "runtime\python.exe" -m dsakiko_webui.backend.main
) else (
    ".venv\Scripts\python.exe" -m dsakiko_webui.backend.main
)

pause
