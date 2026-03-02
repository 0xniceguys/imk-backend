#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.reverse.tasks import default_reverse_tasks  # noqa: E402


def main() -> int:
    for task in default_reverse_tasks():
        print(f"{task.task_id}: {task.title}")
        print(f"  goal: {task.goal}")
        print("  labels:")
        for label in task.labels:
            print(f"    - {label}")
        print("  notes:")
        for note in task.notes:
            print(f"    - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
