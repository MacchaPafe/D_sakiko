"""在 macOS Terminal 中运行程序，以状态文件跟踪真实退出码。"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time


class TerminalProcess:
    def __init__(self, opener, status_path):
        self.opener = opener
        self.status_path = status_path
        self.started = time.monotonic()
        self.returncode = None

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        try:
            state = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            code = self.opener.poll()
            if code not in (None, 0):
                self.returncode = code
            elif time.monotonic() - self.started > 120:
                self.returncode = 1
            return self.returncode
        if "returncode" in state:
            self.returncode = int(state["returncode"])
        else:
            try:
                os.kill(int(state["pid"]), 0)
            except ProcessLookupError:
                self.returncode = 1
            except PermissionError:
                pass
        return self.returncode


def start_in_terminal(command, cwd, root, log_path):
    request = log_path.with_suffix(".request.json")
    status = log_path.with_suffix(".status.json")
    request.write_text(json.dumps({"command": command, "cwd": str(cwd),
        "root": str(root), "status": str(status)}, ensure_ascii=False), encoding="utf-8")
    # 两层独立转义：shell 使用 shlex；AppleScript 通过 argv 接收。
    shell_command = shlex.join([command[0], str(root / "launcher/launcher_macos.py"), "--run", str(request)])
    script = 'on run argv\ntell application "Terminal"\nactivate\ndo script (item 1 of argv)\nend tell\nend run'
    with log_path.open("a", encoding="utf-8") as log:
        opener = subprocess.Popen(["/usr/bin/osascript", "-e", script, shell_command],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
            start_new_session=True, close_fds=True)
    return TerminalProcess(opener, status)


def run_request(request_path):
    request = json.loads(request_path.read_text(encoding="utf-8"))
    status = Path(request["status"])

    def publish(state):
        temporary = status.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(status)

    publish({"pid": os.getpid()})
    env = os.environ.copy()
    venv = str(Path(request["root"]) / ".venv")
    env.update(VIRTUAL_ENV=venv, PYTHONIOENCODING="utf-8")
    env["PATH"] = str(Path(venv) / "bin") + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONHOME", None)
    child = None
    code = 1

    def terminate(signum, frame):
        if child is not None and child.poll() is None:
            child.send_signal(signum)

    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, terminate)
    try:
        child = subprocess.Popen(request["command"], cwd=request["cwd"], env=env)
        while True:
            try:
                code = child.wait()
                break
            except KeyboardInterrupt:
                # Terminal 已向前台进程组发送 SIGINT，等待程序清理。
                continue
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
    finally:
        publish({"pid": os.getpid(), "returncode": code})
    return code


if __name__ == "__main__":
    raise SystemExit(run_request(Path(sys.argv[2])))
