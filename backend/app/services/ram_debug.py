from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _default_log_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "logs" / "ram_debug"


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return repr(value)


@dataclass
class RamDebugRecorder:
    match_id: str
    instance_id: str
    log_dir: Path | None = None
    enabled: bool = True
    file_path: Path = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        base_dir = self.log_dir or _default_log_dir()
        base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        filename = f"{timestamp}__{_slug(self.match_id)}__{_slug(self.instance_id)}.jsonl"
        self.file_path = base_dir / filename

    def record(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        record = dict(payload)
        record.setdefault("ts", time.time())
        record.setdefault("match_id", self.match_id)
        record.setdefault("instance_id", self.instance_id)
        line = json.dumps(record, ensure_ascii=True, sort_keys=True, default=_json_default)
        with self._lock:
            with self.file_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")

    def record_event(self, event: str, **fields: Any) -> None:
        self.record({
            "kind": "event",
            "event": event,
            **fields,
        })
