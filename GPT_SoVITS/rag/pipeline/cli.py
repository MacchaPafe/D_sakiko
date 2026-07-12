"""RAG 标注流水线 CLI。"""

from __future__ import annotations

import argparse
from pathlib import Path

from rag.models import CanonBranch, SeriesId

from .llm_client import LiteLLMConfig
from .prompt_package import load_prompt_package_manifest
from .prompt_package_litellm import complete_prompt_package_with_litellm
from .stage1_speaker_annotation import (
    DEFAULT_TEMPLATE_PATH,
    annotate_prepared_stage1_artifact,
    default_status_printer,
    default_stream_printer,
    load_stage1_annotation_artifact,
    load_stage1_prepared_artifact,
    prepare_stage1_artifact,
    prepare_stage1_prompt_package,
    assemble_stage1_prompt_package,
    save_stage1_annotation_artifact,
    save_stage1_prepared_artifact,
)
from .stage2_input_builder import (
    build_stage2_input_artifact,
    derive_stage2_input_output_path,
    load_stage2_input_artifact,
    save_stage2_input_artifact,
)
from .stage2_document_extraction import (
    DEFAULT_TEMPLATE_PATH as DEFAULT_STAGE2_TEMPLATE_PATH,
    annotate_stage2_input_artifact,
    assemble_stage2_prompt_package,
    load_stage2_annotation_artifact,
    save_stage2_annotation_artifact,
    prepare_stage2_prompt_package,
)
from .stage2b_thought_extraction import (
    DEFAULT_TEMPLATE_PATH as DEFAULT_STAGE2B_TEMPLATE_PATH,
    annotate_stage2b_artifact,
    assemble_stage2b_prompt_package,
    load_stage2b_annotation_artifact,
    save_stage2b_annotation_artifact,
    prepare_stage2b_prompt_package,
)
from .stage3_rag_import import (
    backfill_existing_stage3_character_relations,
    backfill_stage3_character_relations_across_artifacts,
    build_stage3_normalized_import_artifact,
    create_rag_service,
    load_stage3_artifacts_from_directory,
    load_stage3_normalized_import_artifact,
    save_stage3_artifacts_to_directory,
    save_stage3_normalized_import_artifact,
    upsert_stage3_normalized_import_artifact,
)
from .stage3_relation_aggregation import (
    DEFAULT_TEMPLATE_PATH as DEFAULT_RELATION_AGGREGATION_TEMPLATE_PATH,
    build_stage3_relation_aggregation_artifact_from_many,
    assemble_stage3_relation_prompt_package,
    load_stage3_relation_aggregation_artifact,
    save_stage3_relation_aggregation_artifact,
    prepare_stage3_relation_prompt_package,
)
from .stage3_lore_deduplicate import (
    apply_lore_dedup_decisions_from_files,
    build_lore_dedup_review_from_directory,
    save_deduped_stage3_artifacts,
    save_lore_dedup_decisions,
    save_lore_dedup_review_artifact,
)
from .stage3_thought_pipeline import (
    DEFAULT_LINK_TEMPLATE_PATH,
    build_stage3_thought_import_artifact,
    assemble_stage3_thought_link_prompt_package,
    load_stage3_thought_import_artifact,
    save_stage3_thought_import_artifact,
    upsert_stage3_thought_import_artifact,
    prepare_stage3_thought_link_prompt_package,
)
from rag.worldbook.publisher import publish_worldbook_package


