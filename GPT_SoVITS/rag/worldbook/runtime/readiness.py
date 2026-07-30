"""保存聊天查询与同步控制器共享的世界书索引就绪态。"""

from __future__ import annotations

from threading import Lock

from rag.worldbook.models import WorldbookReadiness


class WorldbookIndexReadinessState:
    """以线程安全方式共享进程内的世界书索引就绪态。"""

    def __init__(
        self,
        initial: WorldbookReadiness = WorldbookReadiness.UNAVAILABLE,
    ) -> None:
        """使用保守的初始状态创建共享状态。"""

        self._lock = Lock()
        self._value = initial

    def get(self) -> WorldbookReadiness:
        """返回当前世界书索引就绪态快照。"""

        with self._lock:
            return self._value

    def update(
        self,
        readiness: WorldbookReadiness | str,
    ) -> WorldbookReadiness:
        """校验、保存并返回新的世界书索引就绪态。"""

        normalized = WorldbookReadiness(readiness)
        with self._lock:
            self._value = normalized
        return normalized
