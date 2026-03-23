"""RAG 标注流水线 CLI。"""

from __future__ import annotations

import argparse
from pathlib import Path

from rag.models import CanonBranch, SeasonId, SeriesId

from .llm_client import LiteLLMConfig
from .stage1_speaker_annotation import (
    DEFAULT_TEMPLATE_PATH,
    annotate_prepared_stage1_artifact,
    default_status_printer,
    default_stream_printer,
    load_stage1_annotation_artifact,
    load_stage1_prepared_artifact,
    prepare_stage1_artifact,
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
    load_stage2_annotation_artifact,
    save_stage2_annotation_artifact,
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG 第一阶段字幕标注流水线")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare-stage1", help="解析字幕并导出第一阶段预处理结果")
    prepare_parser.add_argument("--subtitle", required=True, help="输入字幕文件路径")
    prepare_parser.add_argument("--output", required=True, help="输出 prepared JSON 路径")
    prepare_parser.add_argument("--anime-title", default="It's MyGO!!!!!", help="动画标题")
    prepare_parser.add_argument("--series-id", default=SeriesId.ITS_MYGO.value, help="系列 id")
    prepare_parser.add_argument("--season-id", type=int, default=SeasonId.THREE.value, help="season id")
    prepare_parser.add_argument("--canon-branch", default=CanonBranch.MAIN.value, help="剧情分支")
    prepare_parser.add_argument("--scene-gap-ms", type=int, default=12000, help="场景切分时间阈值")

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

    normalize_stage3_parser = subparsers.add_parser(
        "normalize-stage3-rag",
        help="将第二阶段 raw 结果规范化为待入库的审查文件",
    )
    normalize_stage3_parser.add_argument("--input", required=True, help="stage2_input JSON 路径")
    normalize_stage3_parser.add_argument("--annotation", required=True, help="annotate-stage2 输出的 pass2_raw JSON 路径")
    normalize_stage3_parser.add_argument("--output", required=True, help="输出待入库 artifact JSON 路径")

    backfill_stage3_parser = subparsers.add_parser(
        "backfill-stage3-relations",
        help="对现有 stage3 artifact 中的 character_relations.visible_to 进行自动回填",
    )
    backfill_stage3_parser.add_argument("--input", required=True, help="现有 stage3 artifact JSON 路径，或包含多个此类 JSON 的目录")
    backfill_stage3_parser.add_argument("--output", required=True, help="输出更新后的 stage3 artifact JSON 路径，或目录")

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "prepare-stage1":
        artifact = prepare_stage1_artifact(
            subtitle_path=args.subtitle,
            anime_title=args.anime_title,
            series_id=SeriesId(args.series_id),
            season_id=SeasonId(args.season_id),
            canon_branch=CanonBranch(args.canon_branch),
            scene_gap_ms=args.scene_gap_ms,
        )
        save_stage1_prepared_artifact(artifact, args.output)
        print(f"已写入第一阶段预处理结果: {Path(args.output).resolve()}")
        print(f"scene 数量: {len(artifact.scenes)}")
        return 0

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

    if args.command == "normalize-stage3-rag":
        input_artifact = load_stage2_input_artifact(args.input)
        annotation_artifact = load_stage2_annotation_artifact(args.annotation)
        normalized_artifact = build_stage3_normalized_import_artifact(
            input_artifact=input_artifact,
            annotation_artifact=annotation_artifact,
        )
        save_stage3_normalized_import_artifact(normalized_artifact, args.output)
        print(f"已写入待入库 artifact: {Path(args.output).resolve()}")
        print(f"story_events 条目数: {len(normalized_artifact.story_events)}")
        print(f"character_relations 条目数: {len(normalized_artifact.character_relations)}")
        print(f"lore_entries 条目数: {len(normalized_artifact.lore_entries)}")
        print(f"规范化问题数: {len(normalized_artifact.issues)}")
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

    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
