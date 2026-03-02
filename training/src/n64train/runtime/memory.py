from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from n64train.runtime.debugger_cli import DebuggerCliConfig, DebuggerCliSession


@dataclass(frozen=True)
class MemoryProbe:
    name: str
    address: int
    size: int


class MemoryReader:
    """
    Placeholder for future RAM access.

    This will eventually be implemented via one of:
    - Mupen64Plus core/debugger APIs
    - a custom plugin / patched core
    - emulator memory export hooks
    """

    def read(self, probe: MemoryProbe) -> bytes:
        raise NotImplementedError("RAM access is not wired yet")


class DebuggerCommandCapable(Protocol):
    def debugger_command(self, command: str, *, timeout_s: float | None = None) -> str: ...
    def debugger_is_alive(self) -> bool: ...


class ZeroMemoryReader(MemoryReader):
    """
    Test/dry-run memory reader used before emulator memory export is available.

    This keeps the bridge plumbing real while making the unsupported state explicit.
    """

    def read(self, probe: MemoryProbe) -> bytes:
        if probe.size < 0:
            raise ValueError("probe.size must be >= 0")
        return b"\x00" * probe.size

    def read_many(self, probes: Iterable[MemoryProbe]) -> dict[str, bytes]:
        return {probe.name: self.read(probe) for probe in probes}


@dataclass(frozen=True)
class DebuggerDumpMemoryReaderConfig:
    ui_binary: Path
    corelib: Path
    rom_path: Path
    plugindir: Path | None = None
    configdir: Path | None = None
    datadir: Path | None = None
    workdir: Path | None = None
    dump_dir: Path | None = None
    gfx_plugin: str = "dummy"
    audio_plugin: str = "dummy"
    input_plugin: str = "dummy"
    rsp_plugin: str = "dummy"
    nospeedlimit: bool = True
    emumode: int = 1


class DebuggerDumpMemoryReader(MemoryReader):
    """
    RAM reader backed by patched `mupen64plus-ui-console --debug` `dumpmem`.

    This gives the reverse-engineering tools real memory bytes before the
    custom in-process bridge is patched into Mupen64Plus.
    """

    def __init__(self, config: DebuggerDumpMemoryReaderConfig) -> None:
        self.config = config
        self._session = DebuggerCliSession(
            DebuggerCliConfig(
                ui_binary=config.ui_binary,
                corelib=config.corelib,
                rom_path=config.rom_path,
                plugindir=config.plugindir,
                configdir=config.configdir,
                datadir=config.datadir,
                workdir=config.workdir,
                gfx_plugin=config.gfx_plugin,
                audio_plugin=config.audio_plugin,
                input_plugin=config.input_plugin,
                rsp_plugin=config.rsp_plugin,
                nospeedlimit=config.nospeedlimit,
                emumode=config.emumode,
                dump_dir=config.dump_dir,
            )
        )

    def read(self, probe: MemoryProbe) -> bytes:
        if probe.size < 0:
            raise ValueError("probe.size must be >= 0")
        return self._session.dump_memory(probe.address, probe.size)

    def read_many(self, probes: Iterable[MemoryProbe]) -> dict[str, bytes]:
        return {probe.name: self.read(probe) for probe in probes}

    def close(self) -> None:
        self._session.close()

    def debugger_command(self, command: str, *, timeout_s: float | None = None) -> str:
        return self._session.command(command, timeout_s=timeout_s)

    def debugger_is_alive(self) -> bool:
        return self._session.is_alive()
