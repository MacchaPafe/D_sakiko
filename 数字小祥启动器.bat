@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title 数字小祥 - 启动器

if not exist "%~dp0launcher\launcher.py" (
    echo 缺少启动器文件，请完整解压软件包后重试。
    pause
    exit /b 1
)

set "launcher_python="
if exist "%~dp0.venv\Scripts\python.exe" set "launcher_python=%~dp0.venv\Scripts\python.exe"
if exist "%~dp0.venv\Scripts\pythonw.exe" set "launcher_python=%~dp0.venv\Scripts\pythonw.exe"
if exist "%~dp0runtime\python.exe" set "launcher_python=%~dp0runtime\python.exe"
if exist "%~dp0runtime\pythonw.exe" set "launcher_python=%~dp0runtime\pythonw.exe"
if not defined launcher_python (
    echo 未找到软件包运行环境。请完整解压 Windows 软件包，或先安装项目的 .venv 环境。
    pause
    exit /b 1
)

"%ComSpec%" /d /c exit 0
start "" /d "%~dp0" "%launcher_python%" "%~dp0launcher\launcher.py"
if errorlevel 1 (
    echo 启动器打开失败，请检查运行环境与文件权限。
    pause
    exit /b 1
)
exit /b 0
