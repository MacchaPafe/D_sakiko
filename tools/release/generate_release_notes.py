#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


def normalize_release_body(body: str, title: str, version: str) -> str:
    """将 GitHub release body 规范化为 release_notes.md 内容。"""

    stripped_body = body.strip()
    heading = f"# {title.strip() or version.strip()}"
    if stripped_body.startswith("#"):
        return stripped_body + "\n"
    if not stripped_body:
        stripped_body = "本次更新暂无详细说明。"
    return f"{heading}\n\n{stripped_body}\n"


def extract_summary(markdown: str, max_length: int = 120) -> str:
    """从完整 release note 中提取简短摘要，写入 update_index.json。"""

    lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line == "---":
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        lines.append(line)
        if sum(len(item) for item in lines) >= max_length:
            break
    summary = " ".join(lines).strip()
    if len(summary) > max_length:
        summary = summary[: max_length - 1].rstrip() + "…"
    return summary or "本次更新暂无详细说明。"


def write_release_notes(markdown: str, output_path: Path) -> None:
    """写出 release_notes.md。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="从 GitHub Release body 生成客户端更新公告附件。")
    parser.add_argument("--release-body-file", required=True, help="GitHub Release 正文文件。")
    parser.add_argument("--version", required=True, help="版本号。")
    parser.add_argument("--title", required=True, help="公告标题。")
    parser.add_argument("--output", default="release_notes.md", help="输出 Markdown 文件。")
    parser.add_argument("--summary-output", default="release_summary.txt", help="输出摘要文件。")
    parser.add_argument("--summary-length", type=int, default=120, help="摘要最大字符数。")
    return parser.parse_args()


def main() -> int:
    """CLI 入口。"""

    args = parse_args()
    body = Path(args.release_body_file).read_text(encoding="utf-8")
    markdown = normalize_release_body(body, args.title, args.version)
    write_release_notes(markdown, Path(args.output))
    Path(args.summary_output).write_text(extract_summary(markdown, args.summary_length) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

