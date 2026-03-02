from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.reverse.scanner import AddressRange, MemoryRangeSnapshot, chunked_memory_probes  # noqa: E402


class ReverseScannerTests(unittest.TestCase):
    def test_chunked_probes_cover_range(self) -> None:
        addr_range = AddressRange(0x1000, 0x1801)
        probes = chunked_memory_probes(addr_range, chunk_size=0x100, prefix="p")
        self.assertGreater(len(probes), 0)
        self.assertEqual(probes[0].address, 0x1000)
        self.assertEqual(sum(probe.size for probe in probes), addr_range.size)
        self.assertEqual(probes[-1].address + probes[-1].size, addr_range.end)

    def test_snapshot_save_load_roundtrip(self) -> None:
        snap = MemoryRangeSnapshot(
            label="test",
            addr_range=AddressRange(0, 8),
            chunk_size=4,
            payload=b"\x01\x02\x03\x04\x05\x06\x07\x08",
            captured_at_utc="2026-02-26T00:00:00.000000Z",
            bridge_status={"placeholder_ram_export": True},
            bridge_hello={"payload": {}, "status": {}},
            metadata={"task_id": "difficulty_setting"},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path, _ = snap.save(Path(tmpdir))
            loaded = MemoryRangeSnapshot.load(manifest_path)
            self.assertEqual(loaded.label, snap.label)
            self.assertEqual(loaded.addr_range, snap.addr_range)
            self.assertEqual(loaded.payload, snap.payload)


if __name__ == "__main__":
    unittest.main()
