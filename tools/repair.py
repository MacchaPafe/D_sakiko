#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'GPT_SoVITS'))

from maintenance.process import setup_logging
from maintenance.transactions import recommended_version, recover_pending
from repair.repair_checker import check_integrity, get_configured_repair_base_urls, prepare_repair
from repair.repair_manifest import version_key

# 发行时维护；实际可用性仍由该平台的已签名远端清单确认。
BUILTIN_VERSIONS: tuple[str, ...] = ('3.5.0', '3.2.0')


def choose_version(recommendation: str | None) -> str:
    """推荐版本并允许按编号选择或手动输入版本。"""
    print('请选择当前程序的版本。你可以从这些版本中选择：')
    for index, version in enumerate(BUILTIN_VERSIONS, 1):
        print(f'  {index}. {version}')
    print()

    print('如果对版本选择有问题，请联系开发者，选择错误的版本可能导致程序完全损坏。')
    while True:
        prompt = f'检测到当前版本为 {recommendation}。你可以输入编号，或按下回车按 {recommendation} 版本修复：' if recommendation else '请输入编号或版本，如 "1", "3.2.0"：'
        value = input(prompt).strip()
        if not value and recommendation:
            return recommendation
        if value.isdigit() and 1 <= int(value) <= len(BUILTIN_VERSIONS):
            return BUILTIN_VERSIONS[int(value) - 1]
        try:
            version_key(value)
            return value
        except ValueError:
            print('请输入有效版本，例如 3.2.0。')


def confirm(prompt: str) -> bool:
    """通过一次简单交互确认操作，默认不执行。"""
    return input(prompt + ' [y/N]：').strip().lower() in {'y', 'yes', '是'}


def main() -> int:
    """不加载界面或模型，完成在线检查、下载与修复。"""
    parser = argparse.ArgumentParser(description='检查并修复数字小祥程序文件。')
    parser.add_argument('--app-root', default=str(ROOT))
    parser.add_argument('--version', help='明确指定修复版本。')
    parser.add_argument('--check', action='store_true', help='仅检查，不修改文件。')
    parser.add_argument('--recover-last-update', action='store_true', help='先再次尝试本地回滚。')
    args = parser.parse_args()
    root = Path(args.app_root).expanduser().resolve()
    log = root / 'logs' / 'repair' / f'cli_{datetime.now():%Y%m%d_%H%M%S_%f}.log'
    original_stdout, original_stderr = sys.stdout, sys.stderr
    handle = None
    try:
        handle = setup_logging(log)
        if args.recover_last_update:
            if args.check:
                raise ValueError('--check 不能与 --recover-last-update 同时使用')
            recover_pending(root, automatic=False)
        print(f"数字小祥修复程序")
        version = args.version or choose_version(recommended_version(root))
        version_key(version)
        print(f'正在核对版本 {version} 的程序文件...')
        result = check_integrity(root, get_configured_repair_base_urls(), version=version)
        if not result.candidates:
            print('无需修复。')
            return 0
        print('\n以下文件将被修复：')
        for candidate in result.candidates:
            print(f'  {candidate.entry.path}')
        print()
        print(f'需要修复 {len(result.candidates)} 个文件，预计下载 {result.total_download_size / 1024 / 1024:.2f} MB。如果你修改过这些文件，你的修改将丢失。')
        if args.check or not confirm('是否开始修复？请先关闭正在运行的数字小祥程序'):
            return 0
        prepared = prepare_repair(root, result)
        command = [sys.executable, str(ROOT / 'tools' / 'apply_repair.py'), '--app-root', str(root),
                   '--plan', str(prepared.plan_file), '--target-version', version]
        code = subprocess.run(command, check=False).returncode
        if code:
            print(f'修复失败，请查看日志：{root / "logs" / "repair"}')
            return code
        print('修复完成。')
        if confirm('是否现在启动程序？'):
            subprocess.Popen([sys.executable, str(root / 'GPT_SoVITS' / 'main2.py')], cwd=root)
        return 0
    except (KeyboardInterrupt, EOFError):
        print('\n已取消。')
        return 1
    except Exception as exc:
        print(f'修复未完成：{exc}')
        traceback.print_exc()
        print(f'日志：{log}')
        return 1
    finally:
        if handle is not None:
            sys.stdout, sys.stderr = original_stdout, original_stderr
            handle.close()


if __name__ == '__main__':
    raise SystemExit(main())
