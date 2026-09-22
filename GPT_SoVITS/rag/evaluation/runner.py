"""运行版本化世界书检索案例并输出结构化报告。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence
from uuid import UUID

from rag.worldbook.paths import WorldbookPaths
from rag.worldbook.models import WorldbookReadiness
from rag.worldbook.runtime import (
    WorldbookIndexReadinessState,
    create_worldbook_conversation_service,
)
from rag.worldbook.runtime.catalog import WorldbookRootCatalog
from rag.worldbook.runtime.models import (
    WorldbookResolvedContext,
)
from rag.worldbook.runtime.service import WorldbookConversationService

from .models import (
    WorldbookEvaluationCase,
    WorldbookEvaluationCaseFile,
    WorldbookEvaluationCaseResult,
    WorldbookEvaluationReport,
)


def load_evaluation_cases(path: Path) -> WorldbookEvaluationCaseFile:
    """从 JSON 文件严格读取一组版本化检索案例。"""

    raw = json.loads(path.read_text(encoding="utf-8"))
    return WorldbookEvaluationCaseFile.model_validate(raw)


class WorldbookEvaluationRunner:
    """解析案例上下文并通过正式运行时 facade 执行验收。"""

    def __init__(
        self,
        catalog: WorldbookRootCatalog,
        service: WorldbookConversationService,
    ) -> None:
        """注入与聊天运行时相同的目录和检索服务。"""

        self._catalog = catalog
        self._service = service

    def run(
        self,
        case_file: WorldbookEvaluationCaseFile,
        *,
        backend_label: str,
    ) -> WorldbookEvaluationReport:
        """运行全部案例，并使基础设施失败不能被误判为零命中。"""

        results = [self._run_case(case) for case in case_file.cases]
        passed = sum(result.passed for result in results)
        return WorldbookEvaluationReport(
            dataset_id=case_file.dataset_id,
            backend_label=backend_label,
            total=len(results),
            passed=passed,
            pass_rate=passed / len(results),
            results=results,
        )

    def _run_case(
        self,
        case: WorldbookEvaluationCase,
    ) -> WorldbookEvaluationCaseResult:
        """运行单个案例并比较模型真正可见的结果条目。"""

        try:
            context = self._catalog.resolve(
                case.root_package_id,
                case.episode,
                case.character_id,
            )
            retrieved, failure = self._query(case, context)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return WorldbookEvaluationCaseResult(
                case_id=case.case_id,
                passed=False,
                failure=str(exc),
            )
        retrieved_set = set(retrieved)
        missing = [
            entry_id
            for entry_id in case.expected_entry_ids
            if entry_id not in retrieved_set
        ]
        leaked = [
            entry_id
            for entry_id in case.forbidden_entry_ids
            if entry_id in retrieved_set
        ]
        empty_failed = case.expect_empty and bool(retrieved)
        return WorldbookEvaluationCaseResult(
            case_id=case.case_id,
            passed=not failure and not missing and not leaked and not empty_failed,
            retrieved_entry_ids=retrieved,
            missing_expected_entry_ids=missing,
            leaked_forbidden_entry_ids=leaked,
            failure=failure,
        )

    def _query(
        self,
        case: WorldbookEvaluationCase,
        context: WorldbookResolvedContext,
    ) -> tuple[list[UUID], str | None]:
        """按案例类型调用正式服务并读取精确选中条目 UUID。"""

        if case.query_type == "direct_thought":
            result = self._service.direct_thoughts(
                context,
                case.query,
                case.current_user_text or case.query,
            )
            return (
                result.trace.selected_entry_ids,
                result.failure.message if result.failure is not None else None,
            )
        if case.query_type == "direct_context":
            result = self._service.direct_context(
                context,
                case.query,
                case.current_user_text or case.query,
            )
            return (
                result.trace.selected_entry_ids,
                result.failure.message if result.failure is not None else None,
            )
        if case.query_type == "lore":
            result = self._service.search_lore(context, case.query)
            return (
                result.trace.selected_entry_ids,
                result.failure.message if result.failure is not None else None,
            )
        if case.query_type == "relation":
            if case.target_character_id is None:
                return [], "relation 案例缺少目标角色"
            result = self._service.query_relation(
                context,
                case.target_character_id,
                case.query,
                case.relation_episode,
            )
            return (
                result.trace.selected_entry_ids,
                result.failure.message if result.failure is not None else None,
            )
        result = self._service.search_memory(context, case.query)
        return (
            result.trace.selected_entry_ids,
            result.failure.message if result.failure is not None else None,
        )


def _build_parser() -> argparse.ArgumentParser:
    """创建真实 E5 验收命令行参数。"""

    parser = argparse.ArgumentParser(description="运行世界书真实 E5 检索验收")
    parser.add_argument("cases", type=Path, help="案例 JSON 路径")
    parser.add_argument("--output", type=Path, help="可选报告 JSON 输出路径")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """使用应用自带 embedding 和当前世界书索引执行质量门。"""

    arguments = _build_parser().parse_args(argv)
    app_root = Path(__file__).resolve().parents[3]
    paths = WorldbookPaths(app_root)
    catalog = WorldbookRootCatalog(paths.official_packages, paths.user_state)
    service = create_worldbook_conversation_service(
        app_root,
        WorldbookIndexReadinessState(WorldbookReadiness.READY),
    )
    try:
        report = WorldbookEvaluationRunner(catalog, service).run(
            load_evaluation_cases(arguments.cases),
            backend_label="multilingual-e5-small",
        )
    finally:
        service.close()
    report_json = report.model_dump_json(indent=2)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report_json + "\n", encoding="utf-8")
    else:
        print(report_json)
    return 0 if report.passed == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
