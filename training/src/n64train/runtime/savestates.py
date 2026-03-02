from __future__ import annotations

from pathlib import Path

from n64train.paths import PATHS


def savestate_dir() -> Path:
    PATHS.savestate_dir.mkdir(parents=True, exist_ok=True)
    return PATHS.savestate_dir


def list_savestates() -> list[Path]:
    root = savestate_dir()
    files = [p for p in root.iterdir() if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def newest_savestate() -> Path | None:
    states = list_savestates()
    return states[0] if states else None
