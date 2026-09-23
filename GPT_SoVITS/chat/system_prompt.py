"""系统提示词快照与正常请求共用的无损拼接约定。"""


def compose_system_prompt(base: str, runtime: str) -> str:
    """非空运行时指令以一个换行追加；不裁剪或改写任一段文本。"""
    return base + "\n" + runtime if runtime else base
