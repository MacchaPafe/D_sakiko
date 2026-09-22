"""启动器的入口目录与进程管理；不导入聊天、语音或 Live2D 模型。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys


@dataclass(frozen=True)
class LaunchEntry:
    key: str
    title: str
    description: str
    script: str
    icon: str
    group: str


ENTRIES = (
    LaunchEntry("desktop", "桌面端", "与角色聊天，显示桌面 Live2D。", "GPT_SoVITS/main2.py", "CHAT", "聊天与演出"),
    LaunchEntry("webui", "WebUI（手机端）", "打开配对页面，用手机或浏览器连接。", "dsakiko_webui/backend/main.py", "PHONE", "聊天与演出"),
    LaunchEntry("theater", "小剧场模式", "让多个角色一起参与对话与演出。", "GPT_SoVITS/multi_char_main.py", "PEOPLE", "聊天与演出"),
    LaunchEntry("config", "启动参数配置", "管理角色、API、语音合成与个性化设置。", "GPT_SoVITS/dsakiko_configuration.py", "SETTING", "配置与模型"),
    LaunchEntry("downloader", "Live2D 模型下载器", "为已有角色添加服装，或下载新角色模型。", "GPT_SoVITS/live2d_downloader_ui.py", "DOWNLOAD", "配置与模型"),
    LaunchEntry("editor", "动作组编辑器", "预览 Live2D 动作，编辑角色动作组。", "GPT_SoVITS/live2d_viewer.py", "EDIT", "配置与模型"),
    LaunchEntry("update", "手动更新", "选择已解压的官方更新包。", "tools/apply_update_patch.py", "UPDATE", "程序维护"),
)
ENTRY_BY_KEY = {entry.key: entry for entry in ENTRIES}


def console_python(executable: str) -> Path:
    """子进程保留标准输出，使用当前环境中的控制台解释器。"""
    path = Path(executable)
    if path.name.lower() == "pythonw.exe":
        path = path.with_name("python.exe")
    if not path.is_file():
        raise FileNotFoundError(f"缺少 Python 运行环境：{path}")
    return path


def build_command(
    key: str, root: Path, executable: str, *, package: Path | None = None,
    wait_pid: int | None = None,
) -> tuple[list[str], Path]:
    """所有入口直接启动 Python 程序，不经 CMD，也不依赖旧 BAT。"""
    entry = ENTRY_BY_KEY[key]
    root = root.resolve()
    script = root / entry.script
    if not script.is_file():
        raise FileNotFoundError(f"缺少程序文件：{script}\n请完整解压软件包，或使用程序文件修复功能。")
    python = str(console_python(executable))
    if key == "webui":
        # 兼容整合包的 python._pth 隔离模式，与原 run_webui.bat 一致。
        return [python, "-u", "-c", "import os, runpy, sys; sys.path.insert(0, os.getcwd()); "
                "runpy.run_module('dsakiko_webui.backend.main', run_name='__main__')",
                "--open-pairing"], root
    command = [python, "-u", str(script)]
    if key == "update":
        if package is None or not all((package / name).is_file() for name in ("manifest.json", "patch.hdiff")):
            raise ValueError("请选择已解压的更新包目录，其中应包含 manifest.json 和 patch.hdiff。")
        if wait_pid is None or wait_pid <= 0:
            raise ValueError("更新前需要等待启动器退出。")
        command += ["--app-root", str(root), "--package", str(package.resolve()),
                    "--wait-pid", str(wait_pid),
                    "--status-file", str(root / "logs/update/last_update_result.json"),
                    "--restart-command", json.dumps([executable, str(root / "launcher/launcher.py")])]
        return command, root
    return command, root if sys.platform == "darwin" else root / "GPT_SoVITS"


@dataclass
class RunningEntry:
    process: subprocess.Popen
    log_path: Path


class LauncherProcesses:
    """跟踪本窗口启动的进程；关闭窗口时让子程序继续运行。"""

    def __init__(self, root: Path, executable: str | None = None):
        self.root = root.resolve()
        self.executable = executable or (str(self.root / ".venv/bin/python") if sys.platform == "darwin" else sys.executable)
        self.running: dict[str, RunningEntry] = {}

    def blocked_reason(self, key: str) -> str | None:
        previous = self.running.get(key)
        if previous and previous.process.poll() is None:
            return "这个程序已从当前启动器打开，请先关闭它的窗口。"
        other_key = {"desktop": "webui", "webui": "desktop"}.get(key)
        other = self.running.get(other_key)
        if other and other.process.poll() is None:
            return f"请先关闭{ENTRY_BY_KEY[other_key].title}，桌面端与 WebUI 不能同时运行。"
        return None

    def start(self, key: str, *, package: Path | None = None) -> Path:
        reason = self.blocked_reason(key)
        if reason:
            raise RuntimeError(reason)
        if key == "update" and any(item.process.poll() is None for item in self.running.values()):
            raise RuntimeError("请先关闭当前启动器打开的程序，再执行更新。")
        command, cwd = build_command(key, self.root, self.executable, package=package, wait_pid=os.getpid())
        log_dir = self.root / "logs/launcher"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{key}_{datetime.now():%Y%m%d_%H%M%S_%f}.log"
        # 独立控制台直接接收输入、输出；启动器退出不会关闭子程序。
        options = {"creationflags": subprocess.CREATE_NEW_CONSOLE} if os.name == "nt" else {"start_new_session": True}
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        # 日志只记录启动信息，运行输出交给程序自己的控制台。
        log_path.write_text(f"工作目录：{cwd}\n命令：{subprocess.list2cmdline(command)}\n"
                            "运行输出显示在独立终端中。\n", encoding="utf-8")
        if sys.platform == "darwin":
            from launcher_macos import start_in_terminal
            process = start_in_terminal(command, cwd, self.root, log_path)
        else:
            process = subprocess.Popen(command, cwd=str(cwd), close_fds=True, env=env, **options)
        self.running[key] = RunningEntry(process, log_path)
        return log_path

    def finished(self) -> list[tuple[str, int, Path]]:
        results = []
        for key, item in list(self.running.items()):
            code = item.process.poll()
            if code is not None:
                try:
                    with item.log_path.open("a", encoding="utf-8") as output:
                        output.write(f"退出时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
                                     f"退出码：{code}（0x{code & 0xFFFFFFFF:08X}）\n")
                except OSError:
                    # 日志不可写不能阻止释放入口。
                    pass
                results.append((key, code, item.log_path))
                del self.running[key]
        return results


def preferred_font(root: Path) -> Path:
    """固定使用随项目分发的字体，不受本机自定义字体设置影响。"""
    return root / "font" / "ft.ttf"
