#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(script_dir.parent / "src"))


_add_src_to_path()

from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper  # noqa: E402
from n64train.reverse.mk4_findings import (  # noqa: E402
    mk4_symbol_registry,
    normalize_options_difficulty,
    options_difficulty_label,
)
from n64train.runtime.bridge import SocketEmulatorBridge  # noqa: E402


DEFAULT_SOCKET = "/Users/ichiropractic/code/n64/training/data/bridge/mk4-visible.sock"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MK4 reverse/debug helper commands (persistent known-symbol tools)"
    )
    parser.add_argument("--socket-path", default=DEFAULT_SOCKET)
    parser.add_argument(
        "--debugger-timeout-sec",
        type=float,
        default=5.0,
        help="Bridge-side debugger command timeout",
    )
    parser.add_argument(
        "--output-tail-chars",
        type=int,
        default=4000,
        help="Limit debugger output returned by the bridge",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_symbols = subparsers.add_parser("symbols", help="List known MK4 reverse-engineered symbols")
    p_symbols.set_defaults(func=_cmd_symbols)

    p_diff = subparsers.add_parser("difficulty", help="Get/set Options -> Difficulty via bridge debugger")
    diff_sub = p_diff.add_subparsers(dest="difficulty_cmd", required=True)

    p_diff_get = diff_sub.add_parser("get", help="Read the current Options -> Difficulty value")
    p_diff_get.add_argument("--pause", action="store_true", help="Pause before reading")
    p_diff_get.add_argument("--resume", action="store_true", help="Resume after reading")
    p_diff_get.set_defaults(func=_cmd_difficulty_get)

    p_diff_set = diff_sub.add_parser("set", help="Set Options -> Difficulty (use while on options screen)")
    p_diff_set.add_argument(
        "level",
        help="Difficulty label or value (e.g. ultimate, very_hard, 5)",
    )
    p_diff_set.add_argument(
        "--no-pause",
        dest="pause",
        action="store_false",
        help="Do not pause before writing (default: pause)",
    )
    p_diff_set.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Leave emulator paused after write/verify (default: resume)",
    )
    p_diff_set.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="Skip read-back verification",
    )
    p_diff_set.set_defaults(func=_cmd_difficulty_set, pause=True, resume=True, verify=True)

    p_menu = subparsers.add_parser("menu", help="Read known menu-related state via bridge debugger")
    menu_sub = p_menu.add_subparsers(dest="menu_cmd", required=True)

    p_menu_cursor = menu_sub.add_parser(
        "top-cursor",
        help="Read top-level main-menu cursor index (Arcade..Options) when on the top-level menu screen",
    )
    p_menu_cursor.add_argument(
        "action",
        nargs="?",
        choices=["get"],
        default="get",
        help="Only 'get' is supported currently",
    )
    p_menu_cursor.add_argument("--pause", action="store_true", help="Pause before reading")
    p_menu_cursor.add_argument("--resume", action="store_true", help="Resume after reading")
    p_menu_cursor.set_defaults(func=_cmd_menu_top_cursor_get)

    p_menu_arcade_pc_cursor = menu_sub.add_parser(
        "arcade-player-count-cursor",
        help="Read Arcade 1P/2P screen cursor (1 Player / 2 Player)",
    )
    p_menu_arcade_pc_cursor.add_argument(
        "action",
        nargs="?",
        choices=["get"],
        default="get",
        help="Only 'get' is supported currently",
    )
    p_menu_arcade_pc_cursor.add_argument("--pause", action="store_true", help="Pause before reading")
    p_menu_arcade_pc_cursor.add_argument("--resume", action="store_true", help="Resume after reading")
    p_menu_arcade_pc_cursor.set_defaults(func=_cmd_menu_arcade_player_count_cursor_get)

    p_menu_screen = menu_sub.add_parser(
        "screen-state",
        help="Read menu/screen state ID (e.g. top-level main menu vs options screen)",
    )
    p_menu_screen.add_argument(
        "action",
        nargs="?",
        choices=["get"],
        default="get",
        help="Only 'get' is supported currently",
    )
    p_menu_screen.add_argument("--pause", action="store_true", help="Pause before reading")
    p_menu_screen.add_argument("--resume", action="store_true", help="Resume after reading")
    p_menu_screen.set_defaults(func=_cmd_menu_screen_state_get)

    return parser


def _open_helper(args: argparse.Namespace) -> tuple[SocketEmulatorBridge, Mk4BridgeHelper]:
    bridge = SocketEmulatorBridge(args.socket_path, timeout_sec=max(args.debugger_timeout_sec + 5.0, 10.0))
    helper = Mk4BridgeHelper(
        bridge=bridge,
        debugger_timeout_sec=args.debugger_timeout_sec,
        output_tail_chars=args.output_tail_chars,
    )
    return bridge, helper


def _cmd_symbols(args: argparse.Namespace) -> int:
    _ = args
    registry = mk4_symbol_registry()
    print(
        json.dumps(
            {
                "symbols": {key: symbol.to_dict() for key, symbol in sorted(registry.items())},
                "notes": [
                    "Use difficulty set/get while the game is on Options -> Difficulty for reliable UI-visible updates."
                ],
            },
            indent=2,
        )
    )
    return 0


def _cmd_difficulty_get(args: argparse.Namespace) -> int:
    bridge, helper = _open_helper(args)
    paused = False
    try:
        if args.pause:
            helper.pause()
            paused = True
        payload = helper.get_options_difficulty()
        print(json.dumps(payload, indent=2))
        if args.resume and paused:
            helper.run()
    finally:
        bridge.close()
    return 0


def _cmd_difficulty_set(args: argparse.Namespace) -> int:
    bridge, helper = _open_helper(args)
    paused = False
    try:
        target = normalize_options_difficulty(args.level)
        if args.pause:
            helper.pause()
            paused = True
        payload = helper.set_options_difficulty(target, verify=args.verify)
        payload["usage_note"] = (
            "Write can be overwritten by the game if done too early; use this while on the Options->Difficulty row."
        )
        payload["requested"]["normalized_input"] = {
            "value": int(target),
            "label": options_difficulty_label(int(target)),
        }
        print(json.dumps(payload, indent=2))
        if args.resume and paused:
            helper.run()
    finally:
        bridge.close()
    return 0


def _cmd_menu_top_cursor_get(args: argparse.Namespace) -> int:
    bridge, helper = _open_helper(args)
    paused = False
    try:
        if args.pause:
            helper.pause()
            paused = True
        payload = helper.get_main_menu_top_level_cursor()
        print(json.dumps(payload, indent=2))
        if args.resume and paused:
            helper.run()
    finally:
        bridge.close()
    return 0


def _cmd_menu_arcade_player_count_cursor_get(args: argparse.Namespace) -> int:
    bridge, helper = _open_helper(args)
    paused = False
    try:
        if args.pause:
            helper.pause()
            paused = True
        payload = helper.get_arcade_player_count_cursor()
        print(json.dumps(payload, indent=2))
        if args.resume and paused:
            helper.run()
    finally:
        bridge.close()
    return 0


def _cmd_menu_screen_state_get(args: argparse.Namespace) -> int:
    bridge, helper = _open_helper(args)
    paused = False
    try:
        if args.pause:
            helper.pause()
            paused = True
        payload = helper.get_menu_screen_state()
        print(json.dumps(payload, indent=2))
        if args.resume and paused:
            helper.run()
    finally:
        bridge.close()
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
