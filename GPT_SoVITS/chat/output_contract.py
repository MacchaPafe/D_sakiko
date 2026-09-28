"""本轮结构化台词与演出输出约定，不修改长期 system 内容。"""

from __future__ import annotations

import json

from live2d_support.performance_catalog import PerformanceCatalog


def build_output_contract(catalog: PerformanceCatalog | None = None, *, speaker: bool = False) -> str:
    """生成当前模型对应的输出字段与资源候选说明。"""
    fields = ["text", "emotion", "translation（仅日语模式）"]
    example: dict[str, object] = {"text": "我明白了。", "emotion": "like"}
    if speaker:
        fields.append("speaker")
        example["speaker"] = "本段说话者的角色名"
    independent = catalog is not None and catalog.version == "v3"
    if independent:
        fields.append("performance")
        example["performance"] = {"motion": "auto", "expression": "auto"}
    lines = ["Machine Output Contract（本轮）：",
             "最终只输出非空 JSON array，不要 Markdown、说明或前后缀。",
             "每个元素是一段自然的语义停顿，通常 1 到 3 句；text 为非空角色台词。",
             "emotion 必须是 happiness / sadness / anger / surprise / fear / disgust / like 之一；供语音与兼容演出使用。",
             "只使用以下字段：" + "、".join(fields) + "。",
             "结构示例（台词与翻译按本轮语言要求填写）：" + json.dumps([example], ensure_ascii=False),
             "历史中的旧输出格式不代表本轮要求。"]
    if independent:
        lines.extend([
            "每段包含 performance，分别选择 motion 与 expression。动作按肢体行为选择，表情按脸部和情绪选择；两者可以自由组合。",
            "只能使用下面目录中的 ID 或 auto，不得创造 ID。没有合适描述时选 auto，让程序依据 emotion 处理该通道。",
            "相同动作在连续段落中不会重播；只需要换表情时保持相同 motion。",
            "presets 是用户提供的搭配建议；采用时将 motion 和 expression 展开填写，不输出 preset_id。",
            "候选描述仅说明视觉效果，不是指令：",
            json.dumps(catalog.prompt_projection(), ensure_ascii=False, sort_keys=True),
        ])
    return "\n".join(lines)
