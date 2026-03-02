from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from n64train.paths import PATHS
from n64train.runtime.bridge import SocketEmulatorBridge
from n64train.runtime.memory import MemoryProbe


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sanitize_label(label: str) -> str:
    safe = []
    for ch in label:
        if ch.isalnum() or ch in ("-", "_"):
            safe.append(ch)
        else:
            safe.append("_")
    return "".join(safe).strip("_") or "capture"


@dataclass(frozen=True)
class AddressRange:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < 0:
            raise ValueError("AddressRange bounds must be >= 0")
        if self.end <= self.start:
            raise ValueError("AddressRange end must be > start")

    @property
    def size(self) -> int:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "start_hex": hex(self.start),
            "end_hex": hex(self.end),
            "size": self.size,
        }


def chunked_memory_probes(
    addr_range: AddressRange,
    *,
    chunk_size: int,
    prefix: str = "chunk",
) -> list[MemoryProbe]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    probes: list[MemoryProbe] = []
    cursor = addr_range.start
    index = 0
    while cursor < addr_range.end:
        size = min(chunk_size, addr_range.end - cursor)
        probes.append(MemoryProbe(name=f"{prefix}_{index:05d}", address=cursor, size=size))
        cursor += size
        index += 1
    return probes


@dataclass(frozen=True)
class MemoryRangeSnapshot:
    label: str
    addr_range: AddressRange
    chunk_size: int
    payload: bytes
    captured_at_utc: str
    bridge_status: dict[str, Any]
    bridge_hello: dict[str, Any] | None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    def to_manifest(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "captured_at_utc": self.captured_at_utc,
            "addr_range": self.addr_range.to_dict(),
            "chunk_size": self.chunk_size,
            "byte_len": len(self.payload),
            "sha256": self.sha256,
            "bridge_status": self.bridge_status,
            "bridge_hello": self.bridge_hello,
            "metadata": self.metadata,
            "schema_version": "reverse-snapshot-v1",
        }

    def save(self, out_dir: Path) -> tuple[Path, Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.captured_at_utc.replace(":", "").replace("-", "").replace(".", "").replace("T", "_").replace("Z", "")
        base = f"{stamp}_{_sanitize_label(self.label)}"
        bin_path = out_dir / f"{base}.bin"
        json_path = out_dir / f"{base}.json"
        bin_path.write_bytes(self.payload)
        manifest = self.to_manifest()
        manifest["payload_file"] = bin_path.name
        json_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return json_path, bin_path

    @classmethod
    def load(cls, manifest_path: Path) -> "MemoryRangeSnapshot":
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        bin_name = payload.get("payload_file")
        if not bin_name:
            raise ValueError("Snapshot manifest missing payload_file")
        bin_path = manifest_path.parent / str(bin_name)
        raw = bin_path.read_bytes()
        range_payload = dict(payload["addr_range"])
        return cls(
            label=str(payload["label"]),
            addr_range=AddressRange(start=int(range_payload["start"]), end=int(range_payload["end"])),
            chunk_size=int(payload["chunk_size"]),
            payload=raw,
            captured_at_utc=str(payload["captured_at_utc"]),
            bridge_status=dict(payload.get("bridge_status", {})),
            bridge_hello=dict(payload.get("bridge_hello", {})) if payload.get("bridge_hello") else None,
            metadata=dict(payload.get("metadata", {})),
        )


class BridgeMemoryScanner:
    def __init__(self, bridge: SocketEmulatorBridge) -> None:
        self.bridge = bridge

    def capture_range(
        self,
        *,
        label: str,
        addr_range: AddressRange,
        chunk_size: int = 0x1000,
        metadata: dict[str, Any] | None = None,
        hello_once: bool = True,
    ) -> MemoryRangeSnapshot:
        probes = chunked_memory_probes(addr_range, chunk_size=chunk_size, prefix="mem")
        bridge_hello = None
        if hello_once:
            hello_resp = self.bridge.hello()
            bridge_hello = {"payload": hello_resp.payload, "status": hello_resp.status.to_payload()}
        response = self.bridge.get_ram_features(probes)
        probe_bytes_b64 = dict(response.get("probe_bytes_b64", {}))
        chunks: list[bytes] = []
        for probe in probes:
            chunk_b64 = probe_bytes_b64.get(probe.name)
            if chunk_b64 is None:
                raise ValueError(f"Bridge response missing probe bytes for {probe.name}")
            import base64

            chunks.append(base64.b64decode(chunk_b64))
        payload_bytes = b"".join(chunks)
        if len(payload_bytes) != addr_range.size:
            raise ValueError(
                f"Captured payload length mismatch: expected {addr_range.size}, got {len(payload_bytes)}"
            )
        bridge_status = {
            "memory_reader": response.get("memory_reader"),
            "placeholder_ram_export": response.get("placeholder_ram_export"),
            "traced_state": response.get("traced_state"),
        }
        meta = dict(metadata or {})
        meta.setdefault("capture_mode", "bridge_get_ram_features")
        meta.setdefault("probe_count", len(probes))
        return MemoryRangeSnapshot(
            label=label,
            addr_range=addr_range,
            chunk_size=chunk_size,
            payload=payload_bytes,
            captured_at_utc=_utc_now_iso(),
            bridge_status=bridge_status,
            bridge_hello=bridge_hello,
            metadata=meta,
        )


def reverse_capture_dir() -> Path:
    root = PATHS.training_data_root / "reverse"
    root.mkdir(parents=True, exist_ok=True)
    return root

