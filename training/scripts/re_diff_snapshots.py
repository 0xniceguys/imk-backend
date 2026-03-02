#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.reverse.diff import absolute_offset_to_address, diff_snapshots  # noqa: E402
from n64train.reverse.scanner import MemoryRangeSnapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Diff two reverse-engineering RAM snapshots")
    parser.add_argument("before_manifest")
    parser.add_argument("after_manifest")
    parser.add_argument("--max-runs", type=int, default=25)
    parser.add_argument("--max-candidates", type=int, default=25)
    parser.add_argument(
        "--delta",
        action="append",
        type=int,
        default=[],
        help="Filter word candidates to exact delta value(s); can be repeated (e.g. --delta 1 --delta -1)",
    )
    parser.add_argument(
        "--width",
        action="append",
        type=int,
        default=[],
        help="Filter word candidates to width(s) in bytes (1,2,4); can be repeated",
    )
    parser.add_argument(
        "--signed",
        choices=["any", "true", "false"],
        default="any",
        help="Filter word candidates by signedness interpretation",
    )
    parser.add_argument(
        "--endian",
        choices=["any", "little", "big"],
        default="any",
        help="Filter word candidates by endian interpretation",
    )
    args = parser.parse_args()

    before = MemoryRangeSnapshot.load(Path(args.before_manifest))
    after = MemoryRangeSnapshot.load(Path(args.after_manifest))
    summary = diff_snapshots(before, after)

    data = {
        "before": {
            "label": before.label,
            "captured_at_utc": before.captured_at_utc,
            "range": before.addr_range.to_dict(),
            "sha256": before.sha256,
        },
        "after": {
            "label": after.label,
            "captured_at_utc": after.captured_at_utc,
            "range": after.addr_range.to_dict(),
            "sha256": after.sha256,
        },
        "summary": {
            "total_bytes": summary.total_bytes,
            "changed_bytes": summary.changed_bytes,
            "changed_ratio": summary.changed_ratio,
        },
        "changed_runs": [],
        "word_candidates": [],
    }
    for run in summary.changed_runs[: args.max_runs]:
        row = run.to_dict()
        row["address_hex"] = hex(absolute_offset_to_address(before.addr_range, run.offset))
        data["changed_runs"].append(row)
    filtered_candidates = list(summary.word_candidates)
    if args.delta:
        allowed_deltas = set(args.delta)
        filtered_candidates = [cand for cand in filtered_candidates if cand.delta in allowed_deltas]
    if args.width:
        allowed_widths = set(args.width)
        filtered_candidates = [cand for cand in filtered_candidates if cand.width in allowed_widths]
    if args.signed != "any":
        signed_flag = args.signed == "true"
        filtered_candidates = [cand for cand in filtered_candidates if cand.signed is signed_flag]
    if args.endian != "any":
        filtered_candidates = [cand for cand in filtered_candidates if cand.endian == args.endian]

    data["summary"]["word_candidates_total"] = len(summary.word_candidates)
    data["summary"]["word_candidates_filtered"] = len(filtered_candidates)

    for cand in filtered_candidates[: args.max_candidates]:
        row = cand.to_dict()
        row["address_hex"] = hex(absolute_offset_to_address(before.addr_range, cand.offset))
        data["word_candidates"].append(row)
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
