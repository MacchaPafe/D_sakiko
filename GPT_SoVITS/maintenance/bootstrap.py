from __future__ import annotations

import subprocess
import sys
import traceback
from pathlib import Path


def recover_before_startup(root: Path) -> None:
    """通过独立进程尝试恢复，失败仍允许主程序继续启动。"""
    if not (root / '.updates' / 'transactions').is_dir():
        return
    log = root / 'logs' / 'update' / 'startup_recovery.log'
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a', encoding='utf-8') as stream:
            result = subprocess.run([sys.executable, str(root / 'tools' / 'recover.py'),
                                     '--app-root', str(root)], stdout=stream, stderr=stream, check=False)
        if result.returncode:
            print(f'部分文件未能恢复，将继续启动。恢复日志：{log}')
    except Exception:
        print('自动恢复未能执行，将继续启动。')
        traceback.print_exc()
