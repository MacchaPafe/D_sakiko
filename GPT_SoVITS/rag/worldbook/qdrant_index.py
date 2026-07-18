"""世界书独立本地 Qdrant 生产 adapter。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

from qdrant_client import QdrantClient, models as qdrant_models

from rag.services import EmbeddingProvider

from .hashing import canonical_json_sha256
from .models import EntryType, IndexPointMetadata, IndexProjection


_COLLECTIONS: dict[EntryType, str] = {
    "story_event": "story_events",
    "character_relation": "character_relations",
    "lore_entry": "lore_entries",
    "character_thought": "character_thoughts",
}


class QdrantWorldbookIndex:
    """只拥有四类世界书 collection 的本地 Qdrant adapter。"""

    def __init__(self, index_path: Path, embedding_model_path: Path, batch_size: int = 32) -> None:
        """保存路径并延迟初始化重型资源。"""

        self._index_path = index_path
        self._embedding_model_path = embedding_model_path
        self._batch_size = batch_size
        self._client: QdrantClient | None = None
        self._embedding = EmbeddingProvider(str(embedding_model_path), default_batch_size=batch_size)

    def index_fingerprint(self) -> str:
        """计算索引 Schema、投影和本地模型文件摘要。"""

        return canonical_json_sha256(
            {
                "index_schema_version": 2,
                "projection_version": 0,
                "embedding_model_id": "multilingual-e5-small",
                "embedding_model_digest": _directory_digest(self._embedding_model_path),
                "distance": "cosine",
            }
        )

    def scan_metadata(self) -> dict[UUID, IndexPointMetadata]:
        """分页扫描四类 collection 中的统一 envelope。"""

        client = self._ensure_client()
        result: dict[UUID, IndexPointMetadata] = {}
        for entry_type, collection_name in _COLLECTIONS.items():
            if not client.collection_exists(collection_name):
                continue
            offset: int | str | UUID | None = None
            while True:
                points, next_offset = client.scroll(collection_name=collection_name, offset=offset, limit=256, with_payload=True, with_vectors=False)
                for point in points:
                    payload = point.payload or {}
                    point_id = UUID(str(point.id))
                    result[point_id] = IndexPointMetadata(
                        point_id=point_id,
                        package_id=str(payload.get("package_id", "")),
                        entry_type=entry_type,
                        entry_revision=str(payload.get("entry_revision", "")),
                        index_fingerprint=str(payload.get("index_fingerprint", "")),
                    )
                if next_offset is None:
                    break
                offset = next_offset
        return result

    def upsert(self, projections: list[IndexProjection], index_fingerprint: str) -> int:
        """按类型分组生成真实向量并 upsert。"""

        if not projections:
            return 0
        client = self._ensure_client()
        self._ensure_collections()
        grouped: dict[EntryType, list[IndexProjection]] = {entry_type: [] for entry_type in _COLLECTIONS}
        for projection in projections:
            grouped[projection.entry_type].append(projection)
        for entry_type, items in grouped.items():
            if not items:
                continue
            vectors = self._embedding.encode_texts([item.embedding_text for item in items], batch_size=self._batch_size)
            points: list[qdrant_models.PointStruct] = []
            for item, vector in zip(items, vectors):
                payload = dict(item.payload)
                payload.update(
                    {
                        "entry_id": str(item.entry_id),
                        "package_id": item.package_id,
                        "entry_type": item.entry_type,
                        "entry_revision": item.entry_revision,
                    }
                )
                payload["index_fingerprint"] = index_fingerprint
                points.append(qdrant_models.PointStruct(id=str(item.entry_id), vector=vector, payload=payload))
            client.upsert(collection_name=_COLLECTIONS[entry_type], points=points, wait=True)
        return len(projections)

    def delete(self, point_ids: list[UUID]) -> int:
        """从四类 collection 删除给定 UUID。"""

        if not point_ids:
            return 0
        client = self._ensure_client()
        ids = [str(point_id) for point_id in point_ids]
        for collection_name in _COLLECTIONS.values():
            if client.collection_exists(collection_name):
                client.delete(collection_name=collection_name, points_selector=qdrant_models.PointIdsList(points=ids), wait=True)
        return len(point_ids)

    def delete_package(self, package_id: str) -> int:
        """使用统一 package_id payload 删除一个包。"""

        client = self._ensure_client()
        deleted = 0
        package_filter = qdrant_models.Filter(must=[qdrant_models.FieldCondition(key="package_id", match=qdrant_models.MatchValue(value=package_id))])
        for collection_name in _COLLECTIONS.values():
            if not client.collection_exists(collection_name):
                continue
            before = client.count(collection_name=collection_name, count_filter=package_filter, exact=True).count
            client.delete(collection_name=collection_name, points_selector=qdrant_models.FilterSelector(filter=package_filter), wait=True)
            deleted += int(before)
        return deleted

    def rebuild(self) -> None:
        """删除且仅删除世界书拥有的四类 collection。"""

        client = self._ensure_client()
        for collection_name in _COLLECTIONS.values():
            if client.collection_exists(collection_name):
                client.delete_collection(collection_name)

    def close(self) -> None:
        """真实关闭 Qdrant client 并释放 embedding 模型。"""

        if self._client is not None:
            self._client.close()
            self._client = None
        self._embedding.close()

    def _ensure_client(self) -> QdrantClient:
        """延迟创建本地 client。"""

        if self._client is None:
            self._index_path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(self._index_path))
        return self._client

    def _ensure_collections(self) -> None:
        """在真正写入前加载模型并建立四类 collection。"""

        client = self._ensure_client()
        vector_size = self._embedding.get_dimension()
        for collection_name in _COLLECTIONS.values():
            if not client.collection_exists(collection_name):
                client.create_collection(collection_name=collection_name, vectors_config=qdrant_models.VectorParams(size=vector_size, distance=qdrant_models.Distance.COSINE))
            try:
                client.create_payload_index(collection_name=collection_name, field_name="package_id", field_schema=qdrant_models.PayloadSchemaType.KEYWORD)
            except Exception:
                pass


def _directory_digest(path: Path) -> str:
    """计算模型目录中相对路径与文件内容的稳定摘要。"""

    if not path.is_dir():
        raise FileNotFoundError(f"embedding 模型目录不存在: {path}")
    digest = hashlib.sha256()
    for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(file_path.relative_to(path).as_posix().encode("utf-8"))
        with file_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()
