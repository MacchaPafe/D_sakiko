"""定义世界书派生索引的深层接口与内存测试实现。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from .models import IndexPointMetadata, IndexProjection


class WorldbookIndex(Protocol):
    """隐藏具体向量数据库实现的世界书索引接口。"""

    def scan_metadata(self) -> dict[UUID, IndexPointMetadata]:
        """读取实际索引中的统一 point 元数据。"""

    def upsert(self, projections: list[IndexProjection], index_fingerprint: str) -> int:
        """批量生成向量并写入投影。"""

    def delete(self, point_ids: list[UUID]) -> int:
        """按稳定 entry UUID 删除 point。"""

    def delete_package(self, package_id: str) -> int:
        """删除指定包的全部 point。"""

    def rebuild(self) -> None:
        """只重建世界书拥有的 collection。"""

    def close(self) -> None:
        """释放 client 与 embedding 资源。"""


class InMemoryWorldbookIndex:
    """用于同步纯逻辑测试的无向量内存 adapter。"""

    def __init__(self) -> None:
        """创建空 point 元数据集合。"""

        self._points: dict[UUID, IndexPointMetadata] = {}

    def scan_metadata(self) -> dict[UUID, IndexPointMetadata]:
        """返回当前元数据副本。"""

        return dict(self._points)

    def upsert(self, projections: list[IndexProjection], index_fingerprint: str) -> int:
        """记录投影的统一元数据。"""

        for projection in projections:
            self._points[projection.entry_id] = IndexPointMetadata(
                point_id=projection.entry_id,
                package_id=projection.package_id,
                entry_type=projection.entry_type,
                entry_revision=projection.entry_revision,
                index_fingerprint=index_fingerprint,
            )
        return len(projections)

    def delete(self, point_ids: list[UUID]) -> int:
        """删除存在的 point 并返回数量。"""

        count = 0
        for point_id in point_ids:
            if self._points.pop(point_id, None) is not None:
                count += 1
        return count

    def delete_package(self, package_id: str) -> int:
        """删除一个包的全部 point。"""

        point_ids = [point_id for point_id, metadata in self._points.items() if metadata.package_id == package_id]
        return self.delete(point_ids)

    def rebuild(self) -> None:
        """清空内存索引。"""

        self._points.clear()

    def close(self) -> None:
        """内存 adapter 没有外部资源。"""
