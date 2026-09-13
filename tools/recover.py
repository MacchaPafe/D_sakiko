#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'GPT_SoVITS'))

from maintenance.transactions import recover_pending


def main() -> int:
    """在加载业务模块前独立执行本地恢复。"""
    parser = argparse.ArgumentParser(description='恢复未完成的更新或修复事务。')
    parser.add_argument('--app-root', default=str(ROOT))
    parser.add_argument('--manual', action='store_true', help='再次尝试所有尚未恢复的文件。')
    args = parser.parse_args()
    try:
        return 0 if recover_pending(Path(args.app_root).resolve(), automatic=not args.manual) else 1
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
