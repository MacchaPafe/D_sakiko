"""角色专属演出策略；不向通用模型适配器添加角色判断。"""

from random import random
from live2d_support.runtime_adapter import Live2DModelAdapter

SAKIKO_COSTUME = "../live2d_related/sakiko/live2D_model_costume/3.model.json"


def apply_sakiko_state(player, model, value, default_path, replace_model):
    if (
        not player.if_sakiko
        or not isinstance(model, Live2DModelAdapter)
        or model.version != "v2"
    ):
        return model
    player._reset_long_audio_motion_loop()
    if value == "maskoff":
        if player.sakiko_state:
            model.StartRandomMotion(
                "change_character_maskoff" if player.if_mask else "maskon",
                3,
                player.onStartCallback,
                player.onFinishCallback,
                position="C",
            )
            player.if_mask = not player.if_mask
        else:
            model.StartMotion(
                "text_generating",
                0,
                3,
                player.onStartCallback,
                player.onFinishCallback,
                position="C",
            )
        return model
    path = SAKIKO_COSTUME if value else default_path
    if path is None:
        return model
    model = replace_model(model, path)
    if not isinstance(model, Live2DModelAdapter):
        return model
    player.sakiko_state = bool(value)
    player.if_mask = random() < 0.5 if value else True
    model.StartRandomMotion(
        "change_character" if player.if_mask else "change_character_maskoff",
        2,
        player.onStartCallback,
        player.onFinishCallback,
        position="C",
    )
    model.SetSemanticExpression("serious" if value else "idle")
    return model
