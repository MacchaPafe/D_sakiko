"""实现世界书非阻塞、短连接的本地 Qdrant 只读仓库。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from filelock import FileLock, Timeout
from qdrant_client import QdrantClient, models as qdrant_models
from qdrant_client.models import Record, ScoredPoint

from rag.models import CharacterId
from rag.services import EmbeddingProvider
from rag.worldbook.index_schema import COLLECTION_NAMES, e5_query_text
from rag.worldbook.models import EntryType, WorldbookReadiness

from .models import (
    PayloadBatch,
    PayloadRecord,
    RetrievalBatch,
    RetrievalCandidate,
    RetrievalFailure,
    WorldbookResolvedContext,
)


class QueryEmbedding(Protocol):
    """约束运行时查询所需的最小 embedding 能力。"""

    def encode_text(self, text: str) -> list[float]:
        """编码一条查询文本。"""

    def close(self) -> None:
        """释放模型资源。"""


class IndexReadinessProvider(Protocol):
    """约束只读仓库判断派生索引是否允许查询的最小接口。"""

    def get(self) -> WorldbookReadiness:
        """返回当前索引就绪态。"""


@dataclass(frozen=True, slots=True)
class RetrievalConstraints:
    """描述一次查询共享的世界书硬过滤条件。"""

    context: WorldbookResolvedContext
    time_mode: Literal["active", "started", "none"] = "active"
    query_time: int | None = None
    subject_character_id: CharacterId | None = None
    object_character_id: CharacterId | None = None


@dataclass(frozen=True, slots=True)
class SemanticSearchRequest:
    """描述一次带最低阈值和轻量精确词加分的向量查询。"""

    entry_type: EntryType
    query: str
    constraints: RetrievalConstraints
    limit: int
    score_threshold: float
    boost_source: str = ""
    candidate_limit: int = 24


@dataclass(frozen=True, slots=True)
class PayloadScanRequest:
    """描述一次不进行语义排序的 payload 扫描。"""

    entry_type: EntryType
    constraints: RetrievalConstraints
    limit: int = 2048


ClientFactory = Callable[[Path], QdrantClient]


class WorldbookRetrievalRepository:
    """常驻查询 embedding，并为每次查询短暂打开本地 Qdrant。"""

    def __init__(
        self,
        index_path: Path,
        lock_path: Path,
        embedding_model_path: Path,
        readiness: IndexReadinessProvider,
        embedding: QueryEmbedding | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        """保存路径和可替换测试边界，不持有长期 Qdrant client。"""

        self._index_path = index_path
        self._lock_path = lock_path
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._readiness = readiness
        self._embedding = embedding or EmbeddingProvider(str(embedding_model_path))
        self._client_factory = client_factory or self._default_client_factory

    def semantic_search(self, request: SemanticSearchRequest) -> RetrievalBatch:
        """执行带硬过滤的向量候选查询，并在阈值后应用轻量 boost。"""

        if request.limit < 1 or request.candidate_limit < request.limit:
            return RetrievalBatch(
                failure=RetrievalFailure(code="invalid_request", message="检索数量参数无效")
            )
        lock = FileLock(str(self._lock_path))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            return RetrievalBatch(
                failure=RetrievalFailure(
                    code="temporarily_unavailable",
                    message="世界书索引正在同步",
                )
            )
        client: QdrantClient | None = None
        try:
            readiness_failure = self._readiness_failure()
            if readiness_failure is not None:
                return RetrievalBatch(failure=readiness_failure)
            vector = self._embedding.encode_text(e5_query_text(request.query))
            client = self._client_factory(self._index_path)
            collection_name = COLLECTION_NAMES[request.entry_type]
            if not client.collection_exists(collection_name):
                return RetrievalBatch(
                    failure=RetrievalFailure(
                        code="index_unavailable",
                        message=f"世界书索引缺少 {collection_name}",
                    )
                )
            response = client.query_points(
                collection_name=collection_name,
                query=vector,
                query_filter=self._qdrant_filter(request.entry_type, request.constraints),
                limit=request.candidate_limit,
                with_payload=True,
                with_vectors=False,
                score_threshold=request.score_threshold,
            )
            candidates = [
                self._candidate_from_point(request.entry_type, point, request.boost_source)
                for point in response.points
                if point.score >= request.score_threshold
            ]
            candidates.sort(key=lambda item: (-item.final_score, str(item.entry_id)))
            return RetrievalBatch(candidates=candidates[: request.limit])
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return RetrievalBatch(
                failure=RetrievalFailure(code="retrieval_failed", message=str(exc))
            )
        finally:
            if client is not None:
                client.close()
            lock.release()

    def scan_payloads(self, request: PayloadScanRequest) -> PayloadBatch:
        """读取符合硬过滤的 payload，用于关系历史等确定性查询。"""

        lock = FileLock(str(self._lock_path))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            return PayloadBatch(
                failure=RetrievalFailure(
                    code="temporarily_unavailable",
                    message="世界书索引正在同步",
                )
            )
        client: QdrantClient | None = None
        try:
            readiness_failure = self._readiness_failure()
            if readiness_failure is not None:
                return PayloadBatch(failure=readiness_failure)
            client = self._client_factory(self._index_path)
            collection_name = COLLECTION_NAMES[request.entry_type]
            if not client.collection_exists(collection_name):
                return PayloadBatch(
                    failure=RetrievalFailure(
                        code="index_unavailable",
                        message=f"世界书索引缺少 {collection_name}",
                    )
                )
            records: list[PayloadRecord] = []
            offset: int | str | UUID | None = None
            while len(records) < request.limit:
                points, next_offset = client.scroll(
                    collection_name=collection_name,
                    scroll_filter=self._qdrant_filter(request.entry_type, request.constraints),
                    offset=offset,
                    limit=min(256, request.limit - len(records)),
                    with_payload=True,
                    with_vectors=False,
                )
                records.extend(
                    self._record_from_qdrant(request.entry_type, point)
                    for point in points
                )
                if next_offset is None:
                    break
                offset = next_offset
            return PayloadBatch(records=records)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return PayloadBatch(
                failure=RetrievalFailure(code="retrieval_failed", message=str(exc))
            )
        finally:
            if client is not None:
                client.close()
            lock.release()

    def retrieve_payloads(
        self,
        entry_type: EntryType,
        entry_ids: list[UUID],
    ) -> PayloadBatch:
        """按显式 UUID 读取 payload，不执行第二次语义检索。"""

        if not entry_ids:
            return PayloadBatch()
        lock = FileLock(str(self._lock_path))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            return PayloadBatch(
                failure=RetrievalFailure(
                    code="temporarily_unavailable",
                    message="世界书索引正在同步",
                )
            )
        client: QdrantClient | None = None
        try:
            readiness_failure = self._readiness_failure()
            if readiness_failure is not None:
                return PayloadBatch(failure=readiness_failure)
            client = self._client_factory(self._index_path)
            collection_name = COLLECTION_NAMES[entry_type]
            if not client.collection_exists(collection_name):
                return PayloadBatch(
                    failure=RetrievalFailure(
                        code="index_unavailable",
                        message=f"世界书索引缺少 {collection_name}",
                    )
                )
            records = client.retrieve(
                collection_name=collection_name,
                ids=entry_ids,
                with_payload=True,
                with_vectors=False,
            )
            return PayloadBatch(
                records=[self._record_from_qdrant(entry_type, item) for item in records]
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return PayloadBatch(
                failure=RetrievalFailure(code="retrieval_failed", message=str(exc))
            )
        finally:
            if client is not None:
                client.close()
            lock.release()

    def close(self) -> None:
        """释放常驻的查询 embedding 模型。"""

        self._embedding.close()

    def _readiness_failure(self) -> RetrievalFailure | None:
        """把非查询态转换成稳定的可降级失败。"""

        readiness = self._readiness.get()
        if readiness in {WorldbookReadiness.READY, WorldbookReadiness.DEGRADED}:
            return None
        if readiness == WorldbookReadiness.SYNCING:
            return RetrievalFailure(
                code="temporarily_unavailable",
                message="世界书索引正在同步",
            )
        return RetrievalFailure(
            code="index_unavailable",
            message="世界书索引尚未完成可用性对账",
        )

    def _qdrant_filter(
        self,
        entry_type: EntryType,
        constraints: RetrievalConstraints,
    ) -> qdrant_models.Filter:
        """把统一可见性上下文转换成 Qdrant 硬过滤。"""

        context = constraints.context
        must: list[qdrant_models.Condition] = [
            qdrant_models.FieldCondition(
                key="package_id",
                match=qdrant_models.MatchAny(any=context.package_ids),
            ),
            qdrant_models.FieldCondition(
                key="timeline_id",
                match=qdrant_models.MatchValue(value=context.timeline_id),
            ),
            qdrant_models.FieldCondition(
                key="canon_branch",
                match=qdrant_models.MatchValue(value=context.canon_branch.value),
            ),
        ]
        query_time = constraints.query_time or context.current_time
        if constraints.time_mode in {"active", "started"}:
            must.append(self._optional_range_condition("visible_from", lte=query_time))
        if constraints.time_mode == "active":
            must.append(self._optional_range_condition("visible_to", gte=query_time))
        if constraints.subject_character_id is not None:
            key = "character_id" if entry_type == "character_thought" else "subject_character_id"
            must.append(
                qdrant_models.FieldCondition(
                    key=key,
                    match=qdrant_models.MatchValue(
                        value=constraints.subject_character_id.value
                    ),
                )
            )
        if constraints.object_character_id is not None:
            must.append(
                qdrant_models.FieldCondition(
                    key="object_character_id",
                    match=qdrant_models.MatchValue(
                        value=constraints.object_character_id.value
                    ),
                )
            )
        return qdrant_models.Filter(must=must)

    def _optional_range_condition(
        self,
        field_name: str,
        *,
        lte: int | None = None,
        gte: int | None = None,
    ) -> qdrant_models.Filter:
        """允许缺失边界字段，同时约束实际存在的整数值。"""

        return qdrant_models.Filter(
            should=[
                qdrant_models.IsNullCondition(
                    is_null=qdrant_models.PayloadField(key=field_name)
                ),
                qdrant_models.IsEmptyCondition(
                    is_empty=qdrant_models.PayloadField(key=field_name)
                ),
                qdrant_models.FieldCondition(
                    key=field_name,
                    range=qdrant_models.Range(lte=lte, gte=gte),
                ),
            ]
        )

    def _candidate_from_point(
        self,
        entry_type: EntryType,
        point: ScoredPoint,
        boost_source: str,
    ) -> RetrievalCandidate:
        """把 Qdrant point 转换成内部候选并计算轻量精确词加分。"""

        payload = dict(point.payload or {})
        boost = self._exact_match_boost(payload, boost_source)
        return RetrievalCandidate(
            entry_id=UUID(str(point.id)),
            package_id=str(payload.get("package_id", "")),
            entry_type=entry_type,
            payload=payload,
            score=float(point.score),
            boost=boost,
            final_score=float(point.score) + boost,
        )

    def _record_from_qdrant(
        self,
        entry_type: EntryType,
        point: Record,
    ) -> PayloadRecord:
        """把无向量 Qdrant 记录转换成内部 payload 记录。"""

        payload = dict(point.payload or {})
        return PayloadRecord(
            entry_id=UUID(str(point.id)),
            package_id=str(payload.get("package_id", "")),
            entry_type=entry_type,
            payload=payload,
        )

    def _exact_match_boost(
        self,
        payload: dict[str, object],
        source: str,
    ) -> float:
        """对正文明确包含的规范 title 或 tag 施加有限加分。"""

        normalized = source.casefold()
        if not normalized:
            return 0.0
        boost = 0.0
        title = payload.get("title")
        if isinstance(title, str) and title.casefold() in normalized:
            boost += 0.04
        tags = payload.get("tags")
        if isinstance(tags, list) and any(
            isinstance(tag, str) and tag.casefold() in normalized for tag in tags
        ):
            boost += 0.02
        return min(boost, 0.05)

    def _default_client_factory(self, index_path: Path) -> QdrantClient:
        """创建一次调用范围内的本地 Qdrant client。"""

        return QdrantClient(path=str(index_path))
