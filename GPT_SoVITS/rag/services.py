"""Qdrant RAG service 层实现。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Generic, List, Mapping, Optional, Sequence, Tuple, Type, TypeVar, Union
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from qdrant_client import QdrantClient
from qdrant_client import models as qdrant_models
from sentence_transformers import SentenceTransformer

from .models import (
    BaseQdrantDocument,
    CharacterRelationDocument,
    CharacterRelationQuery,
    CollectionName,
    LoreEntryDocument,
    LoreQuery,
    RetrievalContext,
    StoryEventDocument,
    StoryEventQuery,
)


DocumentT = TypeVar("DocumentT", bound=BaseQdrantDocument)
PointId = str
QdrantPointId = Union[int, str, UUID]


def _normalize_keyword(value: str) -> str:
    """将关键词标准化为便于比较的形式。"""

    return value.strip().casefold()


def _normalize_keyword_list(values: Optional[Sequence[str]]) -> List[str]:
    """将关键词列表标准化，并移除空值。"""

    if values is None:
        return []

    normalized_values: List[str] = []
    for raw_value in values:
        normalized_value = _normalize_keyword(raw_value)
        if normalized_value:
            normalized_values.append(normalized_value)
    return normalized_values


def _serialize_qdrant_result(value: Any) -> Any:
    """尽量将 Qdrant SDK 返回对象转换为普通 Python 数据。"""

    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return value


class RetrievalMode(str, Enum):
    """定义 service 层支持的检索模式。"""

    VECTOR = "vector"
    KEYWORD = "keyword"
    DIRECT = "direct"


class QdrantConnectType(str, Enum):
    """定义 Qdrant 连接方式枚举。"""

    MEMORY = "memory"
    LOCAL = "local"
    GRPC = "grpc"
    HTTP = "http"


@dataclass
class RagServiceConfig:
    """描述 Qdrant RAG service 的初始化配置。"""

    #: Qdrant 的连接位置
    qdrant_location: str = "../knowledge_base/default_world_info"
    # Qdrant 的连接方式，包含存储到内存，本地路径和服务器地址三种。
    qdrant_connect_type: QdrantConnectType = QdrantConnectType.LOCAL
    #: sentence-transformer 模型的本地路径。
    embedding_model_path: str = "pretrained_models/multilingual-e5-small"
    #: 向量距离类型，默认使用余弦距离。
    distance: qdrant_models.Distance = qdrant_models.Distance.COSINE
    #: 批量编码时的默认 batch size。
    default_batch_size: int = 64
    #: 查询接口默认返回的 top-k 数量。
    default_top_k: int = 5


@dataclass
class PointRecord(Generic[DocumentT]):
    """将 point id 与一个文档对象绑定在一起。"""

    #: 当前记录的 point id，允许留空后由 service 自动生成。
    point_id: Optional[PointId]
    #: 当前 point 对应的强类型文档对象。
    document: DocumentT


@dataclass
class _ResolvedPointRecord(Generic[DocumentT]):
    """表示已经补齐并归一化 point id 的内部记录对象。"""

    #: 已确保非空且符合 Qdrant 要求的 point id。
    point_id: QdrantPointId
    #: 当前 point 对应的强类型文档对象。
    document: DocumentT


@dataclass
class RagDatasetBundle:
    """表示一次性初始化整个数据库时要导入的数据集合。"""

    #: 要写入 `story_events` collection 的记录。
    story_events: List[PointRecord[StoryEventDocument]] = field(default_factory=list)
    #: 要写入 `character_relations` collection 的记录。
    character_relations: List[PointRecord[CharacterRelationDocument]] = field(default_factory=list)
    #: 要写入 `lore_entries` collection 的记录。
    lore_entries: List[PointRecord[LoreEntryDocument]] = field(default_factory=list)


@dataclass
class QueryHit(Generic[DocumentT]):
    """表示一条查询命中结果。"""

    #: 命中结果对应的 point id。
    point_id: PointId
    #: 命中的文档对象。
    document: DocumentT
    #: 向量检索分数，非向量模式下可能为空。
    score: Optional[float]
    #: 命中的来源模式。
    source: RetrievalMode


@dataclass
class UpsertReport:
    """表示一次写入操作的统计结果。"""

    #: 当前报告对应的 collection 名称，建库场景下可为 `database`。
    collection_name: str
    #: 本次操作计划处理的记录数量。
    requested_count: int = 0
    #: 本次操作成功写入的记录数量。
    success_count: int = 0
    #: 本次操作失败的记录数量。
    failure_count: int = 0
    #: 成功写入的 point id 列表。
    success_point_ids: List[PointId] = field(default_factory=list)
    #: 写入失败的 point id 列表。
    failed_point_ids: List[PointId] = field(default_factory=list)
    #: 本次操作中由 service 自动生成的 point id 列表。
    generated_point_ids: List[PointId] = field(default_factory=list)

    def merge(self, other: "UpsertReport") -> None:
        """将另一份写入报告聚合到当前报告中。"""

        self.requested_count += other.requested_count
        self.success_count += other.success_count
        self.failure_count += other.failure_count
        self.success_point_ids.extend(other.success_point_ids)
        self.failed_point_ids.extend(other.failed_point_ids)
        self.generated_point_ids.extend(other.generated_point_ids)


@dataclass
class DeleteReport:
    """表示一次删除操作的统计结果。"""

    #: 当前报告对应的 collection 名称。
    collection_name: str
    #: 本次删除请求包含的目标数量。
    requested_count: int
    #: service 认为已经提交删除的数量。
    deleted_count: int
    #: 删除失败的 point id 列表。
    failed_point_ids: List[PointId] = field(default_factory=list)


@dataclass
class _PayloadIndexSpec:
    """描述某个 payload 字段应建立的索引类型。"""

    #: 需要建立索引的字段名称。
    field_name: str
    #: Qdrant payload schema 类型名称。
    schema_name: str


@dataclass
class _CollectionSpec(Generic[DocumentT]):
    """描述一个 collection 的静态注册信息。"""

    #: collection 的名字。
    collection_name: CollectionName
    #: collection 对应的文档 dataclass 类型。
    document_type: Type[DocumentT]
    #: 该 collection 需要建立的 payload index 列表。
    payload_indexes: List[_PayloadIndexSpec]


class EmbeddingProvider:
    """负责加载并调用 sentence-transformer 模型。"""

    def __init__(self, model_path: str, default_batch_size: int = 64) -> None:
        """初始化 embedding provider。"""

        self._model_path: str = model_path
        self._default_batch_size: int = default_batch_size
        self._model: Any = None
        self._dimension: Optional[int] = None

    def ensure_loaded(self) -> None:
        """确保底层模型已经完成加载。"""

        if self._model is not None:
            return

        self._model = SentenceTransformer(self._model_path)
        self._dimension = self._model.get_sentence_embedding_dimension()
        if not self._dimension:
            raise ValueError("无法从 embedding 模型中获取有效向量维度。")

    def is_loaded(self) -> bool:
        """返回当前 embedding 模型是否已经加载。"""

        return self._model is not None

    def get_dimension(self) -> int:
        """返回当前 embedding 模型的向量维度。"""

        self.ensure_loaded()
        if self._dimension is None:
            raise ValueError("embedding 模型的向量维度尚未初始化。")
        return self._dimension

    def encode_text(self, text: str) -> List[float]:
        """对单条文本执行向量编码。"""

        vectors = self.encode_texts([text])
        return vectors[0]

    def encode_texts(self, texts: Sequence[str], batch_size: Optional[int] = None) -> List[List[float]]:
        """对多条文本执行批量向量编码。"""

        self.ensure_loaded()
        if not texts:
            return []

        actual_batch_size = batch_size or self._default_batch_size
        encoded = self._model.encode(list(texts), batch_size=actual_batch_size, show_progress_bar=False)

        vectors: List[List[float]] = []
        for vector in encoded:
            if hasattr(vector, "tolist"):
                vectors.append(list(vector.tolist()))
            else:
                vectors.append(list(vector))
        return vectors

    def close(self) -> None:
        """释放当前 provider 持有的模型引用。"""

        self._model = None
        self._dimension = None


class CollectionRegistry:
    """维护 collection 与文档类型之间的静态映射关系。"""

    def __init__(self) -> None:
        """初始化默认的 collection 注册表。"""

        self._specs: Dict[CollectionName, _CollectionSpec[Any]] = {
            CollectionName.STORY_EVENTS: _CollectionSpec(
                collection_name=CollectionName.STORY_EVENTS,
                document_type=StoryEventDocument,
                payload_indexes=[
                    _PayloadIndexSpec("series_id", "KEYWORD"),
                    _PayloadIndexSpec("season_id", "INTEGER"),
                    _PayloadIndexSpec("episode", "INTEGER"),
                    _PayloadIndexSpec("time_order", "INTEGER"),
                    _PayloadIndexSpec("visible_from", "INTEGER"),
                    _PayloadIndexSpec("visible_to", "INTEGER"),
                    _PayloadIndexSpec("canon_branch", "KEYWORD"),
                    _PayloadIndexSpec("participants", "KEYWORD"),
                ],
            ),
            CollectionName.CHARACTER_RELATIONS: _CollectionSpec(
                collection_name=CollectionName.CHARACTER_RELATIONS,
                document_type=CharacterRelationDocument,
                payload_indexes=[
                    _PayloadIndexSpec("subject_character_id", "KEYWORD"),
                    _PayloadIndexSpec("object_character_id", "KEYWORD"),
                    _PayloadIndexSpec("series_id", "KEYWORD"),
                    _PayloadIndexSpec("season_id", "INTEGER"),
                    _PayloadIndexSpec("visible_from", "INTEGER"),
                    _PayloadIndexSpec("visible_to", "INTEGER"),
                    _PayloadIndexSpec("canon_branch", "KEYWORD"),
                ],
            ),
            CollectionName.LORE_ENTRIES: _CollectionSpec(
                collection_name=CollectionName.LORE_ENTRIES,
                document_type=LoreEntryDocument,
                payload_indexes=[
                    _PayloadIndexSpec("scope_type", "KEYWORD"),
                    _PayloadIndexSpec("series_ids", "KEYWORD"),
                    _PayloadIndexSpec("season_ids", "INTEGER"),
                    _PayloadIndexSpec("visible_from", "INTEGER"),
                    _PayloadIndexSpec("visible_to", "INTEGER"),
                    _PayloadIndexSpec("canon_branch", "KEYWORD"),
                ],
            ),
        }

    def get(self, collection_name: CollectionName) -> _CollectionSpec[Any]:
        """根据 collection 名称获取注册信息。"""

        return self._specs[collection_name]

    def all_specs(self) -> List[_CollectionSpec[Any]]:
        """返回当前注册表中的全部 collection 定义。"""

        return list(self._specs.values())


class QdrantRagService:
    """统一管理 Qdrant client、embedding 模型与 typed 检索接口。"""

    def __init__(self, config: RagServiceConfig) -> None:
        """初始化 service，但不立即执行重型依赖加载。"""

        self._config: RagServiceConfig = config
        self._embedding_provider: EmbeddingProvider = EmbeddingProvider(
            model_path=config.embedding_model_path,
            default_batch_size=config.default_batch_size,
        )
        self._registry: CollectionRegistry = CollectionRegistry()
        self._client: Any = None

    def initialize(self) -> None:
        """初始化底层 QdrantClient，并确保模型信息可用。"""

        if self._client is None:
            if self._config.qdrant_connect_type == QdrantConnectType.MEMORY:
                self._client = QdrantClient(":memory:")
            elif self._config.qdrant_connect_type == QdrantConnectType.LOCAL:
                self._client = QdrantClient(path=self._config.qdrant_location)
            elif self._config.qdrant_connect_type == QdrantConnectType.GRPC:
                self._client = QdrantClient(url=self._config.qdrant_location, prefer_grpc=True)
            elif self._config.qdrant_connect_type == QdrantConnectType.HTTP:
                self._client = QdrantClient(url=self._config.qdrant_location, prefer_grpc=False)
            else:
                raise ValueError("不支持的 Qdrant 连接类型：{}".format(self._config.qdrant_connect_type))

        self._embedding_provider.ensure_loaded()

    def close(self) -> None:
        """关闭当前 service 持有的资源引用。"""

        self._client = None
        self._embedding_provider.close()

    def warm_up_embedding_model(self) -> None:
        """主动预热 embedding 模型。"""

        self._embedding_provider.ensure_loaded()

    def health_check(self) -> Dict[str, Any]:
        """返回当前 service 的基础健康状态。"""

        status: Dict[str, Any] = {
            "client_initialized": self._client is not None,
            "embedding_model_loaded": self._embedding_provider.is_loaded(),
            "vector_size": None,
            "collection_count": None,
            "qdrant_connected": False,
            "qdrant_error": None
        }

        if self._embedding_provider.is_loaded():
            status["vector_size"] = self._embedding_provider.get_dimension()

        if self._client is not None:
            try:
                status["qdrant_connected"] = True
                status["collection_count"] = len(self.list_collections())
            except Exception as exc:  # pragma: no cover - 依赖运行时环境
                status["qdrant_connected"] = False
                status["qdrant_error"] = str(exc)
        else:
            status["qdrant_connected"] = False

        return status

    def ensure_collections_exist(self) -> None:
        """确保三张业务 collection 已存在，并具备向量配置。"""

        self.initialize()
        vector_size = self._embedding_provider.get_dimension()
        for spec in self._registry.all_specs():
            if not self._collection_exists(spec.collection_name):
                self._create_collection(spec.collection_name, vector_size)
            self._ensure_payload_indexes(spec)

    def create_database(self, bundle: RagDatasetBundle, drop_existing: bool = True) -> UpsertReport:
        """创建全新的数据库，并导入 bundle 中的全部数据。"""

        self.initialize()
        vector_size = self._embedding_provider.get_dimension()
        aggregate_report = UpsertReport(collection_name="database")

        for spec in self._registry.all_specs():
            if drop_existing and self._collection_exists(spec.collection_name):
                self._client.delete_collection(collection_name=spec.collection_name.value)
            if not self._collection_exists(spec.collection_name):
                self._create_collection(spec.collection_name, vector_size)
            self._ensure_payload_indexes(spec)

        aggregate_report.merge(self.upsert_story_events(bundle.story_events))
        aggregate_report.merge(self.upsert_character_relations(bundle.character_relations))
        aggregate_report.merge(self.upsert_lore_entries(bundle.lore_entries))
        return aggregate_report

    def upsert_story_events(self, records: Sequence[PointRecord[StoryEventDocument]]) -> UpsertReport:
        """向 `story_events` collection 批量写入或更新数据。"""

        return self._upsert_documents(CollectionName.STORY_EVENTS, records)

    def upsert_character_relations(self, records: Sequence[PointRecord[CharacterRelationDocument]]) -> UpsertReport:
        """向 `character_relations` collection 批量写入或更新数据。"""

        return self._upsert_documents(CollectionName.CHARACTER_RELATIONS, records)

    def upsert_lore_entries(self, records: Sequence[PointRecord[LoreEntryDocument]]) -> UpsertReport:
        """向 `lore_entries` collection 批量写入或更新数据。"""

        return self._upsert_documents(CollectionName.LORE_ENTRIES, records)

    def delete_by_point_ids(self, collection_name: CollectionName, point_ids: Sequence[PointId]) -> DeleteReport:
        """根据 point id 列表删除指定 collection 中的数据。"""

        self.initialize()
        point_id_list: List[QdrantPointId] = [
            self._normalize_point_id(collection_name, str(point_id)) for point_id in point_ids
        ]
        if not point_id_list:
            return DeleteReport(collection_name=collection_name.value, requested_count=0, deleted_count=0)

        selector = qdrant_models.PointIdsList(points=point_id_list)
        self._client.delete(collection_name=collection_name.value, points_selector=selector)
        return DeleteReport(
            collection_name=collection_name.value,
            requested_count=len(point_id_list),
            deleted_count=len(point_id_list),
        )

    def delete_by_filter(self, collection_name: CollectionName, delete_filter: qdrant_models.Filter) -> DeleteReport:
        """根据 Qdrant filter 删除指定 collection 中的数据。"""

        self.initialize()
        count_before = self.count_points(collection_name)
        selector = qdrant_models.FilterSelector(filter=delete_filter)
        self._client.delete(collection_name=collection_name.value, points_selector=selector)
        count_after = self.count_points(collection_name)
        deleted_count = max(0, count_before - count_after)
        return DeleteReport(
            collection_name=collection_name.value,
            requested_count=deleted_count,
            deleted_count=deleted_count,
        )

    def query_story_events(
        self,
        query_text: Optional[str],
        context: RetrievalContext,
        options: StoryEventQuery,
        query_mode: RetrievalMode = RetrievalMode.VECTOR,
        tag_keywords: Optional[Sequence[str]] = None,
        top_k: Optional[int] = None,
    ) -> List[QueryHit[StoryEventDocument]]:
        """查询 `story_events` collection。"""

        if query_mode == RetrievalMode.DIRECT:
            raise ValueError("story_events 不支持 DIRECT 查询模式。")
        return self._query_documents(
            collection_name=CollectionName.STORY_EVENTS,
            document_type=StoryEventDocument,
            query_text=query_text,
            query_mode=query_mode,
            context=context,
            options=options,
            tag_keywords=tag_keywords,
            top_k=top_k,
        )

    def query_character_relations(
        self,
        query_text: Optional[str],
        context: RetrievalContext,
        options: CharacterRelationQuery,
        query_mode: RetrievalMode = RetrievalMode.VECTOR,
        tag_keywords: Optional[Sequence[str]] = None,
        top_k: Optional[int] = None,
    ) -> List[QueryHit[CharacterRelationDocument]]:
        """查询 `character_relations` collection。"""

        return self._query_documents(
            collection_name=CollectionName.CHARACTER_RELATIONS,
            document_type=CharacterRelationDocument,
            query_text=query_text,
            query_mode=query_mode,
            context=context,
            options=options,
            tag_keywords=tag_keywords,
            top_k=top_k,
        )

    def query_lore_entries(
        self,
        query_text: Optional[str],
        context: RetrievalContext,
        options: LoreQuery,
        query_mode: RetrievalMode = RetrievalMode.VECTOR,
        tag_keywords: Optional[Sequence[str]] = None,
        top_k: Optional[int] = None,
    ) -> List[QueryHit[LoreEntryDocument]]:
        """查询 `lore_entries` collection。"""

        if query_mode == RetrievalMode.DIRECT:
            raise ValueError("lore_entries 不支持 DIRECT 查询模式。")
        return self._query_documents(
            collection_name=CollectionName.LORE_ENTRIES,
            document_type=LoreEntryDocument,
            query_text=query_text,
            query_mode=query_mode,
            context=context,
            options=options,
            tag_keywords=tag_keywords,
            top_k=top_k,
        )

    def query_all(
        self,
        query_text: Optional[str],
        context: RetrievalContext,
        tag_keywords: Optional[Sequence[str]] = None,
        query_modes: Optional[Mapping[CollectionName, RetrievalMode]] = None,
        top_k_per_collection: int = 5,
        story_options: Optional[StoryEventQuery] = None,
        relation_options: Optional[CharacterRelationQuery] = None,
        lore_options: Optional[LoreQuery] = None,
    ) -> Dict[CollectionName, List[QueryHit[Any]]]:
        """一次性查询三张表，并按 collection 分组返回结果。"""

        story_mode = RetrievalMode.VECTOR
        relation_mode = RetrievalMode.VECTOR
        lore_mode = RetrievalMode.VECTOR
        if query_modes is not None:
            story_mode = query_modes.get(CollectionName.STORY_EVENTS, story_mode)
            relation_mode = query_modes.get(CollectionName.CHARACTER_RELATIONS, relation_mode)
            lore_mode = query_modes.get(CollectionName.LORE_ENTRIES, lore_mode)

        return {
            CollectionName.STORY_EVENTS: self.query_story_events(
                query_text=query_text,
                context=context,
                options=story_options or StoryEventQuery(),
                query_mode=story_mode,
                tag_keywords=tag_keywords,
                top_k=top_k_per_collection,
            ),
            CollectionName.CHARACTER_RELATIONS: self.query_character_relations(
                query_text=query_text,
                context=context,
                options=relation_options or CharacterRelationQuery(),
                query_mode=relation_mode,
                tag_keywords=tag_keywords,
                top_k=top_k_per_collection,
            ),
            CollectionName.LORE_ENTRIES: self.query_lore_entries(
                query_text=query_text,
                context=context,
                options=lore_options or LoreQuery(),
                query_mode=lore_mode,
                tag_keywords=tag_keywords,
                top_k=top_k_per_collection,
            ),
        }

    def count_points(self, collection_name: CollectionName) -> int:
        """返回指定 collection 当前存储的 point 数量。"""

        self.initialize()
        count_result = self._client.count(collection_name=collection_name.value)
        return int(getattr(count_result, "count", 0))

    def list_collections(self) -> List[str]:
        """列出当前数据库中的所有 collection 名称。"""

        self.initialize()
        collections_result = self._client.get_collections()
        collections = getattr(collections_result, "collections", [])
        return [str(getattr(item, "name", "")) for item in collections]

    def get_collection_info(self, collection_name: CollectionName) -> Dict[str, Any]:
        """返回指定 collection 的基础信息。"""

        self.initialize()
        info = self._client.get_collection(collection_name=collection_name.value)
        serialized_info = _serialize_qdrant_result(info)
        if isinstance(serialized_info, dict):
            return serialized_info
        return {"value": serialized_info}

    def _collection_exists(self, collection_name: CollectionName) -> bool:
        """判断指定 collection 是否已经存在。"""

        if hasattr(self._client, "collection_exists"):
            return bool(self._client.collection_exists(collection_name=collection_name.value))
        return collection_name.value in self.list_collections()

    def _create_collection(self, collection_name: CollectionName, vector_size: int) -> None:
        """根据当前 embedding 配置创建新的 collection。"""

        vector_params = qdrant_models.VectorParams(
            size=vector_size,
            distance=self._resolve_distance(self._config.distance),
        )
        self._client.create_collection(collection_name=collection_name.value, vectors_config=vector_params)

    def _ensure_payload_indexes(self, spec: _CollectionSpec[Any]) -> None:
        """为某个 collection 建立规划中的 payload index。"""

        if not hasattr(self._client, "create_payload_index"):
            return

        payload_schema_type = getattr(qdrant_models, "PayloadSchemaType", None)
        for index_spec in spec.payload_indexes:
            if payload_schema_type is None:
                continue
            field_schema = getattr(payload_schema_type, index_spec.schema_name, None)
            if field_schema is None:
                continue
            try:
                self._client.create_payload_index(
                    collection_name=spec.collection_name.value,
                    field_name=index_spec.field_name,
                    field_schema=field_schema,
                )
            except Exception:
                continue

    def _resolve_distance(self, distance_value: Union[str, Any]) -> Any:
        """将字符串形式的距离类型解析为 Qdrant 的枚举对象。"""

        if isinstance(distance_value, str):
            return getattr(qdrant_models.Distance, distance_value.upper())
        return distance_value

    def _generate_point_id(self, collection_name: CollectionName) -> PointId:
        """为一条记录生成默认的 point id。"""

        return str(uuid4())

    def _normalize_point_id(self, collection_name: CollectionName, point_id: PointId) -> PointId:
        """将外部 point id 归一化为当前 Qdrant 可接受的稳定 UUID 字符串。"""
 
        try:
            return str(UUID(str(point_id)))
        except (TypeError, ValueError):
            return str(uuid5(NAMESPACE_URL, "{0}:{1}".format(collection_name.value, point_id)))

    def _ensure_point_ids(
        self,
        collection_name: CollectionName,
        records: Sequence[PointRecord[DocumentT]],
    ) -> Tuple[List[_ResolvedPointRecord[DocumentT]], List[PointId]]:
        """补齐记录列表中缺失的 point id。"""

        completed_records: List[_ResolvedPointRecord[DocumentT]] = []
        generated_ids: List[PointId] = []
        for record in records:
            point_id = record.point_id
            if point_id is None:
                point_id = self._generate_point_id(collection_name)
                generated_ids.append(point_id)
            normalized_point_id = self._normalize_point_id(collection_name, point_id)
            completed_records.append(_ResolvedPointRecord(point_id=normalized_point_id, document=record.document))
        return completed_records, generated_ids

    def _get_document_retrieval_text(self, document: BaseQdrantDocument) -> str:
        """读取文档对象上的检索文本，并为静态检查提供明确类型。"""

        retrieval_text = getattr(document, "retrieval_text", None)
        if not isinstance(retrieval_text, str):
            raise TypeError("文档对象缺少字符串类型的 retrieval_text 字段。")
        return retrieval_text

    def _build_points(
        self,
        collection_name: CollectionName,
        records: Sequence[PointRecord[DocumentT]],
    ) -> Tuple[List[Any], List[PointId]]:
        """将文档记录构造成可提交给 Qdrant 的 PointStruct 列表。"""

        completed_records, generated_ids = self._ensure_point_ids(collection_name, records)
        vectors = self._embedding_provider.encode_texts(
            [self._get_document_retrieval_text(record.document) for record in completed_records],
            batch_size=self._config.default_batch_size,
        )

        points: List[Any] = []
        for record, vector in zip(completed_records, vectors):
            points.append(
                qdrant_models.PointStruct(
                    id=record.point_id,
                    vector=vector,
                    payload=record.document.to_payload(),
                )
            )
        return points, generated_ids

    def _upsert_documents(
        self,
        collection_name: CollectionName,
        records: Sequence[PointRecord[DocumentT]],
    ) -> UpsertReport:
        """执行某个 collection 的通用 upsert 流程。"""

        self.initialize()
        report = UpsertReport(collection_name=collection_name.value, requested_count=len(records))
        if not records:
            return report

        if not self._collection_exists(collection_name):
            vector_size = self._embedding_provider.get_dimension()
            self._create_collection(collection_name, vector_size)
            self._ensure_payload_indexes(self._registry.get(collection_name))

        points, generated_ids = self._build_points(collection_name, records)
        self._client.upsert(collection_name=collection_name.value, points=points)

        report.generated_point_ids.extend(generated_ids)
        report.success_count = len(points)
        report.success_point_ids.extend([str(getattr(point, "id")) for point in points])
        return report

    def _query_documents(
        self,
        collection_name: CollectionName,
        document_type: Type[DocumentT],
        query_text: Optional[str],
        query_mode: RetrievalMode,
        context: RetrievalContext,
        options: Union[LoreQuery, StoryEventQuery, CharacterRelationQuery],
        tag_keywords: Optional[Sequence[str]],
        top_k: Optional[int],
    ) -> List[QueryHit[DocumentT]]:
        """执行某个 collection 的通用查询流程。"""

        self.initialize()
        limit = top_k or self._config.default_top_k

        if query_mode == RetrievalMode.VECTOR:
            if query_text is None or not query_text.strip():
                raise ValueError("VECTOR 模式下必须提供 query_text。")
            return self._vector_query(collection_name, document_type, query_text, context, options, limit)

        if query_mode == RetrievalMode.KEYWORD:
            normalized_keywords = _normalize_keyword_list(tag_keywords)
            if not normalized_keywords:
                raise ValueError("KEYWORD 模式下必须提供非空 tag_keywords。")
            return self._keyword_query(collection_name, document_type, normalized_keywords, context, options, limit)

        if query_mode == RetrievalMode.DIRECT:
            if collection_name != CollectionName.CHARACTER_RELATIONS:
                raise ValueError("只有 character_relations 支持 DIRECT 模式。")
            if not isinstance(options, CharacterRelationQuery):
                raise ValueError("直接查询时，options 参数必须是 CharacterRelationQuery 的对象。")
            return self._direct_relation_query(document_type, context, options, limit)

        raise ValueError("不支持的查询模式: {0}".format(query_mode))

    def _vector_query(
        self,
        collection_name: CollectionName,
        document_type: Type[DocumentT],
        query_text: str,
        context: RetrievalContext,
        options: Union[LoreQuery, StoryEventQuery, CharacterRelationQuery],
        limit: int,
    ) -> List[QueryHit[DocumentT]]:
        """执行向量检索模式的查询。"""

        vector = self._embedding_provider.encode_text(query_text)
        query_filter = self._build_base_filter(collection_name, context, options)
        candidate_limit = max(limit * 5, limit)

        raw_response = self._client.query_points(
            collection_name=collection_name.value,
            query=vector,
            query_filter=query_filter,
            limit=candidate_limit,
            with_payload=True,
        )
        raw_points = list(getattr(raw_response, "points", []))

        results: List[QueryHit[DocumentT]] = []
        for point in raw_points:
            payload = getattr(point, "payload", None) or {}
            document = document_type.from_payload(payload)
            if not self._document_matches_runtime_constraints(collection_name, document, context, options):
                continue
            results.append(
                QueryHit(
                    point_id=str(getattr(point, "id")),
                    document=document,
                    score=getattr(point, "score", None),
                    source=RetrievalMode.VECTOR,
                )
            )
            if len(results) >= limit:
                break
        return results

    def _keyword_query(
        self,
        collection_name: CollectionName,
        document_type: Type[DocumentT],
        normalized_keywords: Sequence[str],
        context: RetrievalContext,
        options: Union[LoreQuery, StoryEventQuery, CharacterRelationQuery],
        limit: int,
    ) -> List[QueryHit[DocumentT]]:
        """执行纯关键词模式的查询。"""

        scroll_filter = self._build_base_filter(collection_name, context, options)
        raw_points = self._scroll_points(collection_name, scroll_filter, max(limit * 10, limit))

        results: List[QueryHit[DocumentT]] = []
        for point in raw_points:
            payload = getattr(point, "payload", None) or {}
            document = document_type.from_payload(payload)
            if not self._document_matches_runtime_constraints(collection_name, document, context, options):
                continue
            if not self._document_matches_keywords(document, normalized_keywords):
                continue
            results.append(
                QueryHit(
                    point_id=str(getattr(point, "id")),
                    document=document,
                    score=None,
                    source=RetrievalMode.KEYWORD,
                )
            )
            if len(results) >= limit:
                break
        return results

    def _direct_relation_query(
        self,
        document_type: Type[DocumentT],
        context: RetrievalContext,
        options: CharacterRelationQuery,
        limit: int,
    ) -> List[QueryHit[DocumentT]]:
        """执行角色关系的小剧场 direct 查询。"""

        if not options.use_direct_pair_insert or options.direct_insert_pair is None:
            raise ValueError("DIRECT 模式要求提供 direct_insert_pair。")

        scroll_filter = self._build_base_filter(CollectionName.CHARACTER_RELATIONS, context, options)
        raw_points = self._scroll_points(CollectionName.CHARACTER_RELATIONS, scroll_filter, max(limit * 10, limit))
        expected_pair = options.direct_insert_pair

        results: List[QueryHit[DocumentT]] = []
        for point in raw_points:
            payload = getattr(point, "payload", None) or {}
            document = document_type.from_payload(payload)
            if not isinstance(document, CharacterRelationDocument):
                continue
            if not self._document_matches_runtime_constraints(CollectionName.CHARACTER_RELATIONS, document, context, options):
                continue

            if (document.subject_character_id, document.object_character_id) not in (
                expected_pair,
                (expected_pair[1], expected_pair[0]),
            ):
                continue

            results.append(
                QueryHit(
                    point_id=str(getattr(point, "id")),
                    document=document,
                    score=None,
                    source=RetrievalMode.DIRECT,
                )
            )
            if len(results) >= limit:
                break
        return results

    def _scroll_points(self, collection_name: CollectionName, scroll_filter: Any, limit: int) -> List[Any]:
        """执行 Qdrant scroll 操作并返回 point 列表。"""

        scroll_result = self._client.scroll(
            collection_name=collection_name.value,
            scroll_filter=scroll_filter,
            limit=limit,
            with_payload=True,
        )
        if isinstance(scroll_result, tuple):
            return list(scroll_result[0])
        return list(scroll_result)

    def _build_base_filter(self, collection_name: CollectionName, context: RetrievalContext, options: Any) -> Any:
        """根据 collection 与查询上下文构造基础 Qdrant filter。"""

        must_conditions: List[Any] = []
        if collection_name == CollectionName.STORY_EVENTS:
            must_conditions.extend(self._build_story_event_conditions(context, options))
        elif collection_name == CollectionName.CHARACTER_RELATIONS:
            must_conditions.extend(self._build_relation_conditions(context, options))
        elif collection_name == CollectionName.LORE_ENTRIES:
            must_conditions.extend(self._build_lore_conditions(context, options))
        return qdrant_models.Filter(must=must_conditions)

    def _build_story_event_conditions(self, context: RetrievalContext, options: StoryEventQuery) -> List[Any]:
        """构造 `story_events` 的基础过滤条件。"""

        conditions: List[Any] = [
            qdrant_models.FieldCondition(key="visible_from", range=qdrant_models.Range(lte=context.current_time)),
            qdrant_models.FieldCondition(key="visible_to", range=qdrant_models.Range(gte=context.current_time)),
        ]
        if options.require_character_match and context.current_character_id is not None:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="participants",
                    match=qdrant_models.MatchValue(value=context.current_character_id.value),
                )
            )
        if options.limit_to_series and context.current_series_id is not None:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="series_id",
                    match=qdrant_models.MatchValue(value=context.current_series_id.value),
                )
            )
        if options.limit_to_season and context.current_season_id is not None:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="season_id",
                    match=qdrant_models.MatchValue(value=int(context.current_season_id.value)),
                )
            )
        if options.limit_to_canon_branch and context.current_canon_branch is not None:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="canon_branch",
                    match=qdrant_models.MatchValue(value=context.current_canon_branch.value),
                )
            )
        return conditions

    def _build_relation_conditions(self, context: RetrievalContext, options: CharacterRelationQuery) -> List[Any]:
        """构造 `character_relations` 的基础过滤条件。"""

        conditions: List[Any] = [
            qdrant_models.FieldCondition(key="visible_from", range=qdrant_models.Range(lte=context.current_time)),
            qdrant_models.FieldCondition(key="visible_to", range=qdrant_models.Range(gte=context.current_time)),
        ]
        if options.limit_to_series and context.current_series_id is not None:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="series_id",
                    match=qdrant_models.MatchValue(value=context.current_series_id.value),
                )
            )
        if options.limit_to_season and context.current_season_id is not None:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="season_id",
                    match=qdrant_models.MatchValue(value=int(context.current_season_id.value)),
                )
            )
        if options.limit_to_canon_branch and context.current_canon_branch is not None:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="canon_branch",
                    match=qdrant_models.MatchValue(value=context.current_canon_branch.value),
                )
            )
        return conditions

    def _build_lore_conditions(self, context: RetrievalContext, options: LoreQuery) -> List[Any]:
        """构造 `lore_entries` 的基础过滤条件。"""

        conditions: List[Any] = []
        if options.limit_to_canon_branch and context.current_canon_branch is not None:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="canon_branch",
                    match=qdrant_models.MatchValue(value=context.current_canon_branch.value),
                )
            )
        return conditions

    def _document_matches_runtime_constraints(
        self,
        collection_name: CollectionName,
        document: BaseQdrantDocument,
        context: RetrievalContext,
        options: Any,
    ) -> bool:
        """在 Python 侧对查询结果执行补充约束校验。"""

        if collection_name == CollectionName.STORY_EVENTS and isinstance(document, StoryEventDocument):
            return self._story_event_matches(document, context, options)
        if collection_name == CollectionName.CHARACTER_RELATIONS and isinstance(document, CharacterRelationDocument):
            return self._character_relation_matches(document, context, options)
        if collection_name == CollectionName.LORE_ENTRIES and isinstance(document, LoreEntryDocument):
            return self._lore_entry_matches(document, context, options)
        return False

    def _story_event_matches(
        self,
        document: StoryEventDocument,
        context: RetrievalContext,
        options: StoryEventQuery,
    ) -> bool:
        """判断剧情事件文档是否满足运行时约束。"""
        # 判断当前时间是否在可看到事件的范围内
        if not (document.visible_from <= context.current_time <= document.visible_to):
            return False
        # 判断当前角色是否参与了事件
        if options.require_character_match and context.current_character_id is not None:
            if context.current_character_id not in document.participants:
                return False
        # 判断当前的动画系列是否匹配
        if options.limit_to_series and context.current_series_id is not None:
            if document.series_id != context.current_series_id:
                return False
        # 判断整体时间年份是否匹配（整体时间是指香澄从高一到高三的三年时间线；邦目前所有动画的时间都局限于这三年中）
        if options.limit_to_season and context.current_season_id is not None:
            # 如果年份不满足条件，则丢弃
            if document.season_id != context.current_season_id:
                return False
        # 判断事件所属分支（动画/游戏剧情）是否匹配
        if options.limit_to_canon_branch and context.current_canon_branch is not None:
            if document.canon_branch != context.current_canon_branch:
                return False
        return True

    def _character_relation_matches(
        self,
        document: CharacterRelationDocument,
        context: RetrievalContext,
        options: CharacterRelationQuery,
    ) -> bool:
        """判断角色关系文档是否满足运行时约束。"""

        if not (document.visible_from <= context.current_time <= document.visible_to):
            return False
        if options.limit_to_series and context.current_series_id is not None:
            if document.series_id != context.current_series_id:
                return False
        if options.limit_to_season and context.current_season_id is not None:
            if document.season_id != context.current_season_id:
                return False
        if options.limit_to_canon_branch and context.current_canon_branch is not None:
            if document.canon_branch != context.current_canon_branch:
                return False
        return True

    def _lore_entry_matches(
        self,
        document: LoreEntryDocument,
        context: RetrievalContext,
        options: LoreQuery,
    ) -> bool:
        """判断世界观文档是否满足运行时约束。"""

        if options.limit_to_canon_branch and context.current_canon_branch is not None:
            if document.canon_branch != context.current_canon_branch:
                return False
        if options.require_series_match and context.current_series_id is not None:
            if document.scope_type.value == "series":
                if not document.series_ids or context.current_series_id not in document.series_ids:
                    return False
        if options.require_season_match and context.current_season_id is not None:
            if document.season_ids is not None and context.current_season_id not in document.season_ids:
                return False
        if options.require_time_window:
            if document.visible_from is not None and document.visible_from > context.current_time:
                return False
            if document.visible_to is not None and document.visible_to < context.current_time:
                return False
        return True

    def _document_matches_keywords(self, document: BaseQdrantDocument, normalized_keywords: Sequence[str]) -> bool:
        """判断文档的 tags 是否命中了给定关键词。"""

        tags = getattr(document, "tags", [])
        normalized_tags = {_normalize_keyword(tag) for tag in tags}
        return any(keyword in normalized_tags for keyword in normalized_keywords)


__all__ = [
    "CollectionRegistry",
    "DeleteReport",
    "EmbeddingProvider",
    "PointRecord",
    "QdrantRagService",
    "QueryHit",
    "RagDatasetBundle",
    "RagServiceConfig",
    "RetrievalMode",
    "UpsertReport",
]
