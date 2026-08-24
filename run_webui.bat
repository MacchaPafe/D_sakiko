@echo off
cd /d "%~dp0"

if exist "runtime\python.exe" (
    "runtime\python.exe" -m dsakiko_webui.backend.main --open-pairing
) else (
    ".venv\Scripts\python.exe" -m dsakiko_webui.backend.main --open-pairing
)

pause
