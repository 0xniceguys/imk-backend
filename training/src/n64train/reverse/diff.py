from __future__ import annotations

from dataclasses import dataclass

from n64train.reverse.scanner import AddressRange, MemoryRangeSnapshot


@dataclass(frozen=True)
class ByteDiffRun:
    offset: int
    length: int
    before_hex: str
    after_hex: str

    @property
    def end_offset(self) -> int:
        return self.offset + self.length

    def to_dict(self) -> dict[str, object]:
        return {
            "offset": self.offset,
            "offset_hex": hex(self.offset),
            "length": self.length,
            "end_offset": self.end_offset,
            "end_offset_hex": hex(self.end_offset),
            "before_hex": self.before_hex,
            "after_hex": self.after_hex,
        }


@dataclass(frozen=True)
class WordCandidate:
    offset: int
    width: int
    endian: str
    signed: bool
    before: int
    after: int
    delta: int

    def to_dict(self) -> dict[str, object]:
        return {
            "offset": self.offset,
            "offset_hex": hex(self.offset),
            "width": self.width,
            "endian": self.endian,
            "signed": self.signed,
            "before": self.before,
            "after": self.after,
            "delta": self.delta,
        }


@dataclass(frozen=True)
class DiffSummary:
    total_bytes: int
    changed_bytes: int
    changed_runs: tuple[ByteDiffRun, ...]
    word_candidates: tuple[WordCandidate, ...]

    @property
    def changed_ratio(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return self.changed_bytes / self.total_bytes

    def to_dict(self) -> dict[str, object]:
        return {
            "total_bytes": self.total_bytes,
            "changed_bytes": self.changed_bytes,
            "changed_ratio": self.changed_ratio,
            "changed_runs": [run.to_dict() for run in self.changed_runs],
            "word_candidates": [candidate.to_dict() for candidate in self.word_candidates],
        }


def _hex_slice(data: bytes, start: int, length: int, limit: int = 16) -> str:
    sl = data[start : start + min(length, limit)]
    return sl.hex()


def _iter_changed_runs(before: bytes, after: bytes) -> tuple[ByteDiffRun, ...]:
    runs: list[ByteDiffRun] = []
    i = 0
    n = len(before)
    while i < n:
        if before[i] == after[i]:
            i += 1
            continue
        start = i
        while i < n and before[i] != after[i]:
            i += 1
        length = i - start
        runs.append(
            ByteDiffRun(
                offset=start,
                length=length,
                before_hex=_hex_slice(before, start, length),
                after_hex=_hex_slice(after, start, length),
            )
        )
    return tuple(runs)


def _word_candidates(before: bytes, after: bytes, *, max_candidates: int = 50) -> tuple[WordCandidate, ...]:
    candidates: list[WordCandidate] = []
    for width in (1, 2, 4):
        if len(before) < width:
            continue
        for offset in range(0, len(before) - width + 1):
            b = before[offset : offset + width]
            a = after[offset : offset + width]
            if b == a:
                continue
            if width == 1:
                endian_modes = ("little",)
            else:
                endian_modes = ("little", "big")
            for endian in endian_modes:
                for signed in (False, True):
                    before_v = int.from_bytes(b, byteorder=endian, signed=signed)
                    after_v = int.from_bytes(a, byteorder=endian, signed=signed)
                    delta = after_v - before_v
                    # Keep small deltas and narrow values first: useful for timer/health/cursor indexes.
                    if abs(delta) <= 255 or (0 <= before_v <= 1024 and 0 <= after_v <= 1024):
                        candidates.append(
                            WordCandidate(
                                offset=offset,
                                width=width,
                                endian=endian,
                                signed=signed,
                                before=before_v,
                                after=after_v,
                                delta=delta,
                            )
                        )
    candidates.sort(key=lambda c: (abs(c.delta), c.width, c.offset))
    return tuple(candidates[:max_candidates])


def diff_bytes(before: bytes, after: bytes) -> DiffSummary:
    if len(before) != len(after):
        raise ValueError(f"Byte lengths differ: {len(before)} vs {len(after)}")
    changed = sum(1 for b, a in zip(before, after) if b != a)
    runs = _iter_changed_runs(before, after)
    words = _word_candidates(before, after)
    return DiffSummary(total_bytes=len(before), changed_bytes=changed, changed_runs=runs, word_candidates=words)


def diff_snapshots(before: MemoryRangeSnapshot, after: MemoryRangeSnapshot) -> DiffSummary:
    if before.addr_range != after.addr_range:
        raise ValueError(
            "Snapshots use different address ranges: "
            f"{before.addr_range.to_dict()} vs {after.addr_range.to_dict()}"
        )
    return diff_bytes(before.payload, after.payload)


def absolute_offset_to_address(addr_range: AddressRange, offset: int) -> int:
    if offset < 0 or offset >= addr_range.size:
        raise ValueError("offset out of range")
    return addr_range.start + offset

