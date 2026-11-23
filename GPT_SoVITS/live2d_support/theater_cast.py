"""小剧场剧本角色名单、逐句槽位与 Qt/Live2D 共用的角色投影。"""
from copy import deepcopy

from chat.archive import python_meta
from live2d_support.character_forms import default_form_model, explicit_model
from live2d_support.multi_char_scene import resolve_casts


def theater_opening_names(chat):
    """读取旧剧本保存的开场顺序，兼容没有小剧场提示词的存档。"""
    from chat.chat import SmallTheaterPromptGenerator

    if isinstance(chat.prompt_generator, SmallTheaterPromptGenerator):
        names = [name for name in chat.prompt_generator.character_names if name and name != "User"]
        if len(names) >= 2:
            return names[:2]
    return [name for name in chat.involved_characters if name and name != "User"][:2]


class TheaterCast:
    """以资源目录名识别演员，显示名称只用于旧引用和当前界面。"""

    def __init__(self, chat, opening_names, characters, facing):
        self.chat = chat
        self.facing = facing
        self.characters = {character.character_folder_name: character for character in characters}
        saved_names = python_meta(chat._timeline.data).get("characterNames", {})
        self.saved_names = dict(saved_names) if isinstance(saved_names, dict) else {}
        candidates = {}
        installed_names = [(role, character.character_name) for role, character in self.characters.items()]
        for role, name in (*self.saved_names.items(), *installed_names):
            if isinstance(name, str) and name and not role.startswith("missing-"):
                candidates.setdefault(name, set()).add(role)
        self.legacy_ids = {name: next(iter(roles)) for name, roles in candidates.items() if len(roles) == 1}
        unresolved = {name: role for role, name in self.saved_names.items()
                      if isinstance(name, str) and role.startswith("missing-")}
        names = (list(opening_names) + ["", ""])[:2]
        self.opening = tuple(setting.character_id or self.legacy_ids.get(name) or unresolved.get(name) or name or None
                             for setting, name in zip((self.meta.character_0, self.meta.character_1), names))
        for role, name in zip(self.opening, names):
            if role and name:
                self.saved_names.setdefault(role, name)
                self.legacy_ids[name] = role

    @property
    def meta(self):
        return self.chat.meta.theater

    def record_opening(self):
        """首次保存新阵容时为可解析的旧开场角色补稳定身份。"""
        for setting, role in zip((self.meta.character_0, self.meta.character_1), self.opening):
            if role in self.characters and not setting.character_id:
                setting.character_id = role

    def role_ids(self):
        """按开场顺序和加入顺序列出剧本可用角色。"""
        return list(dict.fromkeys(role for role in (*self.opening, *self.meta.extra_character_ids) if role))

    def can_remove(self, role):
        """额外角色可以退出可用名单，开场角色仍由剧本的开场设定提供。"""
        return role not in self.opening and role in self.meta.extra_character_ids

    def remove(self, role, save):
        """移除可用角色，只修改名单。保存失败时恢复原名单。"""
        if not self.can_remove(role):
            return False
        previous = self.meta.extra_character_ids
        self.meta.extra_character_ids = [item for item in previous if item != role]
        saved = False
        try:
            saved = bool(save())
            return saved
        finally:
            if not saved:
                self.meta.extra_character_ids = previous

    def label(self, role):
        if role in self.characters:
            return self.characters[role].character_name
        name = self.saved_names.get(role, role)
        if any(character.character_name == name for character in self.characters.values()):
            return f"{name}（{role}）"
        return name

    def casts(self):
        return resolve_casts(self.chat.message_list, self.opening, self.legacy_ids)

    def timeline(self, timeline, *, for_storage=False):
        """保留每位角色的轨道，只转换编辑或运行视图使用的键。"""
        if not isinstance(timeline, dict):
            return timeline
        result = deepcopy(timeline)
        tracks = result.get("characters")
        if isinstance(tracks, dict):
            roles = set(self.characters) | set(self.saved_names)
            if for_storage:
                keys = {self.label(role): role for role in roles}
            else:
                keys = {role: self.label(role) for role in roles}
                keys.update({name: self.label(role) for name, role in self.legacy_ids.items()
                             if role in self.characters})
            result["characters"] = {keys.get(key, key): value for key, value in tracks.items()}
        return result

    def payload(self, cast):
        """准备左右槽位的模型目标。缺失角色保留身份，空槽位可独立加载。"""
        slots = []
        sakiko_form = self.chat.get_character_form()
        for side, role in enumerate(cast):
            character = self.characters.get(role)
            name = self.label(role) if role else ""
            path = None
            if character is not None:
                path = explicit_model(self.chat.meta, name, role)
                if not path:
                    path = default_form_model(sakiko_form) if role == "sakiko" else getattr(character, "live2d_json", None)
            slots.append({"slot": side, "character_name": name, "character_folder_name": role,
                          "model_json_path": path, "empty": role is None,
                          "sakiko_state": sakiko_form == "black"})
        return {"slots": slots, "motion_facing_mode": self.facing}

    def messages(self):
        """发送已解析的完整阵容；旧剧本沿用原来的显隐和过渡路径。"""
        messages = list(self.chat.message_list)
        uses_casts = any(isinstance(message.scene_before, dict) and "cast" in message.scene_before
                         for message in messages)
        result = []
        payloads = {}
        for message, cast in zip(messages, self.casts()):
            turn = message.as_dict()
            if message.speaker_id in self.characters:
                turn["character_name"] = self.label(message.speaker_id)
            if message.performance_timeline is not None:
                turn["performance_timeline"] = self.timeline(message.performance_timeline)
            scene = turn.get("scene_before")
            if not uses_casts and isinstance(scene, dict) and isinstance(scene.get("visible_characters"), list):
                scene["visible_characters"] = [self.label(self.legacy_ids.get(name, name))
                                               for name in scene["visible_characters"] if isinstance(name, str)]
            if uses_casts:
                if cast not in payloads:
                    payloads[cast] = self.payload(cast)
                turn["stage_slots"] = payloads[cast]
            result.append(turn)
        return result

    def add(self, role, save):
        """保存全剧候选名单，失败后恢复原列表。"""
        if role in self.role_ids() or role not in self.characters:
            return False
        previous = self.meta.extra_character_ids
        opening_before = (self.meta.character_0.character_id, self.meta.character_1.character_id)
        self.meta.extra_character_ids = [*previous, role]
        saved = False
        try:
            self.record_opening()
            saved = bool(save())
            return saved
        finally:
            if not saved:
                self.meta.extra_character_ids = previous
                self.meta.character_0.character_id, self.meta.character_1.character_id = opening_before
