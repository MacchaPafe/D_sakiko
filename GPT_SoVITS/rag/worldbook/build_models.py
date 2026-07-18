"""世界书单包／批量构建配置、身份和报告模型。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rag.models import CanonBranch, SeriesId

from .models import PackageDependency, ValidationIssue


IdentityStatus = Literal["active", "inactive", "retired"]


class EpisodeBuildInput(BaseModel):
    """绑定一集的全部直接构建输入。"""

    model_config = ConfigDict(extra="forbid")

    episode: int = Field(ge=0)
    stage2_input: str
    stage2a_annotation: str
    stage2b_annotation: str
    rag_artifact: str


class WorldbookBuildSpec(BaseModel):
    """定义一个世界书包的可重复构建参数。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = 0
    package_id: str = Field(min_length=1)
    package_version: str
    display_name: str = Field(min_length=1)
    package_type: Literal["season", "common"]
    series_id: SeriesId
    timeline_id: str = Field(min_length=1)
    canon_branch: CanonBranch
    story_year: int | None = Field(default=None, ge=1)
    dependencies: list[PackageDependency] = Field(default_factory=list)
    episodes: list[EpisodeBuildInput] = Field(default_factory=list)
    relation_review: str
    thought_review: str
    lore_decisions: str
    id_map: str
    official_root: str
    build_root: str = ".build"
    build_report: str = ".build/build-report.json"

    @model_validator(mode="after")
    def validate_episode_range(self) -> "WorldbookBuildSpec":
        """要求季度包显式提供不重复的连续集数。"""

        episode_numbers = [item.episode for item in self.episodes]
        if len(episode_numbers) != len(set(episode_numbers)):
            raise ValueError("episodes 不得重复")
        if self.package_type == "season":
            if not episode_numbers:
                raise ValueError("season 包必须提供 episodes")
            ordered = sorted(episode_numbers)
            if ordered != list(range(ordered[0], ordered[-1] + 1)):
                raise ValueError("season 包的 episodes 必须连续")
        return self


class WorldbookBatchBuildSpec(BaseModel):
    """定义一次共同审计和发布的多包配置引用。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = 0
    build_specs: list[str] = Field(min_length=1)
    build_report: str = ".build/batch-build-report.json"

    @field_validator("build_specs")
    @classmethod
    def validate_unique_specs(cls, value: list[str]) -> list[str]:
        """拒绝重复构建同一个配置路径。"""

        if len(value) != len(set(value)):
            raise ValueError("build_specs 不得重复")
        return value


class IdentityMapRecord(BaseModel):
    """保存一个开发侧身份对应的正式 UUID 和生命周期状态。"""

    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    status: IdentityStatus = "active"


class WorldbookIdentityMap(BaseModel):
    """保存单个包内全部正式身份映射。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = 0
    package_id: str
    identities: dict[str, IdentityMapRecord] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_uuids(self) -> "WorldbookIdentityMap":
        """禁止两个开发身份共享同一个正式 UUID。"""

        values = [item.entry_id for item in self.identities.values()]
        if len(values) != len(set(values)):
            raise ValueError("ID map 中正式 UUID 不得重复")
        return self


class BuildIdentityChange(BaseModel):
    """描述本次构建预计或实际发生的身份状态变化。"""

    model_config = ConfigDict(extra="forbid")

    identity_key: str
    change: Literal["allocate", "deactivate", "reactivate", "retire"]
    entry_id: UUID | None = None
    provisional: bool = False


class WorldbookBuildReport(BaseModel):
    """保存一次可覆盖的世界书构建与发布结果。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = 0
    package_id: str
    command: Literal["validate", "publish"]
    succeeded: bool = False
    package_published: bool = False
    index_rebuilt: bool = False
    index_readiness: str | None = None
    git_commit: str | None = None
    input_sha256: dict[str, str] = Field(default_factory=dict)
    identity_changes: list[BuildIdentityChange] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    staging_path: str | None = None
    report_path: str | None = None


class WorldbookBatchBuildReport(BaseModel):
    """保存一次多包共同发布的单次报告。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = 0
    succeeded: bool = False
    package_reports: list[WorldbookBuildReport] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    index_rebuilt: bool = False
    index_readiness: str | None = None
    report_path: str | None = None
