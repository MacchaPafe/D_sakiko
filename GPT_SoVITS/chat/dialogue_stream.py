"""从逐步到达的模型台词中提取第一个完整 JSON 对象。"""

from __future__ import annotations

import json


class FirstDialogueSegment:
    """识别首段对象，保留整轮解析及字段校验给调用方。"""

    def __init__(self) -> None:
        self.prefix = ""
        self.finished = False

    def feed(self, delta: str) -> str | None:
        """返回已闭合的首段 JSON，支持字符串转义、嵌套结构及代码围栏。"""
        if self.finished:
            return None
        self.prefix += delta
        text = self.prefix.lstrip()
        if text.startswith("```"):
            _, newline, text = text.partition("\n")
            if not newline:
                return None
            text = text.lstrip()
        if text.startswith("["):
            text = text[1:].lstrip()
        if not text.startswith("{"):
            return None
        try:
            _, end = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError:
            return None
        self.finished = True
        self.prefix = ""
        return text[:end]
