"""Reindex Stage3 story_event time_order and sync relation visible_from."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PIPELINE_ROOT = Path(__file__).resolve().parent
PROJECT_PACKAGE_ROOT = PIPELINE_ROOT.parents[1]
if str(PROJECT_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE_ROOT))

try:
    from rag.pipeline.schemas import Stage3NormalizedImportArtifact
    from rag.pipeline.stage3_rag_import import (
        load_stage3_normalized_import_artifact,
        save_stage3_normalized_import_artifact,
    )
except ImportError:
    from .schemas import Stage3NormalizedImportArtifact
    from .stage3_rag_import import (
        load_stage3_normalized_import_artifact,
        save_stage3_normalized_import_artifact,
    )


def _replace_story_event_point_id_suffix(point_id: str, old_time_order: int, new_time_order: int) -> str:
    """Keep point_id suffix aligned with updated time_order when possible."""

    head, sep, tail = point_id.rpartition(":")
    if not sep:
        return point_id
    if not tail.isdigit():
        return point_id
    if int(tail) != old_time_order:
        return point_id
    return f"{head}:{new_time_order}"


def reindex_stage3_time_orders(
    artifact: Stage3NormalizedImportArtifact,
    new_start: int,
) -> Stage3NormalizedImportArtifact:
    """Reindex story_events and update character_relations visible_from."""

    if not artifact.story_events:
        return artifact

    indexed_story_events = sorted(
        enumerate(artifact.story_events),
        key=lambda item: (item[1].document.time_order, item[0]),
    )

    old_min = indexed_story_events[0][1].document.time_order
    old_to_new: dict[int, int] = {}
    scene_anchor_new: dict[str, int] = {}

    for offset, (_, record) in enumerate(indexed_story_events):
        old_time_order = record.document.time_order
        new_time_order = new_start + offset

        if old_time_order not in old_to_new:
            old_to_new[old_time_order] = new_time_order

        record.document.time_order = new_time_order
        record.document.visible_from = new_time_order
        record.point_id = _replace_story_event_point_id_suffix(
            record.point_id,
            old_time_order,
            new_time_order,
        )

        current_anchor = scene_anchor_new.get(record.source_scene_id)
        if current_anchor is None or new_time_order < current_anchor:
            scene_anchor_new[record.source_scene_id] = new_time_order

    default_delta = new_start - old_min
    for record in artifact.character_relations:
        scene_anchor = scene_anchor_new.get(record.source_scene_id)
        if scene_anchor is not None:
            record.document.visible_from = scene_anchor
            continue

        old_visible_from = record.document.visible_from
        record.document.visible_from = old_to_new.get(old_visible_from, old_visible_from + default_delta)

    return artifact


def _resolve_output_path(input_path: Path, output: str | None, in_place: bool) -> Path:
    if output is not None and in_place:
        raise ValueError("--output and --in-place cannot be used together")
    if in_place:
        return input_path
    if output is not None:
        return Path(output)
    return input_path.with_name(f"{input_path.stem}_reindexed{input_path.suffix}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reindex stage3 story_events.time_order and sync relation visible_from",
    )
    parser.add_argument("--input", required=True, help="Input stage3 JSON path")
    parser.add_argument("--output", default=None, help="Output JSON path")
    parser.add_argument("--in-place", action="store_true", help="Overwrite input file")
    parser.add_argument(
        "--new-start",
        type=int,
        default=3950,
        help="New starting value for story_events.time_order",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input).resolve()
    output_path = _resolve_output_path(input_path, args.output, args.in_place)

    artifact = load_stage3_normalized_import_artifact(input_path)
    old_orders = [record.document.time_order for record in artifact.story_events]

    reindex_stage3_time_orders(artifact, new_start=args.new_start)
    save_stage3_normalized_import_artifact(artifact, output_path)

    if old_orders:
        print(f"input: {input_path}")
        print(f"output: {output_path.resolve()}")
        print(f"story_events: {len(old_orders)}")
        print(f"old_start: {min(old_orders)}")
        print(f"new_start: {args.new_start}")
    else:
        print(f"No story_events found, wrote unchanged file: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())