def _add_offline_assemble_flags(parser: argparse.ArgumentParser) -> None:
    """为离线 response 组装命令添加一致的公共参数。"""

    parser.add_argument("--manifest", required=True, help="Prompt Package manifest.json 路径")
    parser.add_argument("--output", required=True, help="输出正式 artifact JSON 路径")
    parser.add_argument("--model-label", default="codex-workspace", help="写入 artifact 的模型来源标签")
    parser.add_argument("--allow-partial", action="store_true", help="允许缺少部分 response 并生成部分产物")
    parser.add_argument("--allow-stale", action="store_true", help="允许源文件或 Prompt 哈希已经变化")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG 第一阶段字幕标注流水线")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare-stage1", help="解析字幕并导出第一阶段预处理结果")
    prepare_parser.add_argument("--subtitle", required=True, help="输入字幕文件路径")
    prepare_parser.add_argument("--output", required=True, help="输出 prepared JSON 路径")
    prepare_parser.add_argument("--anime-title", default="It's MyGO!!!!!", help="动画标题")
    prepare_parser.add_argument("--series-id", default=SeriesId.ITS_MYGO.value, help="系列 id")
    prepare_parser.add_argument("--timeline-id", default="bang_dream_original", help="剧情时间线 id")
    prepare_parser.add_argument("--story-year", type=int, default=3, help="可空剧情学年")
    prepare_parser.add_argument("--canon-branch", default=CanonBranch.MAIN.value, help="剧情分支")
    prepare_parser.add_argument("--scene-gap-ms", type=int, default=12000, help="场景切分时间阈值")

    publish_parser = subparsers.add_parser("publish-worldbook", help="把审核后的 Stage 3 artifact 发布为世界书包")
    publish_parser.add_argument("--artifact", type=Path, required=True, help="审核后的 Stage 3 JSON")
    publish_parser.add_argument("--package-dir", type=Path, required=True, help="世界书包输出目录")
    publish_parser.add_argument("--package-id", required=True, help="稳定包 ID")
    publish_parser.add_argument("--package-version", required=True, help="SemVer 包版本")
    publish_parser.add_argument("--display-name", required=True, help="显示名称")
    publish_parser.add_argument("--timeline-id", required=True, help="剧情时间线 ID")
    publish_parser.add_argument("--id-map", type=Path, required=True, help="开发侧稳定 entry ID map")
    publish_parser.add_argument("--allocate-new-ids", action="store_true", help="允许为新来源分配 UUID")

    complete_package_parser = subparsers.add_parser(
        "complete-prompt-package",
        help="使用 LiteLLM 请求 Prompt Package，并把原始回复写入 responses 目录",
    )
    complete_package_parser.add_argument("--manifest", required=True, help="Prompt Package manifest.json 路径")
    complete_package_parser.add_argument("--model", required=True, help="litellm 使用的模型名")
    complete_package_parser.add_argument("--temperature", type=float, default=0.1, help="LLM temperature")
    complete_package_parser.add_argument("--max-tokens", type=int, default=None, help="LLM max_tokens")
    complete_package_parser.add_argument("--retries", type=int, default=2, help="LLM 调用失败时的重试次数")
    complete_package_parser.add_argument("--overwrite", action="store_true", help="覆盖已有的非空 response")
    complete_package_parser.add_argument("--allow-stale", action="store_true", help="允许源文件或 Prompt 哈希已经变化")
    complete_package_parser.add_argument("--stream", action="store_true", help="开启 LLM 流式输出")
    complete_package_parser.add_argument("--quiet-status", action="store_true", help="关闭任务级状态日志")

    annotate_parser = subparsers.add_parser("annotate-stage1", help="批量调用 LLM 进行第一阶段 speaker 标注")
    annotate_parser.add_argument("--prepared", required=True, help="prepare-stage1 输出的 JSON 文件路径")
    annotate_parser.add_argument("--output", required=True, help="输出 pass1_raw JSON 路径")
    annotate_parser.add_argument("--model", required=True, help="litellm 使用的模型名")
    annotate_parser.add_argument("--template", default=str(DEFAULT_TEMPLATE_PATH), help="prompt 模板路径")
    annotate_parser.add_argument("--temperature", type=float, default=0.1, help="LLM temperature")
    annotate_parser.add_argument("--max-tokens", type=int, default=None, help="LLM max_tokens")
    annotate_parser.add_argument("--retries", type=int, default=2, help="LLM 调用失败时的重试次数")
    annotate_parser.add_argument("--scene-id", action="append", default=None, help="只跑指定 scene_id，可重复传入")
    annotate_parser.add_argument("--max-scenes", type=int, default=None, help="最多处理多少个 scene")
    annotate_parser.add_argument("--prompts-dir", default=None, help="若提供，则把每个场景的渲染 prompt 单独落盘")
    annotate_parser.add_argument("--stream", action="store_true", help="开启 LLM 流式输出")
    annotate_parser.add_argument("--quiet-status", action="store_true", help="关闭场景级状态日志")
    annotate_parser.add_argument(
        "--stage2-output",
        default=None,
        help="若提供，则把第一阶段结果自动转换后的第二阶段输入保存到该路径",
    )

    render_stage1_parser = subparsers.add_parser(
        "render-stage1-prompts",
        help="只渲染 Stage 1 Speaker Prompt Package，不请求模型",
    )
    render_stage1_parser.add_argument("--prepared", required=True, help="prepare-stage1 输出路径")
    render_stage1_parser.add_argument("--output-dir", required=True, help="Prompt Package 输出目录")
    render_stage1_parser.add_argument("--template", default=str(DEFAULT_TEMPLATE_PATH), help="Prompt 模板路径")
    render_stage1_parser.add_argument("--scene-id", action="append", default=None, help="只渲染指定 scene_id")
    render_stage1_parser.add_argument("--max-scenes", type=int, default=None, help="最多渲染多少个场景")

    assemble_stage1_parser = subparsers.add_parser(
        "assemble-stage1-responses",
        help="校验离线 response 并组装 Stage 1 Speaker artifact",
    )
    _add_offline_assemble_flags(assemble_stage1_parser)
    assemble_stage1_parser.add_argument(
        "--stage2-output",
        default=None,
        help="可选：同时生成 Stage 2 Input JSON",
    )

    convert_parser = subparsers.add_parser(
        "build-stage2-input",
        help="将第一阶段 prepared + pass1_raw 转换为可直接用于第二阶段的输入 JSON",
    )
    convert_parser.add_argument("--prepared", required=True, help="prepare-stage1 输出的 JSON 文件路径")
    convert_parser.add_argument("--annotation", required=True, help="annotate-stage1 输出的 pass1_raw JSON 路径")
    convert_parser.add_argument("--output", required=True, help="输出 stage2_input JSON 路径")

    annotate_stage2_parser = subparsers.add_parser(
        "annotate-stage2",
        help="批量调用 LLM 进行第二阶段三张表候选抽取",
    )
    annotate_stage2_parser.add_argument("--input", required=True, help="stage2_input JSON 路径")
    annotate_stage2_parser.add_argument("--output", required=True, help="输出 pass2_raw JSON 路径")
    annotate_stage2_parser.add_argument("--model", required=True, help="litellm 使用的模型名")
    annotate_stage2_parser.add_argument(
        "--template",
        default=str(DEFAULT_STAGE2_TEMPLATE_PATH),
        help="第二阶段 prompt 模板路径",
    )
    annotate_stage2_parser.add_argument("--temperature", type=float, default=0.1, help="LLM temperature")
    annotate_stage2_parser.add_argument("--max-tokens", type=int, default=None, help="LLM max_tokens")
    annotate_stage2_parser.add_argument("--retries", type=int, default=2, help="LLM 调用失败时的重试次数")
    annotate_stage2_parser.add_argument("--scene-id", action="append", default=None, help="只跑指定 scene_id，可重复传入")
    annotate_stage2_parser.add_argument("--max-scenes", type=int, default=None, help="最多处理多少个 scene")
    annotate_stage2_parser.add_argument("--prompts-dir", default=None, help="若提供，则把每个场景的渲染 prompt 单独落盘")
    annotate_stage2_parser.add_argument("--stream", action="store_true", help="开启 LLM 流式输出")
    annotate_stage2_parser.add_argument("--quiet-status", action="store_true", help="关闭场景级状态日志")

    render_stage2_parser = subparsers.add_parser(
        "render-stage2-prompts",
        help="只渲染 Stage 2A Document Prompt Package，不请求模型",
    )
    render_stage2_parser.add_argument("--input", required=True, help="Stage 2 Input JSON 路径")
    render_stage2_parser.add_argument("--output-dir", required=True, help="Prompt Package 输出目录")
    render_stage2_parser.add_argument("--template", default=str(DEFAULT_STAGE2_TEMPLATE_PATH), help="Prompt 模板路径")
    render_stage2_parser.add_argument("--scene-id", action="append", default=None, help="只渲染指定 scene_id")
    render_stage2_parser.add_argument("--max-scenes", type=int, default=None, help="最多渲染多少个场景")

    assemble_stage2_parser = subparsers.add_parser(
        "assemble-stage2-responses",
        help="校验离线 response 并组装 Stage 2A artifact",
    )
    _add_offline_assemble_flags(assemble_stage2_parser)

    annotate_stage2b_parser = subparsers.add_parser(
        "annotate-stage2b",
        help="结合 Stage 2A 事件候选抽取 Event Fact 与角色观点更新",
    )
    annotate_stage2b_parser.add_argument("--input", required=True, help="stage2_input JSON 路径")
    annotate_stage2b_parser.add_argument(
        "--stage2a-annotation",
        required=True,
        help="annotate-stage2 输出的 pass2_raw JSON 路径",
    )
    annotate_stage2b_parser.add_argument("--output", required=True, help="输出 Stage 2B JSON 路径")
    annotate_stage2b_parser.add_argument("--model", required=True, help="litellm 使用的模型名")
    annotate_stage2b_parser.add_argument(
        "--template",
        default=str(DEFAULT_STAGE2B_TEMPLATE_PATH),
        help="Stage 2B prompt 模板路径",
    )
    annotate_stage2b_parser.add_argument("--temperature", type=float, default=0.1, help="LLM temperature")
    annotate_stage2b_parser.add_argument("--max-tokens", type=int, default=None, help="LLM max_tokens")
    annotate_stage2b_parser.add_argument("--retries", type=int, default=2, help="LLM 调用失败时的重试次数")
    annotate_stage2b_parser.add_argument("--scene-id", action="append", default=None, help="只跑指定 scene_id，可重复传入")
    annotate_stage2b_parser.add_argument("--max-scenes", type=int, default=None, help="最多处理多少个 scene")
    annotate_stage2b_parser.add_argument("--prompts-dir", default=None, help="若提供，则把每个场景的渲染 prompt 单独落盘")
    annotate_stage2b_parser.add_argument("--stream", action="store_true", help="开启 LLM 流式输出")
    annotate_stage2b_parser.add_argument("--quiet-status", action="store_true", help="关闭场景级状态日志")
    annotate_stage2b_parser.add_argument(
        "--max-prompt-chars",
        type=int,
        default=None,
        help="完整场景 prompt 超过该字符数时启用重叠窗口；默认不限制",
    )
    annotate_stage2b_parser.add_argument("--window-utterances", type=int, default=40, help="fallback 窗口台词数")
    annotate_stage2b_parser.add_argument("--window-overlap", type=int, default=8, help="fallback 窗口重叠台词数")

    render_stage2b_parser = subparsers.add_parser(
        "render-stage2b-prompts",
        help="只渲染 Stage 2B Thought Prompt Package，不请求模型",
    )
    render_stage2b_parser.add_argument("--input", required=True, help="Stage 2 Input JSON 路径")
    render_stage2b_parser.add_argument("--stage2a-annotation", required=True, help="Stage 2A artifact 路径")
    render_stage2b_parser.add_argument("--output-dir", required=True, help="Prompt Package 输出目录")
    render_stage2b_parser.add_argument("--template", default=str(DEFAULT_STAGE2B_TEMPLATE_PATH), help="Prompt 模板路径")
    render_stage2b_parser.add_argument("--scene-id", action="append", default=None, help="只渲染指定 scene_id")
    render_stage2b_parser.add_argument("--max-scenes", type=int, default=None, help="最多渲染多少个场景")
    render_stage2b_parser.add_argument("--max-prompt-chars", type=int, default=None, help="超过字符数时启用窗口")
    render_stage2b_parser.add_argument("--window-utterances", type=int, default=40, help="窗口台词数")
    render_stage2b_parser.add_argument("--window-overlap", type=int, default=8, help="窗口重叠台词数")

    assemble_stage2b_parser = subparsers.add_parser(
        "assemble-stage2b-responses",
        help="校验离线窗口 response 并组装 Stage 2B artifact",
    )
    _add_offline_assemble_flags(assemble_stage2b_parser)

    normalize_stage3_parser = subparsers.add_parser(
        "normalize-stage3-rag",
        help="将第二阶段 raw 结果规范化为待入库的审查文件",
    )
    normalize_stage3_parser.add_argument("--input", required=True, help="stage2_input JSON 路径")
    normalize_stage3_parser.add_argument("--annotation", required=True, help="annotate-stage2 输出的 pass2_raw JSON 路径")
    normalize_stage3_parser.add_argument(
        "--relation-aggregation",
        default=None,
        help="可选：全量关系聚合审查 JSON；提供后用长期 State 替代逐场景关系",
    )
    normalize_stage3_parser.add_argument("--output", required=True, help="输出待入库 artifact JSON 路径")

    aggregate_relations_parser = subparsers.add_parser(
        "aggregate-stage3-relations",
        help="按有向角色对全量聚合 Relation Observation，输出长期关系审查文件",
    )
    aggregate_relations_parser.add_argument(
        "--input",
        required=True,
        action="append",
        help="stage2_input JSON 路径；跨集全量构建时按集重复传入",
    )
    aggregate_relations_parser.add_argument(
        "--annotation",
        required=True,
        action="append",
        help="与 --input 同序的 pass2_raw JSON 路径；可按集重复传入",
    )
    aggregate_relations_parser.add_argument("--output", required=True, help="输出长期关系聚合审查 JSON 路径")
    aggregate_relations_parser.add_argument("--model", required=True, help="litellm 使用的聚合模型名")
    aggregate_relations_parser.add_argument(
        "--template",
        default=str(DEFAULT_RELATION_AGGREGATION_TEMPLATE_PATH),
        help="关系聚合 prompt 模板路径",
    )
    aggregate_relations_parser.add_argument("--temperature", type=float, default=0.1, help="LLM temperature")
    aggregate_relations_parser.add_argument("--max-tokens", type=int, default=None, help="LLM max_tokens")
    aggregate_relations_parser.add_argument("--retries", type=int, default=2, help="LLM 调用失败时的重试次数")
    aggregate_relations_parser.add_argument("--quiet-status", action="store_true", help="关闭角色对级状态日志")

    render_relations_parser = subparsers.add_parser(
        "render-stage3-relation-prompts",
        help="只渲染跨场景角色关系聚合 Prompt Package",
    )
    render_relations_parser.add_argument("--input", required=True, action="append", help="Stage 2 Input，可重复")
    render_relations_parser.add_argument("--annotation", required=True, action="append", help="Stage 2A artifact，可重复")
    render_relations_parser.add_argument("--output-dir", required=True, help="Prompt Package 输出目录")
    render_relations_parser.add_argument(
        "--template",
        default=str(DEFAULT_RELATION_AGGREGATION_TEMPLATE_PATH),
        help="关系聚合 Prompt 模板路径",
    )

    assemble_relations_parser = subparsers.add_parser(
        "assemble-stage3-relations",
        help="校验离线角色对 response 并组装关系 State artifact",
    )
    _add_offline_assemble_flags(assemble_relations_parser)

    normalize_thoughts_parser = subparsers.add_parser(
        "normalize-stage3-thoughts",
        help="链接并跨场景聚合 Stage 2B 角色观点，输出风险审查文件",
    )
    normalize_thoughts_parser.add_argument("--input", required=True, help="stage2_input JSON 路径")
    normalize_thoughts_parser.add_argument("--stage2b-annotation", required=True, help="Stage 2B JSON 路径")
    normalize_thoughts_parser.add_argument(
        "--stage3-rag",
        required=True,
        help="normalize-stage3-rag 输出的现有三表 artifact 路径",
    )
    normalize_thoughts_parser.add_argument("--output", required=True, help="输出 Character Thought 审查 JSON 路径")
    normalize_thoughts_parser.add_argument(
        "--link-model",
        default=None,
        help="可选：用于解决跨场景语义引用的 litellm 模型；不提供时保守保留 unresolved",
    )
    normalize_thoughts_parser.add_argument(
        "--link-template",
        default=str(DEFAULT_LINK_TEMPLATE_PATH),
        help="Stage 3 引用链接 prompt 模板路径",
    )
    normalize_thoughts_parser.add_argument("--link-temperature", type=float, default=0.1, help="链接 LLM temperature")
    normalize_thoughts_parser.add_argument("--link-max-tokens", type=int, default=None, help="链接 LLM max_tokens")
    normalize_thoughts_parser.add_argument("--link-retries", type=int, default=2, help="链接 LLM 重试次数")
    normalize_thoughts_parser.add_argument("--max-link-candidates", type=int, default=8, help="每类最多提供多少个链接候选")

    render_thought_links_parser = subparsers.add_parser(
        "render-stage3-thought-link-prompts",
        help="只为 unresolved Character Thought 渲染语义链接 Prompt Package",
    )
    render_thought_links_parser.add_argument("--input", required=True, help="Stage 2 Input JSON 路径")
    render_thought_links_parser.add_argument("--stage2b-annotation", required=True, help="Stage 2B artifact 路径")
    render_thought_links_parser.add_argument("--stage3-rag", required=True, help="Stage 3 RAG artifact 路径")
    render_thought_links_parser.add_argument("--output-dir", required=True, help="Prompt Package 输出目录")
    render_thought_links_parser.add_argument(
        "--template",
        default=str(DEFAULT_LINK_TEMPLATE_PATH),
        help="观点链接 Prompt 模板路径",
    )
    render_thought_links_parser.add_argument("--max-link-candidates", type=int, default=8, help="每类最多候选数")

    assemble_thoughts_parser = subparsers.add_parser(
        "assemble-stage3-thoughts",
        help="应用离线语义链接 response 并组装 Character Thought artifact",
    )
    _add_offline_assemble_flags(assemble_thoughts_parser)

    backfill_stage3_parser = subparsers.add_parser(
        "backfill-stage3-relations",
        help="对现有 stage3 artifact 中的 character_relations.visible_to 进行自动回填",
    )
    backfill_stage3_parser.add_argument("--input", required=True, help="现有 stage3 artifact JSON 路径，或包含多个此类 JSON 的目录")
    backfill_stage3_parser.add_argument("--output", required=True, help="输出更新后的 stage3 artifact JSON 路径，或目录")

    lore_review_parser = subparsers.add_parser(
        "build-lore-dedup-review",
        help="为 stage3 lore_entries 生成去重 review 文件与自动 decisions 草稿",
    )
    lore_review_parser.add_argument("--input-dir", required=True, help="包含 stage3 artifact JSON 的输入目录")
    lore_review_parser.add_argument("--review-output", required=True, help="输出 lore dedup review JSON 路径")
    lore_review_parser.add_argument("--decisions-output", required=True, help="输出自动 decisions 草稿 JSON 路径")

    lore_apply_parser = subparsers.add_parser(
        "apply-lore-dedup-decisions",
        help="应用 lore dedup decisions，并输出 deduped stage3 artifact 目录",
    )
    lore_apply_parser.add_argument("--input-dir", required=True, help="包含 stage3 artifact JSON 的输入目录")
    lore_apply_parser.add_argument("--decisions", required=True, help="lore dedup decisions JSON 路径")
    lore_apply_parser.add_argument("--output-dir", required=True, help="输出 deduped stage3 artifact 的目录")

    import_stage3_parser = subparsers.add_parser(
        "import-stage3-rag",
        help="将待入库 artifact 正式写入 RAG 数据库",
    )
    import_stage3_parser.add_argument("--input", required=True, help="normalize-stage3-rag 输出的 JSON 路径")
    import_stage3_parser.add_argument(
        "--qdrant-location",
        default="../knowledge_base/default_world_info",
        help="Qdrant 本地路径或服务地址",
    )
    import_stage3_parser.add_argument(
        "--qdrant-connect-type",
        default="local",
        choices=["memory", "local", "grpc", "http"],
        help="Qdrant 连接方式",
    )
    import_stage3_parser.add_argument(
        "--embedding-model-path",
        default="pretrained_models/multilingual-e5-small",
        help="sentence-transformer 模型路径",
    )

    import_thoughts_parser = subparsers.add_parser(
        "import-stage3-thoughts",
        help="将通过人工复核的 Character Thought 写入 Qdrant",
    )
    import_thoughts_parser.add_argument("--input", required=True, help="Character Thought 审查 JSON 路径")
    import_thoughts_parser.add_argument(
        "--allow-unreviewed",
        action="store_true",
        help="测试时允许导入未复核记录；unresolved 和结构非法记录仍会跳过",
    )
    import_thoughts_parser.add_argument(
        "--qdrant-location",
        default="../knowledge_base/default_world_info",
        help="Qdrant 本地路径或服务地址",
    )
    import_thoughts_parser.add_argument(
        "--qdrant-connect-type",
        default="local",
        choices=["memory", "local", "grpc", "http"],
        help="Qdrant 连接方式",
    )
    import_thoughts_parser.add_argument(
        "--embedding-model-path",
        default="pretrained_models/multilingual-e5-small",
        help="sentence-transformer 模型路径",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "prepare-stage1":
        artifact = prepare_stage1_artifact(
            subtitle_path=args.subtitle,
            anime_title=args.anime_title,
            series_id=SeriesId(args.series_id),
            timeline_id=args.timeline_id,
            story_year=args.story_year,
            canon_branch=CanonBranch(args.canon_branch),
            scene_gap_ms=args.scene_gap_ms,
        )
        save_stage1_prepared_artifact(artifact, args.output)
        print(f"已写入第一阶段预处理结果: {Path(args.output).resolve()}")
        print(f"scene 数量: {len(artifact.scenes)}")
        return 0

    if args.command == "publish-worldbook":
        manifest = publish_worldbook_package(
            artifact_path=args.artifact,
            package_dir=args.package_dir,
            package_id=args.package_id,
            package_version=args.package_version,
            display_name=args.display_name,
            timeline_id=args.timeline_id,
            id_map_path=args.id_map,
            allocate_new_ids=args.allocate_new_ids,
        )
        print(f"已发布世界书包: {manifest.package_id} {manifest.package_version}")
        return 0

    if args.command == "complete-prompt-package":
        report = complete_prompt_package_with_litellm(
            manifest_path=args.manifest,
            llm_config=LiteLLMConfig(
                model=args.model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                retries=args.retries,
            ),
            overwrite=args.overwrite,
            allow_stale=args.allow_stale,
            stream=args.stream,
            status_callback=None if args.quiet_status else default_status_printer,
            stream_callback=default_stream_printer if args.stream else None,
        )
        print(f"已完成 response 数: {len(report.completed_task_ids)}")
        print(f"已跳过 response 数: {len(report.skipped_task_ids)}")
        print(f"请求失败数: {len(report.errors)}")
        for error in report.errors:
            print(f"- {error}")
        return 1 if report.errors else 0

    if args.command == "render-stage1-prompts":
        artifact = load_stage1_prepared_artifact(args.prepared)
        manifest = prepare_stage1_prompt_package(
            artifact=artifact,
            prepared_path=args.prepared,
            output_dir=args.output_dir,
            template_path=args.template,
            scene_ids=set(args.scene_id) if args.scene_id else None,
            max_scenes=args.max_scenes,
        )
        print(f"已写入 Stage 1 Prompt Package: {Path(args.output_dir).resolve()}")
        print(f"任务数: {len(manifest.tasks)}")
        return 0

    if args.command == "assemble-stage1-responses":
        result = assemble_stage1_prompt_package(
            manifest_path=args.manifest,
            model_label=args.model_label,
            allow_partial=args.allow_partial,
            allow_stale=args.allow_stale,
        )
        save_stage1_annotation_artifact(result, args.output)
        if args.stage2_output is not None:
            manifest = load_prompt_package_manifest(args.manifest)
            prepared_path = manifest.parameters["prepared_path"]
            prepared_artifact = load_stage1_prepared_artifact(prepared_path)
            stage2_input = build_stage2_input_artifact(
                prepared_artifact=prepared_artifact,
                annotation_artifact=result,
                source_stage1_output_path=str(Path(args.output).resolve()),
            )
            save_stage2_input_artifact(stage2_input, args.stage2_output)
            print(f"已写入 Stage 2 Input: {Path(args.stage2_output).resolve()}")
        error_count = sum(1 for item in result.results if item.error is not None)
        print(f"已写入 Stage 1 artifact: {Path(args.output).resolve()}")
        print(f"成功场景数: {len(result.results) - error_count}")
        print(f"失败场景数: {error_count}")
        return 1 if error_count else 0

    if args.command == "annotate-stage1":
        artifact = load_stage1_prepared_artifact(args.prepared)
        result = annotate_prepared_stage1_artifact(
            artifact=artifact,
            llm_config=LiteLLMConfig(
                model=args.model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                retries=args.retries,
            ),
            template_path=args.template,
            scene_ids=set(args.scene_id) if args.scene_id else None,
            max_scenes=args.max_scenes,
            prompts_dir=args.prompts_dir,
            stream=args.stream,
            status_callback=None if args.quiet_status else default_status_printer,
            stream_callback=default_stream_printer if args.stream else None,
        )
        save_stage1_annotation_artifact(result, args.output)
        stage2_output_path = (
            Path(args.stage2_output).resolve()
            if args.stage2_output is not None
            else derive_stage2_input_output_path(args.output).resolve()
        )
        stage2_input_artifact = build_stage2_input_artifact(
            prepared_artifact=artifact,
            annotation_artifact=result,
            source_stage1_output_path=str(Path(args.output).resolve()),
        )
        save_stage2_input_artifact(stage2_input_artifact, stage2_output_path)
        success_count = sum(1 for item in result.results if item.annotation is not None)
        error_count = sum(1 for item in result.results if item.error is not None)
        print(f"已写入第一阶段原始标注结果: {Path(args.output).resolve()}")
        print(f"已写入第二阶段输入结果: {stage2_output_path}")
        print(f"成功场景数: {success_count}")
        print(f"失败场景数: {error_count}")
        print(f"可直接进入第二阶段的场景数: {len(stage2_input_artifact.scenes)}")
        print(f"被跳过的场景数: {len(stage2_input_artifact.skipped_scenes)}")
        return 0

    if args.command == "build-stage2-input":
        prepared_artifact = load_stage1_prepared_artifact(args.prepared)
        annotation_artifact = load_stage1_annotation_artifact(args.annotation)
        stage2_input_artifact = build_stage2_input_artifact(
            prepared_artifact=prepared_artifact,
            annotation_artifact=annotation_artifact,
            source_stage1_output_path=str(Path(args.annotation).resolve()),
        )
        save_stage2_input_artifact(stage2_input_artifact, args.output)
        print(f"已写入第二阶段输入结果: {Path(args.output).resolve()}")
        print(f"可直接进入第二阶段的场景数: {len(stage2_input_artifact.scenes)}")
        print(f"被跳过的场景数: {len(stage2_input_artifact.skipped_scenes)}")
        return 0

    if args.command == "render-stage2-prompts":
        artifact = load_stage2_input_artifact(args.input)
        manifest = prepare_stage2_prompt_package(
            artifact=artifact,
            input_path=args.input,
            output_dir=args.output_dir,
            template_path=args.template,
            scene_ids=set(args.scene_id) if args.scene_id else None,
            max_scenes=args.max_scenes,
        )
        print(f"已写入 Stage 2A Prompt Package: {Path(args.output_dir).resolve()}")
        print(f"任务数: {len(manifest.tasks)}")
        return 0

    if args.command == "assemble-stage2-responses":
        result = assemble_stage2_prompt_package(
            manifest_path=args.manifest,
            model_label=args.model_label,
            allow_partial=args.allow_partial,
            allow_stale=args.allow_stale,
        )
        save_stage2_annotation_artifact(result, args.output)
        error_count = sum(1 for item in result.results if item.error is not None)
        print(f"已写入 Stage 2A artifact: {Path(args.output).resolve()}")
        print(f"成功场景数: {len(result.results) - error_count}")
        print(f"失败场景数: {error_count}")
        return 1 if error_count else 0

    if args.command == "annotate-stage2":
        artifact = load_stage2_input_artifact(args.input)
        result = annotate_stage2_input_artifact(
            artifact=artifact,
            llm_config=LiteLLMConfig(
                model=args.model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                retries=args.retries,
            ),
            template_path=args.template,
            scene_ids=set(args.scene_id) if args.scene_id else None,
            max_scenes=args.max_scenes,
            prompts_dir=args.prompts_dir,
            stream=args.stream,
            status_callback=None if args.quiet_status else default_status_printer,
            stream_callback=default_stream_printer if args.stream else None,
        )
        save_stage2_annotation_artifact(result, args.output)
        success_count = sum(1 for item in result.results if item.annotation is not None)
        error_count = sum(1 for item in result.results if item.error is not None)
        print(f"已写入第二阶段原始抽取结果: {Path(args.output).resolve()}")
        print(f"成功场景数: {success_count}")
        print(f"失败场景数: {error_count}")
        return 0

    if args.command == "annotate-stage2b":
        input_artifact = load_stage2_input_artifact(args.input)
        stage2a_artifact = load_stage2_annotation_artifact(args.stage2a_annotation)
        result = annotate_stage2b_artifact(
            input_artifact=input_artifact,
            stage2a_artifact=stage2a_artifact,
            llm_config=LiteLLMConfig(
                model=args.model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                retries=args.retries,
            ),
            template_path=args.template,
            source_stage2a_output_path=str(Path(args.stage2a_annotation).resolve()),
            scene_ids=set(args.scene_id) if args.scene_id else None,
            max_scenes=args.max_scenes,
            prompts_dir=args.prompts_dir,
            stream=args.stream,
            status_callback=None if args.quiet_status else default_status_printer,
            stream_callback=default_stream_printer if args.stream else None,
            max_prompt_chars=args.max_prompt_chars,
            window_utterances=args.window_utterances,
            window_overlap=args.window_overlap,
        )
        save_stage2b_annotation_artifact(result, args.output)
        success_count = sum(1 for item in result.results if item.annotation is not None)
        error_count = sum(1 for item in result.results if item.error is not None)
        fact_count = sum(
            len(item.annotation.event_facts)
            for item in result.results
            if item.annotation is not None
        )
        thought_count = sum(
            len(item.annotation.character_thought_updates)
            for item in result.results
            if item.annotation is not None
        )
        print(f"已写入 Stage 2B 角色观点抽取结果: {Path(args.output).resolve()}")
        print(f"成功场景数: {success_count}")
        print(f"失败场景数: {error_count}")
        print(f"Event Fact 候选数: {fact_count}")
        print(f"角色观点更新候选数: {thought_count}")
        return 0

    if args.command == "render-stage2b-prompts":
        input_artifact = load_stage2_input_artifact(args.input)
        stage2a_artifact = load_stage2_annotation_artifact(args.stage2a_annotation)
        manifest = prepare_stage2b_prompt_package(
            input_artifact=input_artifact,
            stage2a_artifact=stage2a_artifact,
            input_path=args.input,
            stage2a_path=args.stage2a_annotation,
            output_dir=args.output_dir,
            template_path=args.template,
            scene_ids=set(args.scene_id) if args.scene_id else None,
            max_scenes=args.max_scenes,
            max_prompt_chars=args.max_prompt_chars,
            window_utterances=args.window_utterances,
            window_overlap=args.window_overlap,
        )
        print(f"已写入 Stage 2B Prompt Package: {Path(args.output_dir).resolve()}")
        print(f"任务数: {len(manifest.tasks)}")
        return 0

    if args.command == "assemble-stage2b-responses":
        result = assemble_stage2b_prompt_package(
            manifest_path=args.manifest,
            model_label=args.model_label,
            allow_partial=args.allow_partial,
            allow_stale=args.allow_stale,
        )
        save_stage2b_annotation_artifact(result, args.output)
        error_count = sum(1 for item in result.results if item.error is not None)
        print(f"已写入 Stage 2B artifact: {Path(args.output).resolve()}")
        print(f"成功场景数: {len(result.results) - error_count}")
        print(f"失败场景数: {error_count}")
        return 1 if error_count else 0

    if args.command == "render-stage3-relation-prompts":
        if len(args.input) != len(args.annotation):
            parser.error("render-stage3-relation-prompts 的 --input 与 --annotation 数量必须一致")
        input_artifacts = [load_stage2_input_artifact(path) for path in args.input]
        annotation_artifacts = [load_stage2_annotation_artifact(path) for path in args.annotation]
        manifest = prepare_stage3_relation_prompt_package(
            input_artifacts=input_artifacts,
            annotation_artifacts=annotation_artifacts,
            input_paths=args.input,
            annotation_paths=args.annotation,
            output_dir=args.output_dir,
            template_path=args.template,
        )
        print(f"已写入 Stage 3 Relation Prompt Package: {Path(args.output_dir).resolve()}")
        print(f"有向角色对任务数: {len(manifest.tasks)}")
        return 0

    if args.command == "assemble-stage3-relations":
        relation_artifact = assemble_stage3_relation_prompt_package(
            manifest_path=args.manifest,
            model_label=args.model_label,
            allow_partial=args.allow_partial,
            allow_stale=args.allow_stale,
        )
        save_stage3_relation_aggregation_artifact(relation_artifact, args.output)
        response_error_count = sum(
            1
            for issue in relation_artifact.issues
            if "response" in issue.message or "manifest" in issue.message
        )
        print(f"已写入 Stage 3 Relation artifact: {Path(args.output).resolve()}")
        print(f"Relation Observation 数量: {len(relation_artifact.observations)}")
        print(f"Character Relation State 数量: {len(relation_artifact.character_relation_states)}")
        print(f"Response 问题数: {response_error_count}")
        return 1 if response_error_count else 0

    if args.command == "aggregate-stage3-relations":
        if len(args.input) != len(args.annotation):
            parser.error("aggregate-stage3-relations 的 --input 与 --annotation 数量必须一致")
        relation_artifact = build_stage3_relation_aggregation_artifact_from_many(
            input_artifacts=[load_stage2_input_artifact(path) for path in args.input],
            annotation_artifacts=[load_stage2_annotation_artifact(path) for path in args.annotation],
            llm_config=LiteLLMConfig(
                model=args.model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                retries=args.retries,
            ),
            template_path=args.template,
            status_callback=None if args.quiet_status else default_status_printer,
        )
        save_stage3_relation_aggregation_artifact(relation_artifact, args.output)
        high_risk_count = sum(
            1
            for record in relation_artifact.character_relation_states
            if record.risk_level == "high"
        )
        validation_error_count = sum(
            1
            for record in relation_artifact.character_relation_states
            if record.validation_errors
        )
        print(f"已写入角色关系聚合审查 artifact: {Path(args.output).resolve()}")
        print(f"Relation Observation 数量: {len(relation_artifact.observations)}")
        print(f"Character Relation State 数量: {len(relation_artifact.character_relation_states)}")
        print(f"未合并 Observation 数量: {len(relation_artifact.unmerged_observations)}")
        print(f"高风险 State 数量: {high_risk_count}")
        print(f"结构校验失败 State 数量: {validation_error_count}")
        return 0

    if args.command == "normalize-stage3-rag":
        input_artifact = load_stage2_input_artifact(args.input)
        annotation_artifact = load_stage2_annotation_artifact(args.annotation)
        normalized_artifact = build_stage3_normalized_import_artifact(
            input_artifact=input_artifact,
            annotation_artifact=annotation_artifact,
            relation_aggregation_artifact=(
                None
                if args.relation_aggregation is None
                else load_stage3_relation_aggregation_artifact(args.relation_aggregation)
            ),
        )
        save_stage3_normalized_import_artifact(normalized_artifact, args.output)
        print(f"已写入待入库 artifact: {Path(args.output).resolve()}")
        print(f"story_events 条目数: {len(normalized_artifact.story_events)}")
        print(f"character_relations 条目数: {len(normalized_artifact.character_relations)}")
        print(f"lore_entries 条目数: {len(normalized_artifact.lore_entries)}")
        print(f"规范化问题数: {len(normalized_artifact.issues)}")
        return 0

    if args.command == "render-stage3-thought-link-prompts":
        input_artifact = load_stage2_input_artifact(args.input)
        stage2b_artifact = load_stage2b_annotation_artifact(args.stage2b_annotation)
        stage3_rag_artifact = load_stage3_normalized_import_artifact(args.stage3_rag)
        manifest = prepare_stage3_thought_link_prompt_package(
            input_artifact=input_artifact,
            stage2b_artifact=stage2b_artifact,
            stage3_rag_artifact=stage3_rag_artifact,
            input_path=args.input,
            stage2b_path=args.stage2b_annotation,
            stage3_rag_path=args.stage3_rag,
            output_dir=args.output_dir,
            template_path=args.template,
            max_link_candidates=args.max_link_candidates,
        )
        print(f"已写入 Stage 3 Thought Link Prompt Package: {Path(args.output_dir).resolve()}")
        print(f"Unresolved 链接任务数: {len(manifest.tasks)}")
        return 0

    if args.command == "assemble-stage3-thoughts":
        thought_artifact = assemble_stage3_thought_link_prompt_package(
            manifest_path=args.manifest,
            model_label=args.model_label,
            allow_partial=args.allow_partial,
            allow_stale=args.allow_stale,
        )
        save_stage3_thought_import_artifact(thought_artifact, args.output)
        response_error_count = sum(
            1
            for issue in thought_artifact.issues
            if "response" in issue.message or "manifest" in issue.message
        )
        unresolved_count = sum(
            1 for record in thought_artifact.character_thoughts if record.link_status == "unresolved"
        )
        print(f"已写入 Stage 3 Thought artifact: {Path(args.output).resolve()}")
        print(f"未解决链接数: {unresolved_count}")
        print(f"Response 问题数: {response_error_count}")
        return 1 if response_error_count else 0

    if args.command == "normalize-stage3-thoughts":
        input_artifact = load_stage2_input_artifact(args.input)
        stage2b_artifact = load_stage2b_annotation_artifact(args.stage2b_annotation)
        stage3_rag_artifact = load_stage3_normalized_import_artifact(args.stage3_rag)
        thought_artifact = build_stage3_thought_import_artifact(
            input_artifact=input_artifact,
            stage2b_artifact=stage2b_artifact,
            stage3_rag_artifact=stage3_rag_artifact,
            linker_llm_config=(
                None
                if args.link_model is None
                else LiteLLMConfig(
                    model=args.link_model,
                    temperature=args.link_temperature,
                    max_tokens=args.link_max_tokens,
                    retries=args.link_retries,
                )
            ),
            linker_template_path=args.link_template,
            max_link_candidates=args.max_link_candidates,
        )
        save_stage3_thought_import_artifact(thought_artifact, args.output)
        unresolved_count = sum(
            1 for record in thought_artifact.character_thoughts if record.link_status == "unresolved"
        )
        high_risk_count = sum(
            1 for record in thought_artifact.character_thoughts if record.risk_level == "high"
        )
        print(f"已写入 Character Thought 审查 artifact: {Path(args.output).resolve()}")
        print(f"Event Fact 条目数: {len(thought_artifact.event_facts)}")
        print(f"观点更新条目数: {len(thought_artifact.linked_updates)}")
        print(f"观点状态条目数: {len(thought_artifact.character_thoughts)}")
        print(f"未解决链接数: {unresolved_count}")
        print(f"高风险条目数: {high_risk_count}")
        return 0

    if args.command == "backfill-stage3-relations":
        input_path = Path(args.input)
        output_path = Path(args.output)
        if input_path.is_dir():
            artifacts = load_stage3_artifacts_from_directory(input_path)
            updated_artifacts = backfill_stage3_character_relations_across_artifacts(artifacts)
            save_stage3_artifacts_to_directory(updated_artifacts, output_path)
            total_relations = sum(len(artifact.character_relations) for _, artifact in updated_artifacts)
            total_issues = sum(len(artifact.issues) for _, artifact in updated_artifacts)
            print(f"已写入更新后的 stage3 artifact 目录: {output_path.resolve()}")
            print(f"处理文件数: {len(updated_artifacts)}")
            print(f"character_relations 总条目数: {total_relations}")
            print(f"issues 总条目数: {total_issues}")
            return 0

        artifact = load_stage3_normalized_import_artifact(input_path)
        updated_artifact = backfill_existing_stage3_character_relations(artifact)
        save_stage3_normalized_import_artifact(updated_artifact, output_path)
        print(f"已写入更新后的 stage3 artifact: {output_path.resolve()}")
        print(f"character_relations 条目数: {len(updated_artifact.character_relations)}")
        print(f"issues 条目数: {len(updated_artifact.issues)}")
        return 0

    if args.command == "build-lore-dedup-review":
        review_artifact = build_lore_dedup_review_from_directory(args.input_dir)
        save_lore_dedup_review_artifact(review_artifact, args.review_output)
        save_lore_dedup_decisions(list(review_artifact.automatic_decisions), args.decisions_output)
        review_group_count = len(review_artifact.exact_groups) + len(review_artifact.fuzzy_groups)
        required_decision_count = sum(
            1
            for group in [*review_artifact.exact_groups, *review_artifact.fuzzy_groups]
            if group.requires_decision
        )
        print(f"已写入 lore dedup review: {Path(args.review_output).resolve()}")
        print(f"已写入自动 decisions 草稿: {Path(args.decisions_output).resolve()}")
        print(f"输入文件数: {len(review_artifact.input_files)}")
        print(f"review 分组数: {review_group_count}")
        print(f"需要人工决策的分组数: {required_decision_count}")
        print(f"自动合并决策数: {len(review_artifact.automatic_decisions)}")
        return 0

    if args.command == "apply-lore-dedup-decisions":
        deduped_artifacts = apply_lore_dedup_decisions_from_files(
            input_dir=args.input_dir,
            decisions_path=args.decisions,
        )
        save_deduped_stage3_artifacts(deduped_artifacts, args.output_dir)
        total_lore_entries = sum(len(artifact.lore_entries) for _, artifact in deduped_artifacts)
        print(f"已写入 deduped stage3 artifact 目录: {Path(args.output_dir).resolve()}")
        print(f"处理文件数: {len(deduped_artifacts)}")
        print(f"lore_entries 总条目数: {total_lore_entries}")
        return 0

    if args.command == "import-stage3-rag":
        normalized_artifact = load_stage3_normalized_import_artifact(args.input)
        service = create_rag_service(
            qdrant_location=args.qdrant_location,
            qdrant_connect_type=args.qdrant_connect_type,
            embedding_model_path=args.embedding_model_path,
        )
        try:
            reports = upsert_stage3_normalized_import_artifact(normalized_artifact, service)
        finally:
            service.close()
        print(f"已导入待入库 artifact: {Path(args.input).resolve()}")
        for collection_name, report in reports.items():
            print(
                f"{collection_name}: 请求 {report.requested_count} 条，"
                f"成功 {report.success_count} 条，失败 {report.failure_count} 条"
            )
        return 0

    if args.command == "import-stage3-thoughts":
        thought_artifact = load_stage3_thought_import_artifact(args.input)
        service = create_rag_service(
            qdrant_location=args.qdrant_location,
            qdrant_connect_type=args.qdrant_connect_type,
            embedding_model_path=args.embedding_model_path,
        )
        try:
            report = upsert_stage3_thought_import_artifact(
                thought_artifact,
                service,
                require_review_approval=not args.allow_unreviewed,
            )
        finally:
            service.close()
        print(f"已导入 Character Thought artifact: {Path(args.input).resolve()}")
        print(
            f"character_thoughts: 请求 {report.requested_count} 条，"
            f"成功 {report.success_count} 条，失败 {report.failure_count} 条"
        )
        return 0

    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
