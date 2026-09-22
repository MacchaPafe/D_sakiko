"""集中定义世界书运行时路径。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorldbookPaths:
    """保存可从应用根目录推导的世界书路径。"""

    app_root: Path

    @property
    def official_packages(self) -> Path:
        """返回只读官方世界书包目录。"""

        return self.app_root / "GPT_SoVITS" / "rag" / "worldbooks" / "official"

    @property
    def user_state(self) -> Path:
        """返回包级用户状态目录。"""

        return self.app_root / "knowledge_base" / "worldbooks" / "package-state"

    @property
    def index(self) -> Path:
        """返回世界书独立 Qdrant 目录。"""

        return self.app_root / "knowledge_base" / "worldbook_index"

    @property
    def lock(self) -> Path:
        """返回跨进程同步锁路径。"""

        return self.app_root / "knowledge_base" / "worldbook_index.lock"

    @property
    def embedding_model(self) -> Path:
        """返回随程序交付的 embedding 模型目录。"""

        return self.app_root / "GPT_SoVITS" / "pretrained_models" / "multilingual-e5-small"
