"""Qdrant RAG schema 的数据模型定义。"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple, Type, TypeVar, Union, get_args, get_origin, get_type_hints


EnumT = TypeVar("EnumT", bound=Enum)
DocumentT = TypeVar("DocumentT", bound="BaseQdrantDocument")


class CollectionName(str, Enum):
    """定义 Qdrant 中会用到的 collection 名称常量。"""

    STORY_EVENTS = "story_events"
    CHARACTER_RELATIONS = "character_relations"
    STYLE_SAMPLES = "style_samples"
    LORE_ENTRIES = "lore_entries"


class SeriesId(str, Enum):
    """BanG Dream! 中各动画系列的标识。"""

    BANG_DREAM_1 = "bang_dream_1"
    BANG_DREAM_2 = "bang_dream_2"
    BANG_DREAM_3 = "bang_dream_3"
    ITS_MYGO = "its_mygo"
    AVE_MUJICA = "ave_mujica"


class SeasonId(int, Enum):
    """BanG Dream! 中统一的三年时间线"""
    # 你知道吗？邦邦世界观里目前只经过了三年，就是 ksm 高一到高三的三年……

    ONE = 1
    TWO = 2
    THREE = 3


class CanonBranch(str, Enum):
    """定义剧情所属的世界线分支。"""
    # 主要动画剧情
    MAIN = "main"
    # 手游补充剧情（项目中暂时没有收录此类剧情）
    GAME = "game"


class ScopeType(str, Enum):
    """定义世界观名词条目的适用范围类型。"""
    # 只在特定烯类动漫中适用
    SERIES = "series"
    # 全局适用
    GLOBAL = "global"


class CharacterId(str, Enum):
    """定义项目中可参与 RAG 检索的角色标识。"""

    KASUMI = "kasumi"  # 香澄 / 户山香澄
    TAE = "tae"  # 多惠 / 花园多惠
    RIMI = "rimi"  # 里美 / 牛込里美
    SAAYA = "saaya"  # 沙绫 / 山吹沙绫
    ARISA = "arisa"  # 有咲 / 市谷有咲
    RAN = "ran"  # 美竹兰 / 美竹兰
    MOCA = "moca"  # 摩卡 / 青叶摩卡
    HIMARI = "himari"  # 绯玛丽 / 上原绯玛丽
    TOMOE = "tomoe"  # 巴 / 宇田川巴
    TSUGUMI = "tsugumi"  # 羽泽鸫 / 羽泽鸫
    KOKORO = "kokoro"  # 弦卷心 / 弦卷心
    KAORU = "kaoru"  # 濑田薰 / 濑田薰
    HAGUMI = "hagumi"  # 育美 / 北泽育美
    KANON = "kanon"  # 花音 / 松原花音
    MISAKI = "misaki"  # 美咲 / 奥泽美咲
    AYA = "aya"  # 丸山彩 / 丸山彩
    HINA = "hina"  # 日菜 / 冰川日菜
    CHISATO = "chisato"  # 千圣 / 白鹭千圣
    MAMI = "mami"  # 麻弥 / 大和麻弥
    EVE = "eve"  # 伊芙 / 若宫伊芙
    YUKINA = "yukina"  # 友希那 / 凑友希那
    SAYO = "sayo"  # 纱夜 / 冰川纱夜
    LISA = "lisa"  # 莉莎 / 今井莉莎
    AKO = "ako"  # 亚子 / 宇田川亚子
    RINKO = "rinko"  # 燐子 / 白金燐子
    MASHIRO = "mashiro"  # 真白 / 仓田真白
    TOKO = "toko"  # 透子 / 桐谷透子
    NANAMI = "nanami"  # 七深 / 广町七深
    TSUKUSHI = "tsukushi"  # 筑紫 / 二叶筑紫
    RUI = "rui"  # 瑠唯 / 八潮瑠唯
    LAYER = "layer"  # layer / 和奏瑞依
    LOCK = "lock"  # 六花 / 朝日六花
    MASKING = "masking"  # msk / 佐藤益木
    PAREO = "pareo"  # pareo / 鳰原令王那
    CHUCHU = "chuchu"  # chu2 / 珠手知由
    TOMORI = "tomori"  # 灯 / 高松灯
    ANON = "anon"  # 爱音 / 千早爱音
    RANA = "rana"  # 乐奈 / 要乐奈
    SOYO = "soyo"  # 素世 / 长崎素世
    TAKI = "taki"  # 立希 / 椎名立希
    UIKA = "uika"  # 初华 / 三角初华
    MUTSUMI = "mutsumi"  # 若叶睦 / 若叶睦
    UMIRI = "umiri"  # 海铃 / 八幡海铃
    NYAMU = "nyamu"  # 喵梦 / 祐天寺喵梦
    SAKIKO = "sakiko"  # 祥子 / 丰川祥子


def _coerce_enum_value(enum_type: Type[EnumT], field_name: str, value: Any) -> EnumT:
    """将传入值转换为指定枚举成员，并在失败时给出明确异常。"""

    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError("字段 {0} 的值 {1!r} 不是合法的 {2}".format(field_name, value, enum_type.__name__)) from exc


def _coerce_enum_list(enum_type: Type[EnumT], field_name: str, values: List[Any]) -> List[EnumT]:
    """将列表中的每一项转换为指定枚举成员。"""

    if not isinstance(values, list):
        raise TypeError("字段 {0} 必须是列表".format(field_name))
    return [_coerce_enum_value(enum_type, field_name, value) for value in values]


def _normalize_text(field_name: str, value: str, allow_empty: bool = False) -> str:
    """清理文本字段，并按需校验文本是否允许为空。"""

    if not isinstance(value, str):
        raise TypeError("字段 {0} 必须是字符串".format(field_name))
    normalized_value = value.strip()
    if not allow_empty and not normalized_value:
        raise ValueError("字段 {0} 不能为空".format(field_name))
    return normalized_value


def _normalize_string_list(field_name: str, values: List[str], allow_empty: bool = False) -> List[str]:
    """清理字符串列表中的每一项，并按需校验列表是否允许为空。"""

    if not isinstance(values, list):
        raise TypeError("字段 {0} 必须是列表".format(field_name))

    normalized_values: List[str] = []
    for raw_value in values:
        normalized_value = _normalize_text(field_name, raw_value, allow_empty=False)
        normalized_values.append(normalized_value)

    if not allow_empty and not normalized_values:
        raise ValueError("字段 {0} 不能为空列表".format(field_name))
    return normalized_values


def _validate_time_window(visible_from: Optional[int], visible_to: Optional[int], field_prefix: str = "") -> None:
    """校验时间窗口上下界是否满足 from 小于等于 to。"""

    if visible_from is None or visible_to is None:
        return
    if visible_from > visible_to:
        if field_prefix:
            raise ValueError("{0} 的 visible_from 不能大于 visible_to".format(field_prefix))
        raise ValueError("visible_from 不能大于 visible_to")


def _serialize_payload_value(value: Any) -> Any:
    """将 Python 对象递归序列化为适合写入 Qdrant payload 的值。"""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_serialize_payload_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_payload_value(item) for item in value]
    return value


def _unwrap_optional(annotation: Any) -> Any:
    """剥离 Optional 注解，返回其中的真实类型。"""

    origin = get_origin(annotation)
    if origin is Union:
        non_none_args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(non_none_args) == 1:
            return non_none_args[0]
    return annotation


def _annotation_allows_none(annotation: Any) -> bool:
    """判断一个类型注解是否允许传入 None。"""

    origin = get_origin(annotation)
    if origin is Union:
        return any(arg is type(None) for arg in get_args(annotation))
    return False


def _deserialize_payload_value(annotation: Any, value: Any) -> Any:
    """按类型注解将 payload 中的原始值还原为 Python 对象。"""

    if value is None:
        return None

    resolved_annotation = _unwrap_optional(annotation)
    origin = get_origin(resolved_annotation)

    if origin in (list, List):
        item_annotation = get_args(resolved_annotation)[0]
        if not isinstance(value, list):
            raise TypeError("列表字段的 payload 值必须是列表")
        return [_deserialize_payload_value(item_annotation, item) for item in value]

    if isinstance(resolved_annotation, type) and issubclass(resolved_annotation, Enum):
        return resolved_annotation(value)

    return value


def _get_dataclass_fields(target: Any) -> Tuple[Any, ...]:
    """安全获取 dataclass 类型或实例的字段定义。"""

    if not is_dataclass(target):
        raise TypeError("目标对象必须是 dataclass 类型或 dataclass 实例")
    return tuple(fields(target))


class BaseQdrantDocument:
    """为所有 Qdrant 文档 dataclass 提供通用序列化与反序列化能力。"""

    collection_name: ClassVar[CollectionName]

    def to_payload(self) -> Dict[str, Any]:
        """将当前文档对象序列化为可写入 Qdrant 的 payload 字典。"""

        payload: Dict[str, Any] = {}
        for dataclass_field in _get_dataclass_fields(self):
            field_value = getattr(self, dataclass_field.name)
            if field_value is None:
                continue
            payload[dataclass_field.name] = _serialize_payload_value(field_value)
        return payload

    @classmethod
    def from_payload(cls: Type[DocumentT], payload: Mapping[str, Any]) -> DocumentT:
        """根据 payload 字典反序列化出强类型文档对象。"""

        type_hints = get_type_hints(cls)
        init_kwargs: Dict[str, Any] = {}

        for dataclass_field in _get_dataclass_fields(cls):
            field_name = dataclass_field.name
            if field_name in payload:
                init_kwargs[field_name] = _deserialize_payload_value(type_hints.get(field_name, Any), payload[field_name])
                continue

            if _annotation_allows_none(type_hints.get(field_name, Any)):
                init_kwargs[field_name] = None
                continue

            has_default = dataclass_field.default is not MISSING
            has_factory = dataclass_field.default_factory is not MISSING  # type: ignore[attr-defined]
            if not has_default and not has_factory:
                raise KeyError("payload 缺少必填字段 {0}".format(field_name))

        return cls(**init_kwargs)


@dataclass
class StoryEventDocument(BaseQdrantDocument):
    """表示 `story_events` collection 中的一条剧情事件文档。"""

    collection_name: ClassVar[CollectionName] = CollectionName.STORY_EVENTS

    #: 事件所属的虚拟时间线编号。
    season_id: SeasonId
    #: 事件所属的动画系列编号。
    series_id: SeriesId
    #: 事件所在的具体集数。
    episode: int
    #: 事件在当前集内的顺序编号。
    time_order: int
    #: 事件从哪个时间点开始对角色可见。
    visible_from: int
    #: 事件到哪个时间点结束可见。
    visible_to: int
    #: 事件所属的剧情分支。
    canon_branch: CanonBranch
    #: 事件标题。
    title: str
    #: 事件摘要描述。
    summary: str
    #: 参与该事件的角色列表。
    participants: List[CharacterId]
    #: 事件重要度，数值越小代表越重要。
    importance: int
    #: 用于标签匹配的关键词列表。
    tags: List[str]
    #: 用于生成 embedding 的检索文本。
    retrieval_text: str

    def __post_init__(self) -> None:
        """在对象创建后执行基础类型收敛与业务校验。"""

        self.season_id = _coerce_enum_value(SeasonId, "season_id", self.season_id)
        self.series_id = _coerce_enum_value(SeriesId, "series_id", self.series_id)
        self.canon_branch = _coerce_enum_value(CanonBranch, "canon_branch", self.canon_branch)
        self.participants = _coerce_enum_list(CharacterId, "participants", self.participants)
        self.title = _normalize_text("title", self.title)
        self.summary = _normalize_text("summary", self.summary)
        self.tags = _normalize_string_list("tags", self.tags, allow_empty=False)
        self.retrieval_text = _normalize_text("retrieval_text", self.retrieval_text)

        if self.importance < 1:
            raise ValueError("字段 importance 必须大于等于 1")
        if not self.participants:
            raise ValueError("字段 participants 不能为空列表")

        _validate_time_window(self.visible_from, self.visible_to, "StoryEventDocument")


@dataclass
class CharacterRelationDocument(BaseQdrantDocument):
    """表示 `character_relations` collection 中的一条角色关系文档。"""

    collection_name: ClassVar[CollectionName] = CollectionName.CHARACTER_RELATIONS

    #: 对关系进行主观判断的一方角色。
    subject_character_id: CharacterId
    #: 被主观判断或被提及的目标角色。
    object_character_id: CharacterId
    #: 当前关系所属的虚拟时间线编号。
    season_id: SeasonId
    #: 当前关系所属的动画系列编号。
    series_id: SeriesId
    #: 当前关系从哪个时间点开始可见。
    visible_from: int
    #: 当前关系到哪个时间点结束可见。
    visible_to: int
    #: 当前关系所属的剧情分支。
    canon_branch: CanonBranch
    #: 对这段关系的简短标签。
    relation_label: str
    #: 对当前关系状态的摘要描述。
    state_summary: str
    #: 提到目标角色时的说话方式提示。
    speech_hint: str
    #: 当前角色对目标角色的常用称呼。
    object_character_nickname: str
    #: 用于标签匹配的关键词列表。
    tags: List[str]
    #: 用于生成 embedding 的检索文本。
    retrieval_text: str

    def __post_init__(self) -> None:
        """在对象创建后执行基础类型收敛与业务校验。"""

        self.subject_character_id = _coerce_enum_value(CharacterId, "subject_character_id", self.subject_character_id)
        self.object_character_id = _coerce_enum_value(CharacterId, "object_character_id", self.object_character_id)
        self.season_id = _coerce_enum_value(SeasonId, "season_id", self.season_id)
        self.series_id = _coerce_enum_value(SeriesId, "series_id", self.series_id)
        self.canon_branch = _coerce_enum_value(CanonBranch, "canon_branch", self.canon_branch)
        self.relation_label = _normalize_text("relation_label", self.relation_label)
        self.state_summary = _normalize_text("state_summary", self.state_summary)
        self.speech_hint = _normalize_text("speech_hint", self.speech_hint, allow_empty=True)
        self.object_character_nickname = _normalize_text("object_character_nickname", self.object_character_nickname, allow_empty=True)
        self.tags = _normalize_string_list("tags", self.tags, allow_empty=False)
        self.retrieval_text = _normalize_text("retrieval_text", self.retrieval_text)

        if self.subject_character_id == self.object_character_id:
            raise ValueError("subject_character_id 与 object_character_id 不能相同")

        _validate_time_window(self.visible_from, self.visible_to, "CharacterRelationDocument")


@dataclass
class LoreEntryDocument(BaseQdrantDocument):
    """表示 `lore_entries` collection 中的一条世界观设定文档。"""

    collection_name: ClassVar[CollectionName] = CollectionName.LORE_ENTRIES

    #: 该条目的适用范围类型，例如全局或特定系列。
    scope_type: ScopeType
    #: 该条目适用的系列范围列表。
    series_ids: Optional[List[SeriesId]]
    #: 该条目适用的虚拟时间线范围列表。
    season_ids: Optional[List[SeasonId]]  
    #: 该条目从哪个时间点开始可见，可为空。
    visible_from: Optional[int]
    #: 该条目到哪个时间点结束可见，可为空。
    visible_to: Optional[int]
    #: 该条目所属的剧情分支。
    canon_branch: CanonBranch
    #: 条目的标题。
    title: str
    #: 条目的详细说明文本。
    content: str
    #: 用于生成 embedding 的检索文本。
    retrieval_text: str
    #: 用于标签匹配的关键词列表。
    tags: List[str]

    def __post_init__(self) -> None:
        """在对象创建后执行基础类型收敛与业务校验。"""

        self.scope_type = _coerce_enum_value(ScopeType, "scope_type", self.scope_type)
        self.canon_branch = _coerce_enum_value(CanonBranch, "canon_branch", self.canon_branch)
        self.series_ids = None if self.series_ids is None else _coerce_enum_list(SeriesId, "series_ids", self.series_ids)
        self.season_ids = None if self.season_ids is None else _coerce_enum_list(SeasonId, "season_ids", self.season_ids)
        self.title = _normalize_text("title", self.title)
        self.content = _normalize_text("content", self.content)
        self.retrieval_text = _normalize_text("retrieval_text", self.retrieval_text)
        self.tags = _normalize_string_list("tags", self.tags, allow_empty=False)

        if self.scope_type == ScopeType.SERIES and not self.series_ids:
            raise ValueError("当 scope_type 为 SERIES 时，series_ids 不能为空")

        _validate_time_window(self.visible_from, self.visible_to, "LoreEntryDocument")


@dataclass
class RetrievalContext:
    """描述一次检索请求的公共上下文信息。"""

    #: 当前检索所处的时间点。
    current_time: int
    #: 当前对话中的主视角角色。
    current_character_id: Optional[CharacterId] = None
    #: 当前对话限定的系列编号。
    current_series_id: Optional[SeriesId] = None
    #: 当前对话限定的虚拟时间线编号。
    current_season_id: Optional[SeasonId] = None
    #: 当前对话限定的剧情分支。
    current_canon_branch: Optional[CanonBranch] = None

    def __post_init__(self) -> None:
        """在对象创建后收敛上下文中的枚举字段类型。"""

        if self.current_character_id is not None:
            self.current_character_id = _coerce_enum_value(CharacterId, "current_character_id", self.current_character_id)
        if self.current_series_id is not None:
            self.current_series_id = _coerce_enum_value(SeriesId, "current_series_id", self.current_series_id)
        if self.current_season_id is not None:
            self.current_season_id = _coerce_enum_value(SeasonId, "current_season_id", self.current_season_id)
        if self.current_canon_branch is not None:
            self.current_canon_branch = _coerce_enum_value(CanonBranch, "current_canon_branch", self.current_canon_branch)


@dataclass
class StoryEventQuery:
    """描述 `story_events` collection 的查询偏好。"""

    #: 是否要求事件参与角色必须命中当前角色。
    require_character_match: bool = True
    #: 是否要求限制到当前系列。
    limit_to_series: bool = True
    #: 是否要求限制到当前虚拟时间线。
    limit_to_season: bool = True
    #: 是否要求限制到当前剧情分支。
    limit_to_canon_branch: bool = True


@dataclass
class CharacterRelationQuery:
    """描述 `character_relations` collection 的查询偏好。"""

    #: 是否启用小剧场模式下的双角色直接插入策略。
    use_direct_pair_insert: bool = False
    #: 小剧场模式下需要直接匹配的双角色组合。
    direct_insert_pair: Optional[Tuple[CharacterId, CharacterId]] = None
    #: 是否要求限制到当前系列。
    limit_to_series: bool = True
    #: 是否要求限制到当前虚拟时间线。
    limit_to_season: bool = True
    #: 是否要求限制到当前剧情分支。
    limit_to_canon_branch: bool = True

    def __post_init__(self) -> None:
        """在对象创建后校验小剧场双角色参数是否完整且合法。"""

        if self.direct_insert_pair is not None:
            if len(self.direct_insert_pair) != 2:
                raise ValueError("direct_insert_pair 必须恰好包含两个角色")
            self.direct_insert_pair = (
                _coerce_enum_value(CharacterId, "direct_insert_pair[0]", self.direct_insert_pair[0]),
                _coerce_enum_value(CharacterId, "direct_insert_pair[1]", self.direct_insert_pair[1]),
            )
            if self.direct_insert_pair[0] == self.direct_insert_pair[1]:
                raise ValueError("direct_insert_pair 中的两个角色不能相同")

        if self.use_direct_pair_insert and self.direct_insert_pair is None:
            raise ValueError("启用 direct pair insert 时必须提供 direct_insert_pair")


@dataclass
class LoreQuery:
    """描述 `lore_entries` collection 的查询偏好。"""

    #: 是否要求按系列范围做过滤。
    require_series_match: bool = True
    #: 是否要求按虚拟时间线范围做过滤。
    require_season_match: bool = True
    #: 是否要求按时间窗口做过滤。
    require_time_window: bool = True
    #: 是否要求限制到当前剧情分支。
    limit_to_canon_branch: bool = True


__all__ = [
    "BaseQdrantDocument",
    "CanonBranch",
    "CharacterId",
    "CharacterRelationDocument",
    "CharacterRelationQuery",
    "CollectionName",
    "LoreEntryDocument",
    "LoreQuery",
    "RetrievalContext",
    "ScopeType",
    "SeasonId",
    "SeriesId",
    "StoryEventDocument",
    "StoryEventQuery",
]
