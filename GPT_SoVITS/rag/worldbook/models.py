"""世界书管理 envelope 与同步报告模型。"""

from __future__ import annotations

from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .versioning import parse_semver, validate_version_spec


EntryType = Literal["story_event", "character_relation", "lore_entry", "character_thought"]


class PackageReadiness(str, Enum):
    """描述单个包能否参与索引。"""

    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class WorldbookReadiness(str, Enum):
    """描述世界书派生索引的整体可用性。"""

    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    SYNCING = "syncing"


class PackageDependency(BaseModel):
    """表示官方包对另一个包的显式依赖。"""

    package_id: str
    version_spec: str = ">=0.0.0"

    @field_validator("package_id")
    @classmethod
    def validate_package_id(cls, value: str) -> str:
        """拒绝空依赖包 ID。"""

        normalized = value.strip()
        if not normalized:
            raise ValueError("依赖 package_id 不能为空")
        return normalized

    @field_validator("version_spec")
    @classmethod
    def validate_spec(cls, value: str) -> str:
        """校验第一版支持的显式 SemVer 比较器。"""

        return validate_version_spec(value)


class ContentFileRecord(BaseModel):
    """描述 manifest 声明的一份内容文件。"""

    path: str
    sha256: str
    entry_type: EntryType


class WorldbookManifest(BaseModel):
    """描述可独立发布的官方世界书包。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = 1
    package_id: str
    package_version: str
    display_name: str
    package_type: Literal["season", "common"]
    timeline_id: str
    dependencies: list[PackageDependency] = Field(default_factory=list)
    content_files: list[ContentFileRecord] = Field(default_factory=list)

    @field_validator("package_version")
    @classmethod
    def validate_package_version(cls, value: str) -> str:
        """要求包版本为完整 SemVer。"""

        normalized = value.strip()
        parse_semver(normalized)
        return normalized

    @model_validator(mode="after")
    def validate_unique_dependencies(self) -> "WorldbookManifest":
        """拒绝重复依赖和包对自身的直接依赖。"""

        dependency_ids = [item.package_id for item in self.dependencies]
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError("dependencies 不得重复声明同一 package_id")
        if self.package_id in dependency_ids:
            raise ValueError("世界书包不得直接依赖自身")
        return self


class WorldbookEntry(BaseModel):
    """表示内容文件中的自描述世界书条目。"""

    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    entry_type: EntryType
    schema_version: int = 0
    content: dict[str, object]


class WorldbookOverride(BaseModel):
    """表示用户对一条官方条目的完整替换。"""

    entry_id: UUID
    entry_type: EntryType
    schema_version: int
    base_revision: str
    content: dict[str, object]


class WorldbookTombstone(BaseModel):
    """表示用户隐藏的一条官方条目。"""

    entry_id: UUID
    base_revision: str


class WorldbookUserState(BaseModel):
    """表示单个官方包对应的用户可写状态。"""

    format_version: int = 1
    package_id: str
    overrides: list[WorldbookOverride] = Field(default_factory=list)
    extensions: list[WorldbookEntry] = Field(default_factory=list)
    tombstones: list[WorldbookTombstone] = Field(default_factory=list)


class EffectiveWorldbookEntry(BaseModel):
    """表示合并后用于查看和索引的世界书条目。"""

    package_id: str
    entry: WorldbookEntry
    revision: str
    source: Literal["official", "override", "extension"]
    base_conflict: bool = False


class ValidationIssue(BaseModel):
    """表示包、条目或同步阶段发现的一项结构化问题。"""

    code: str
    message: str
    package_id: str | None = None
    entry_id: UUID | None = None
    path: str | None = None


class PackageLoadResult(BaseModel):
    """表示包加载器对单个 manifest 的完整结果。"""

    manifest: WorldbookManifest | None = None
    entries: list[WorldbookEntry] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    readiness: PackageReadiness = PackageReadiness.UNAVAILABLE


class IndexPointMetadata(BaseModel):
    """表示从派生索引读取的最小 point 元数据。"""

    point_id: UUID
    package_id: str
    entry_type: EntryType
    entry_revision: str
    index_fingerprint: str


class SyncPlan(BaseModel):
    """表示一次对账需要执行的索引操作。"""

    rebuild: bool = False
    upsert_entry_ids: list[UUID] = Field(default_factory=list)
    delete_entry_ids: list[UUID] = Field(default_factory=list)
    reason: str = ""


class SyncReport(BaseModel):
    """表示一次同步任务的内存结果。"""

    success: bool
    readiness: WorldbookReadiness
    indexed_count: int = 0
    deleted_count: int = 0
    skipped_count: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)


class IndexProjection(BaseModel):
    """表示 adapter 交给索引层的稳定投影。"""

    entry_id: UUID
    package_id: str
    entry_type: EntryType
    entry_revision: str
    embedding_text: str
    payload: dict[str, object]

    @field_validator("embedding_text")
    @classmethod
    def validate_embedding_text(cls, value: str) -> str:
        """拒绝无法生成有效向量的空文本。"""

        normalized = value.strip()
        if not normalized:
            raise ValueError("embedding_text 不能为空")
        return normalized
