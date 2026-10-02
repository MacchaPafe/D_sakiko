"""模型级摘戴动作绑定；不推断或锁定模型的面具参数。"""

from pathlib import Path
from typing import Optional

from .performance_catalog import performance_config_path, read_config, save_config

MASK_MOTION_GROUP = "__dsakiko_mask__"
MASK_ACTIONS = ("on", "off")


def mask_motion_options(model_path: str) -> tuple[str, ...]:
    root = Path(model_path).resolve().parent
    suffix = "*.motion3.json" if str(model_path).endswith(".model3.json") else "*.mtn"
    return tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob(suffix)
                        if path.resolve().is_relative_to(root)))


def mask_actions(model_path: Optional[str]) -> dict[str, str]:
    if not model_path:
        return {}
    path = Path(model_path).resolve()
    config = read_config(performance_config_path(path))
    raw = config.get("mask_actions")
    if not isinstance(raw, dict):
        builtin = Path(__file__).resolve().parents[2] / "live2d_related/sakiko/live2D_model_costume"
        raw = {"on": "maskon_bow.mtn", "off": "maskoff.mtn"} if path.parent == builtin else {}
    allowed = set(mask_motion_options(str(path)))
    return {key: value for key, value in raw.items()
            if key in MASK_ACTIONS and isinstance(value, str) and value in allowed}


def save_mask_actions(model_path: str, actions: dict[str, str]) -> None:
    path = Path(model_path).resolve()
    allowed = set(mask_motion_options(str(path)))
    if any(key not in MASK_ACTIONS or (value and value not in allowed) for key, value in actions.items()):
        raise ValueError("摘戴动作不属于当前模型，或动作文件已失效")
    config_path = performance_config_path(path)
    config = read_config(config_path)
    config["mask_actions"] = {key: value for key, value in actions.items() if value}
    save_config(config_path, config)


def play_mask_action(player, model, action: str) -> bool:
    if not player.if_sakiko or not player.sakiko_state or action not in MASK_ACTIONS:
        return False
    path = getattr(model, "model_json_path", None)
    file = mask_actions(path).get(action)
    if not file:
        return False
    player._reset_long_audio_motion_loop()
    started = model.StartMotionFile(str(Path(path).resolve().parent / file), 3,
                                    player.onStartCallback, player.onFinishCallback,
                                    auto_expression=False)
    if started:
        player.if_mask = action == "on"
    return bool(started)
