"""小剧场临时舞台与句前阶段；不修改模型持久布局或消息数据。"""
from copy import deepcopy
from dataclasses import replace
from math import pi, sin
import time

from live2d_support.multi_char_scene import ACTOR_TRANSITION_MS, SceneTiming, resolve_scene


class MultiCharStage:
    """按帧推进上下场、位置和黑幕，统一正式播放与编辑预览。"""

    def __init__(self, characters, offsets, backgrounds, reset, report, clock=time.monotonic, bind_cast=None):
        """注入角色、原站位、背景纹理服务及进入舞台时的复位回调。"""
        self.characters = characters
        self.offsets = offsets
        self.backgrounds = backgrounds
        self.reset = reset
        self.report = report
        self.clock = clock
        self.bind_cast = bind_cast
        self.opening_slots = None
        self.base_background = backgrounds.current_name
        self.state = self.base_state()
        self.actors = self.actor_targets(self.state)
        self.black = 0.0
        self.capsule = None
        self.timing = None
        self.started = 0.0
        self.changed = False
        self.source_actors = {}
        self.target = None
        self.saved = None
        self.cast_phase = None
        self.walk = False

    def base_state(self, opening_slots=None):
        """建立本次会话默认双人与默认背景，不使用上次演出的末态。"""
        opening = self.opening_slots if opening_slots is None else opening_slots
        if opening is None:
            return {"visible_characters": list(self.characters()), "background": self.base_background}
        return {"visible_characters": self.slot_names(opening), "background": self.base_background, "slots": opening}

    @staticmethod
    def slot_names(payload):
        return [slot["character_name"] for slot in payload["slots"] if slot["character_name"]]

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

    def resolve(self, messages, index, *, opening_slots=None):
        """合并到目标句；坏背景回退到此前可加载的背景并保留存档引用。"""
        base = self.base_state(opening_slots)
        target = resolve_scene(messages, index, base["visible_characters"], self.base_background)
        opening = base.get("slots")
        turn = messages[index] if 0 <= index < len(messages) else None
        slots = turn.get("stage_slots") if isinstance(turn, dict) else None
        if slots is not None:
            target["slots"] = slots
            target["visible_characters"] = self.slot_names(slots)
        elif opening is not None:
            target["slots"] = opening
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
        slots = self.target.get("slots")
        if slots is not None and slots != self.state.get("slots") and self.bind_cast is not None:
            self.bind_cast(slots)
        for name in self.target["visible_characters"]:
            if name not in self.state["visible_characters"]:
                self.reset(name)
        self.backgrounds.show(self.target["background"])
        self.state = deepcopy(self.target)

    def begin(self, target, scene_before, first=False, walk=False):
        """启动句前阶段；首句跳过黑场，胶囊仍按配置执行。"""
        self.cancel()
        self.target = deepcopy(target)
        self.source_actors = dict(self.actors)
        self.timing = SceneTiming.from_scene(scene_before, first=first, actors=bool(target["visible_characters"]))
        self.walk = walk
        slots = target.get("slots")
        previous = self.state.get("slots")
        if slots is not None and previous != slots and not first and not (
                self.timing.black_duration_ms or self.timing.capsule_hold_ms):
            outgoing = set(self.state["visible_characters"]) - set(target["visible_characters"])
            incoming = set(target["visible_characters"]) - set(self.state["visible_characters"])
            old_positions = {slot["character_name"]: slot["slot"] for slot in previous["slots"]} if previous else {}
            moves = any(slot["character_name"] in old_positions and slot["character_name"]
                        and old_positions[slot["character_name"]] != slot["slot"] for slot in slots["slots"])
            fade_out = ACTOR_TRANSITION_MS if outgoing else 0
            move_before_entry = ACTOR_TRANSITION_MS if outgoing and incoming and moves else 0
            movement = 520 if walk else ACTOR_TRANSITION_MS
            self.cast_phase = (fade_out, move_before_entry, movement)
            self.timing = replace(self.timing, change_at_ms=fade_out,
                                  duration_ms=max(self.timing.duration_ms, fade_out + move_before_entry + movement))
        self.started = self.clock()
        self.changed = False
        self.tick()

    def tick_cast(self, elapsed, targets):
        """普通换角依次完成退场、让位和进场；互换预览只叠加短步幅。"""
        fade_out, move_before_entry, movement = self.cast_phase
        position = min(1.0, (elapsed - fade_out) / movement)
        entrance = max(0.0, min(1.0, (elapsed - fade_out - move_before_entry) / movement))
        for side, (name, target) in enumerate(targets.items()):
            source = self.source_actors.get(name)
            if source is None or not source[0]:
                self.actors[name] = (target[0] * entrance, target[1], target[2])
                continue
            alpha, x, y = (start + (end - start) * position for start, end in zip(source, target))
            if self.walk:
                y += 0.035 * sin(pi * position) * abs(sin(2 * pi * position + side * pi / 3))
            self.actors[name] = (alpha, x, y)

    def tick(self):
        """按单调时钟更新透明度与位置；卡顿也一次完成正确末态。"""
        if self.timing is None:
            return
        elapsed = (self.clock() - self.started) * 1000
        self.black = self.timing.black_opacity(elapsed)
        offset = self.timing.capsule_offset(elapsed)
        self.capsule = (self.timing.capsule_text, offset) if offset is not None else None
        if self.cast_phase and elapsed < self.timing.change_at_ms:
            progress = elapsed / self.timing.change_at_ms
            remaining = self.target["visible_characters"]
            self.actors = {name: (alpha if name in remaining else alpha * (1 - progress), x, y)
                           for name, (alpha, x, y) in self.source_actors.items()}
        if elapsed >= self.timing.change_at_ms:
            if not self.changed:
                slots_change = self.target.get("slots") != self.state.get("slots")
                if slots_change and self.timing.black_duration_ms:
                    self.black = 1.0
                if slots_change and self.capsule is not None:
                    self.actors = {}
                if self.cast_phase and self.timing.change_at_ms:
                    self.actors = {name: actor for name, actor in self.source_actors.items()
                                   if name in self.target["visible_characters"]}
                before_load = self.clock()
                self.apply_target()
                if slots_change:
                    self.started += self.clock() - before_load
                self.changed = True
            progress = self.timing.actors_progress(elapsed)
            targets = self.actor_targets(self.target)
            if self.cast_phase:
                self.tick_cast(elapsed, targets)
            else:
                for name, target in targets.items():
                    if self.timing.capsule_hold_ms:
                        self.actors[name] = (target[0] * progress, target[1], target[2])
                        continue
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
        self.capsule = None
        self.cast_phase = None
        self.walk = False

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
            if self.bind_cast is not None and self.state.get("slots") is not None:
                self.bind_cast(self.state["slots"])
            self.backgrounds.show(self.state["background"])
            self.saved = None
        self.prune_backgrounds()

    def new_playback(self, restore_opening=True):
        """新回放重建默认起点，单句定位也使用同一个程序默认背景。"""
        self.base_background = self.backgrounds.default_name
        if restore_opening:
            self.show(self.base_state())
        else:
            self.cancel()

    def prune_backgrounds(self):
        """只保留当前、默认及退出编辑需恢复的背景，避免逐句累积纹理。"""
        keep = {self.state["background"], self.backgrounds.default_name}
        if self.saved is not None:
            keep.add(self.saved[0]["background"])
        self.backgrounds.prune(keep)
