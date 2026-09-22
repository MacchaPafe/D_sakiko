"""独立 Stage2 数据集编辑器使用的轻量数据结构。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Stage2ScreenText(BaseModel):
    """表示编辑器中只读展示的屏幕文字。"""

    s_id: str
    start_ms: int
    end_ms: int
    start_text: str
    end_text: str
    kind: str
    text: str


class Stage2Utterance(BaseModel):
    """表示编辑器中可复核的一条台词。"""

    u_id: str
    start_ms: int
    end_ms: int
    start_text: str
    end_text: str
    speaker_name: str | None = None
    speaker_confidence: float = 0.0
    is_inner_monologue: bool = False
    addressee_candidates: list[str] = Field(default_factory=list)
    mentioned_characters: list[str] = Field(default_factory=list)
    emotion_hint: str | None = None
    zh_text: str = ""
    jp_text: str = ""


class Stage2SceneInput(BaseModel):
    """表示编辑器中的一个场景。"""

    anime_title: str
    series_id: str
    timeline_id: str
    story_year: int | None = None
    episode: int
    scene_id: str
    start_ms: int
    end_ms: int
    scene_start_text: str
    scene_end_text: str
    scene_summary_hint: str | None = None
    present_characters: list[str] = Field(default_factory=list)
    screen_texts: list[Stage2ScreenText] = Field(default_factory=list)
    utterances: list[Stage2Utterance] = Field(default_factory=list)
    global_notes: list[str] = Field(default_factory=list)


class Stage2SkippedScene(BaseModel):
    """表示无法转换为 Stage2 输入的场景记录。"""

    scene_id: str
    error: str


class Stage2InputMetadata(BaseModel):
    """表示 Stage2 输入文件的来源元数据。"""

    subtitle_path: str
    anime_title: str
    series_id: str
    timeline_id: str
    story_year: int | None = None
    canon_branch: str
    episode: int
    scene_gap_ms: int
    source_stage1_model: str
    source_stage1_template_path: str
    source_stage1_output_path: str | None = None


class Stage2InputArtifact(BaseModel):
    """表示可由独立编辑器加载和保存的完整数据集。"""

    metadata: Stage2InputMetadata
    scenes: list[Stage2SceneInput] = Field(default_factory=list)
    skipped_scenes: list[Stage2SkippedScene] = Field(default_factory=list)
