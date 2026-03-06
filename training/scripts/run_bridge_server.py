#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.runtime.bridge_server import BridgeServer  # noqa: E402
from n64train.runtime.local_bridge_backend import LocalBridgeBackend, LocalBridgeBackendConfig  # noqa: E402
from n64train.runtime.memory import DebuggerDumpMemoryReader, DebuggerDumpMemoryReaderConfig  # noqa: E402
from n64train.runtime.types import SpeedMode  # noqa: E402
from n64train.paths import PATHS  # noqa: E402


def main() -> int:
    default_rom = str(PATHS.roms[0]) if PATHS.roms else ""
    parser = argparse.ArgumentParser(description="Run the local MK4 Unix-socket bridge server")
    parser.add_argument(
        "--socket-path",
        default="/Users/ichiropractic/code/n64/training/data/bridge/mk4.sock",
        help="Unix socket path for the bridge server",
    )
    parser.add_argument("--instance-id", default="bridge-main")
    parser.add_argument("--resolution", default="320x240")
    parser.add_argument(
        "--speed-mode",
        default=SpeedMode.DEBUG_VISIBLE.value,
        choices=[mode.value for mode in SpeedMode],
    )
    parser.add_argument("--launch-emulator", action="store_true", help="Launch Mupen64Plus when configured")
    parser.add_argument("--log-path", default="", help="Optional emulator log file path")
    parser.add_argument(
        "--memory-reader",
        default="zero",
        choices=["zero", "debugger-dump"],
        help="RAM export backend for GET_RAM_FEATURES",
    )
    parser.add_argument(
        "--debugger-ui-binary",
        default=str(PATHS.repo_root / "vendor" / "mupen64plus-ui-console" / "projects" / "unix" / "mupen64plus"),
    )
    parser.add_argument(
        "--debugger-corelib",
        default=str(PATHS.repo_root / "vendor" / "mupen64plus-core" / "projects" / "unix" / "libmupen64plus.dylib"),
    )
    parser.add_argument("--rom-path", default=default_rom, help="ROM path for debugger-backed RAM reader")
    parser.add_argument("--debugger-plugindir", default="/opt/homebrew/lib/mupen64plus")
    parser.add_argument("--debugger-configdir", default=str(PATHS.local_m64p_root / "config"))
    parser.add_argument("--debugger-datadir", default=str(PATHS.local_m64p_root / "data"))
    parser.add_argument("--debugger-dump-dir", default=str(PATHS.training_data_root / "bridge" / "debugger_dumps"))
    parser.add_argument("--debugger-gfx-plugin", default="dummy")
    parser.add_argument("--debugger-audio-plugin", default="dummy")
    parser.add_argument("--debugger-input-plugin", default="dummy")
    parser.add_argument("--debugger-rsp-plugin", default="dummy")
    parser.add_argument(
        "--debugger-emumode",
        type=int,
        default=0,
        choices=[0, 1, 2],
        help="Mupen emulation mode for debugger-backed RAM reader (0=Pure Interpreter, 1=Interpreter, 2=DynaRec)",
    )
    args = parser.parse_args()

    memory_reader = None
    if args.memory_reader == "debugger-dump":
        if not args.rom_path:
            parser.error("--rom-path is required when --memory-reader=debugger-dump")
        memory_reader = DebuggerDumpMemoryReader(
            DebuggerDumpMemoryReaderConfig(
                ui_binary=Path(args.debugger_ui_binary),
                corelib=Path(args.debugger_corelib),
                rom_path=Path(args.rom_path),
                plugindir=Path(args.debugger_plugindir) if args.debugger_plugindir else None,
                configdir=Path(args.debugger_configdir) if args.debugger_configdir else None,
                datadir=Path(args.debugger_datadir) if args.debugger_datadir else None,
                workdir=PATHS.repo_root,
                dump_dir=Path(args.debugger_dump_dir) if args.debugger_dump_dir else None,
                gfx_plugin=args.debugger_gfx_plugin,
                audio_plugin=args.debugger_audio_plugin,
                input_plugin=args.debugger_input_plugin,
                rsp_plugin=args.debugger_rsp_plugin,
                # Respect requested bridge speed mode. Previously this was always
                # True, which made DEBUG_VISIBLE behave like turbo.
                nospeedlimit=(SpeedMode(args.speed_mode) is SpeedMode.TRAIN_TURBO),
                emumode=args.debugger_emumode,
            )
        )

    backend = LocalBridgeBackend(
        LocalBridgeBackendConfig(
            instance_id=args.instance_id,
            launch_emulator=args.launch_emulator,
            resolution=args.resolution,
            speed_mode=SpeedMode(args.speed_mode),
            log_path=Path(args.log_path) if args.log_path else None,
        ),
        memory_reader=memory_reader,
    )
    server = BridgeServer(args.socket_path, backend)
    print(f"Bridge server listening on {args.socket_path}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
