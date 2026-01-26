from dataclasses import dataclass
from typing import Any, Optional
import re

from rapidfuzz import fuzz

from .models import Live2dCostume, Language
from .bestdori_client import BestdoriClient

@dataclass(frozen=True)
class CostumeInfo:
    """
    描述一套角色服装的基本信息
    """
    live2d_name: str
    chara_id: int


class Live2dService:
    """
    如果你写过后端的话，这个类就类似于“服务层”，负责协调 BestdoriClient 和业务逻辑。
    这个类的方法返回的结果基本都可以直接用于 Qt 界面展示。
    例如，列出角色、搜索服装、构建 Live2dCostume 等等。
    """
    def __init__(self, client: BestdoriClient, language: Language = Language.SIMPLIFIED_CHINESE) -> None:
        """
        初始化 Live2dService 实例。

        Args:
            client: 用于与 Bestdori API 交互的客户端.
            language: 请求 API 后，返回的角色名、服装名等字符串使用的语言。
        """
        self.client = client
        self.language = language

    def list_characters(self) -> list[dict[str, Any]]:
        """返回角色列表，每个角色为一个字典，包含角色 id 和 name 字段。
        """
        roster = self.client.get_characters_roster()
        result = []
        for key, value in roster.items():
            result.append({"id": key, "name": value["characterName"][self.language.value]})
        return result
    
    def _list_all_languages_characters(self) -> list[dict[str, Any]]:
        """返回角色列表，每个角色为一个字典，包含角色 id 和 name 字段。
        这里的 name 字段包含所有语言的名称，而不是单一语言。
        """
        roster = self.client.get_characters_roster()
        result = []
        for key, value in roster.items():
            result.append({"id": key, "name": value["characterName"]})
        return result
    
    def search_character(self, name: str, *, match_threshold=75, all_language_result=False) -> Optional[dict[str, Any]]:
        """输入角色名，返回最匹配的角色字典，包含 id 和 name 字段；找不到时返回 None。

        输入的角色名称可以是模糊的；我们使用 rapidfuzz 尽可能执行模糊匹配。
        请注意，我们搜索角色名称的语言取决于 self.language 属性.
        
        :param name: 要搜索的角色名称
        :param match_threshold: 最低匹配分数，范围 0-100。 如果没有任何角色的匹配分数达到该阈值，则返回 None。
        :param all_language_result: 如果为 True，那么接受任何语言的匹配结果，而不仅仅是 self.language 指定的语言。
                                   这在用户输入的名称语言不确定时很有用，但可能会导致意外匹配或者重复输出相同角色。
                                   此时，返回的结果同样包含所有语言的名称。
        
        >>> client = BestdoriClient()
        >>> service = Live2dService(client, language=Language.SIMPLIFIED_CHINESE) # 简体中文语言
        >>> service.search_character("灯")
        {'id': '36', 'name': '高松 灯'}
        >>> service.search_character("Tomori")
        None
        >>> service.search_character("Tomori", all_language_result=True)
        {'id': '36', 'name': ['高松 燈', 'Tomori Takamatsu', '高松 燈', '高松 灯', None]}
        >>> service.search_character("Tomori", all_language_result=True)['name'][Language.ENGLISH.value]
        'Tomori Takamatsu'
        """
        characters = self._list_all_languages_characters() if all_language_result else self.list_characters()
        lower_name = name.lower()
        max_score = 0
        best_match = None
        for chara in characters:
            if all_language_result:
                # 检查所有语言的名称
                score = 0
                for lang_name in chara["name"]:
                    if lang_name is None:
                        continue
                    score = max(score, fuzz.partial_ratio(lower_name, lang_name.lower()))
            else:
                # 仅检查 self.language 指定的语言名称
                chara_name = chara["name"]
                if chara_name is not None:
                    score = fuzz.partial_ratio(lower_name, chara_name.lower())
                else:
                    score = 0
            if score > max_score:
                max_score = score
                best_match = chara

        if max_score >= match_threshold:
            return best_match
        
        return None

    def search_costumes(
        self,
        chara_id: int,
        *,
        include_live_event: bool = True,
    ) -> list[str]:
        """输入角色名，返回该角色所有 live2d 服装的名称列表.请注意，这些名称是游戏内部的标识符，而不是显示名称。

        筛选规则：
        - 1) 服装名称以 三位数角色编号_ 开头
        - 2) 服装名称不以 general 结尾（因为 general 是该角色的通用文件，而不是某个特定服装）
        - 3) 如果 include_live_event 为 False，则排除以 live_event 结尾的服装
        - 4) 按照特殊规则排序：
            - live_event 结尾的服装排在最后
            - 其余服装按名称中数字部分排序（如果无法提取数字则排在最后）
        """
        costumes = self.client.get_live2d_assets_map()
        live2d_costumes = []
        for live2d_name, id_ in costumes.items():
            formated_id = f"{chara_id:03d}"
            if live2d_name.startswith(formated_id) and not live2d_name.endswith("general"):
                if not include_live_event and live2d_name.endswith("live_event"):
                    continue
                live2d_costumes.append(live2d_name)
        def _extract_sort_number(name: str) -> float:
            # 期望 name 类似：036_2024_furisode / 037_casual-2023
            tail = name.split("_", 1)[1] if "_" in name else name
            m = re.search(r"\d+", tail)
            return float(m.group(0)) if m else float("inf")

        live2d_costumes.sort(key=lambda x: (
            x.endswith("live_event"),
            _extract_sort_number(x),
            x,
        ))
        return live2d_costumes
    
    def get_costume_icon(self, live2d_name: str) -> Optional[bytes]:
        """
        获得指定服装的图标二进制数据。
        如果找不到对应服装，返回 None。
        """
        costumes = self.client.get_costume_index()
        for costume_id, value in costumes.items():
            if value["assetBundleName"] == live2d_name:
                return self.client.get_costume_icon(int(costume_id), live2d_name)
        return None
    
    def get_costume_name(self, live2d_name: str, other_language=False) -> Optional[str]:
        """
        获得指定服装的显示名称（非内部标识符）。
        如果找不到对应服装，或者服装在当前语言下不存在名称，返回 None。

        :param live2d_name: 服装的内部标识符名称，例如 "036_dream_festival_3_ur"
        :param other_language: 如果设置为 True，那么在当前语言的名称不存在时，尝试返回其他语言的名称。
        """
        costumes = self.client.get_costume_index()
        for _, value in costumes.items():
            if value["assetBundleName"] == live2d_name:
                names = value["description"]
                name = names[self.language.value]
                # 优先返回当前语言名称
                if name:
                    return name
                if other_language:
                    for lang_name in names:
                        if lang_name:
                            return lang_name+"（日服）"
        return None

    def build_costume(self, live2d_name: str) -> Live2dCostume:
        """拉取 buildData.asset 并解析成 Live2dCostume（含文件清单）。
        live2d_name 参数必须是有效的服装名称（例如通过 search_costumes 获得的名称）。
        """
        build_data = self.client.get_build_data(live2d_name)
        return Live2dCostume.from_build_data(live2d_name, build_data)
