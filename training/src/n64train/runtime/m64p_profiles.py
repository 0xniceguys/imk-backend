from __future__ import annotations

from configparser import RawConfigParser
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class M64PProfileSpec:
    name: str
    description: str
    core_events_overrides: dict[str, str]
    control1_overrides: dict[str, str]


def _reverse_human_profile() -> M64PProfileSpec:
    # Purpose: make reverse-engineering / manual labeling safer.
    # We harden emulator hotkeys to avoid accidental quit/reset/savestate corruption.
    return M64PProfileSpec(
        name="reverse_human",
        description="Safe reverse-engineering human profile (hotkey-hardened, controller map unchanged)",
        core_events_overrides={
            "Kbd Mapping Stop": "282",  # F1 (move quit off Esc)
            "Kbd Mapping Fullscreen": "0",
            "Kbd Mapping Save State": "0",
            "Kbd Mapping Load State": "0",
            "Kbd Mapping Increment Slot": "0",
            "Kbd Mapping Reset": "0",
            "Kbd Mapping Gameshark": "0",
            # Disable direct slot switching to avoid accidental save/load corruption.
            "Kbd Mapping Slot 0": "0",
            "Kbd Mapping Slot 1": "0",
            "Kbd Mapping Slot 2": "0",
            "Kbd Mapping Slot 3": "0",
            "Kbd Mapping Slot 4": "0",
            "Kbd Mapping Slot 5": "0",
            "Kbd Mapping Slot 6": "0",
            "Kbd Mapping Slot 7": "0",
            "Kbd Mapping Slot 8": "0",
            "Kbd Mapping Slot 9": "0",
        },
        # Leave the player-1 keyboard controller map unchanged for stability.
        control1_overrides={},
    )


M64P_PROFILES: dict[str, M64PProfileSpec] = {
    "reverse_human": _reverse_human_profile(),
}


def profile_names() -> list[str]:
    return sorted(M64P_PROFILES.keys())


def get_profile(name: str) -> M64PProfileSpec:
    try:
        return M64P_PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown Mupen64Plus profile: {name}") from exc


def _new_parser() -> RawConfigParser:
    parser = RawConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    return parser


def load_m64p_config(path: Path) -> RawConfigParser:
    parser = _new_parser()
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        parser.read_file(fh)
    return parser


def write_m64p_config(path: Path, parser: RawConfigParser) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        parser.write(fh)


def apply_profile_to_parser(parser: RawConfigParser, profile: M64PProfileSpec) -> None:
    if not parser.has_section("CoreEvents"):
        parser.add_section("CoreEvents")
    if not parser.has_section("Input-SDL-Control1"):
        parser.add_section("Input-SDL-Control1")

    for key, value in profile.core_events_overrides.items():
        parser.set("CoreEvents", key, str(value))
    for key, value in profile.control1_overrides.items():
        parser.set("Input-SDL-Control1", key, str(value))


def apply_profile_to_file(*, base_cfg: Path, out_cfg: Path, profile_name: str) -> dict[str, Any]:
    parser = load_m64p_config(base_cfg)
    profile = get_profile(profile_name)
    apply_profile_to_parser(parser, profile)
    write_m64p_config(out_cfg, parser)
    return verify_profile_file(out_cfg, profile_name=profile_name)


def verify_profile_parser(parser: RawConfigParser, profile: M64PProfileSpec) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    actual: dict[str, dict[str, str | None]] = {"CoreEvents": {}, "Input-SDL-Control1": {}}

    def _check(section: str, expected_items: dict[str, str]) -> None:
        if not expected_items:
            return
        for key, expected in expected_items.items():
            actual_value = parser.get(section, key, fallback=None)
            actual[section][key] = actual_value
            if actual_value != expected:
                mismatches.append(
                    {
                        "section": section,
                        "key": key,
                        "expected": expected,
                        "actual": actual_value,
                    }
                )

    _check("CoreEvents", profile.core_events_overrides)
    _check("Input-SDL-Control1", profile.control1_overrides)

    return {
        "profile": profile.name,
        "description": profile.description,
        "ok": not mismatches,
        "mismatches": mismatches,
        "checked": actual,
    }


def verify_profile_file(path: Path, *, profile_name: str) -> dict[str, Any]:
    parser = load_m64p_config(path)
    report = verify_profile_parser(parser, get_profile(profile_name))
    report["config_path"] = str(path)
    return report

