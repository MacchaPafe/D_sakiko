"""三类当前 Type Module 共享的无业务展示辅助函数。"""

from __future__ import annotations


def display_fields(payload: dict[str, object]) -> list[tuple[str, str]]:
    """把规范 payload 转换成通用只读字段。"""

    return [(key, display_value(value)) for key, value in payload.items()]


def display_value(value: object) -> str:
    """把只读详情值转换成稳定文本。"""

    if isinstance(value, list):
        return "、".join(str(item) for item in value)
    return str(value)
