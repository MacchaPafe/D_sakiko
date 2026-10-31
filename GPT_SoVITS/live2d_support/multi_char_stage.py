"""小剧场临时舞台与句前阶段；不修改模型持久布局或消息数据。"""
from copy import deepcopy
import time

from live2d_support.multi_char_scene import SceneTiming, resolve_scene


class MultiCharStage:
    """按帧推进上下场、位置和黑幕，统一正式播放与编辑预览。"""

    def __init__(self, characters, offsets, backgrounds, reset, report, clock=time.monotonic):
        """注入角色、原站位、背景纹理服务及进入舞台时的复位回调。"""
        self.characters = characters
        self.offsets = offsets
        self.backgrounds = backgrounds
        self.reset = reset
        self.report = report
        self.clock = clock
        self.base_background = backgrounds.current_name
        self.state = self.base_state()
        self.actors = self.actor_targets(self.state)
        self.black = 0.0
        self.timing = None
        self.started = 0.0
        self.changed = False
        self.source_actors = {}
        self.target = None
        self.saved = None

    def base_state(self):
        """建立本次会话默认双人与默认背景，不使用上次演出的末态。"""
        return {"visible_characters": list(self.characters()), "background": self.base_background}

    @property
    def busy(self):
        """返回是否仍在执行独立句前阶段。"""
        return self.timing is not None

    def actor_targets(self, state):
        """计算透明度和临时横向位置，单人居中且保留各模型纵向布局。"""
        visible = state["visible_characters"]
        result = {}
        for name, (x, y) in self.offsets().items():
            result[name] = (1.0 if name in visible else 0.0,
                            0.0 if len(visible) == 1 and name in visible else x, y)
        return result

    def visible(self, name):
        """判断角色是否属于当前目标舞台，隐藏事件只消费不播放。"""
        return name in self.state["visible_characters"]

    def resolve(self, messages, index):
        """合并到目标句；坏背景回退到此前可加载的背景并保留存档引用。"""
        target = resolve_scene(messages, index, self.characters(), self.base_background)
        requested = target["background"]
        candidates = [requested]
        for turn in reversed(messages[:max(0, index + 1)]):
            raw = turn.get("scene_before") if isinstance(turn, dict) else getattr(turn, "scene_before", None)
            scene = raw if isinstance(raw, dict) else {}
            background = scene.get("background")
            if isinstance(background, str) and background:
                candidates.append(background)
        candidates.append(self.base_background)
        checked = set()
        for name in candidates:
            if name in checked:
                continue
            checked.add(name)
            try:
                self.backgrounds.prepare(name)
            except (OSError, ValueError, RuntimeError) as exc:
                if name == requested:
                    self.report(f"背景 {name} 加载失败，已保留此前背景。\n{exc}")
                continue
            target["background"] = name
            break
        else:
            target["background"] = self.backgrounds.current_name
        return target

    def show(self, state):
        """立即应用完整目标状态，不补播历史动作或黑场。"""
        self.cancel()
        self.target = deepcopy(state)
        self.apply_target()
        self.actors = self.actor_targets(self.state)
        self.prune_backgrounds()

    def apply_target(self):
        """在阶段指定时点切换背景并仅复位新入场者。"""
        for name in self.target["visible_characters"]:
            if name not in self.state["visible_characters"]:
                self.reset(name)
        self.backgrounds.show(self.target["background"])
        self.state = deepcopy(self.target)

    def begin(self, target, scene_before, first=False):
        """启动一次句前阶段；首句直接就位，不追加等待或黑场。"""
        self.cancel()
        self.target = deepcopy(target)
        self.source_actors = dict(self.actors)
        self.timing = SceneTiming.from_scene(scene_before, first=first)
        self.started = self.clock()
        self.changed = False
        self.tick()

    def tick(self):
        """按单调时钟更新透明度与位置；卡顿也一次完成正确末态。"""
        if self.timing is None:
            return
        elapsed = (self.clock() - self.started) * 1000
        self.black = self.timing.black_opacity(elapsed)
        if elapsed >= self.timing.change_at_ms:
            if not self.changed:
                self.apply_target()
                self.changed = True
            progress = self.timing.actors_progress(elapsed)
            targets = self.actor_targets(self.target)
            for name, target in targets.items():
                source = self.source_actors.get(name, target)
                # 新入场者从最终站位渐显，退场者原地渐隐；只有留场者平移。
                if source[0] == 0:
                    source = (0.0, target[1], target[2])
                if target[0] == 0:
                    target = (0.0, source[1], source[2])
                self.actors[name] = tuple(a + (b - a) * progress for a, b in zip(source, target))
        if elapsed >= self.timing.duration_ms:
            self.actors = self.actor_targets(self.state)
            self.timing = None
            self.black = 0.0
            self.prune_backgrounds()

    def cancel(self):
        """取消过渡并清除黑幕；调用方随后应用目标场景或恢复现场。"""
        self.timing = None
        self.black = 0.0

    def enter_editor(self):
        """首次进入保存实际舞台，切句重新建立预览会话时不覆盖快照。"""
        if self.saved is None:
            self.saved = (deepcopy(self.state), dict(self.actors), self.base_background)
            self.base_background = self.backgrounds.default_name
        self.cancel()

    def exit_editor(self):
        """退出恢复原舞台，不撤回消息里已保存的场景配置。"""
        self.cancel()
        if self.saved is not None:
            self.state, self.actors, self.base_background = self.saved
            self.backgrounds.show(self.state["background"])
            self.saved = None
        self.prune_backgrounds()

    def new_playback(self):
        """新回放重建默认起点，单句定位也使用同一个程序默认背景。"""
        self.base_background = self.backgrounds.default_name
        self.show(self.base_state())

    def prune_backgrounds(self):
        """只保留当前、默认及退出编辑需恢复的背景，避免逐句累积纹理。"""
        keep = {self.state["background"], self.backgrounds.default_name}
        if self.saved is not None:
            keep.add(self.saved[0]["background"])
        self.backgrounds.prune(keep)
