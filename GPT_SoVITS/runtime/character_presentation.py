"""祥子只保留明确的摘戴动作策略，形态模型目标由对话层解析。"""

from live2d_support.mask_actions import play_mask_action


def apply_sakiko_state(player, model, value):
    if isinstance(value, dict) and value.get("type") == "mask_action":
        play_mask_action(player, model, str(value.get("action") or ""))
    return model
