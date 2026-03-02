from __future__ import annotations

import os
import pty
import selectors
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class DebuggerCliError(RuntimeError):
    pass


@dataclass(frozen=True)
class DebuggerCliConfig:
    ui_binary: Path
    corelib: Path
    rom_path: Path
    plugindir: Path | None = None
    configdir: Path | None = None
    datadir: Path | None = None
    workdir: Path | None = None
    gfx_plugin: str = "dummy"
    audio_plugin: str = "dummy"
    input_plugin: str = "dummy"
    rsp_plugin: str = "dummy"
    nospeedlimit: bool = True
    emumode: int = 1
    startup_timeout_s: float = 30.0
    command_timeout_s: float = 10.0
    dump_timeout_s: float = 15.0
    prompt: bytes = b"(dbg) "
    dump_dir: Path | None = None


class DebuggerCliSession:
    """
    Thin adapter over patched `mupen64plus-ui-console --debug`.

    This is intentionally line- and prompt-driven so the Python bridge can
    start using real RAM dumps immediately, before a custom in-core socket
    bridge exists.
    """

    def __init__(self, config: DebuggerCliConfig) -> None:
        self.config = config
        self._proc: subprocess.Popen[bytes] | None = None
        self._selector: selectors.BaseSelector | None = None
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._master_fd: int | None = None

    def start(self) -> None:
        if self.is_alive():
            return
        cmd = [str(self.config.ui_binary)]
        cmd += ["--debug", "--corelib", str(self.config.corelib)]
        if self.config.plugindir is not None:
            cmd += ["--plugindir", str(self.config.plugindir)]
        if self.config.configdir is not None:
            cmd += ["--configdir", str(self.config.configdir)]
        if self.config.datadir is not None:
            cmd += ["--datadir", str(self.config.datadir)]
        cmd += ["--gfx", self.config.gfx_plugin]
        cmd += ["--audio", self.config.audio_plugin]
        cmd += ["--input", self.config.input_plugin]
        cmd += ["--rsp", self.config.rsp_plugin]
        if self.config.nospeedlimit:
            cmd += ["--nospeedlimit"]
        cmd += ["--emumode", str(self.config.emumode)]
        cmd += [str(self.config.rom_path)]

        master_fd, slave_fd = pty.openpty()
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(self.config.workdir or self.config.rom_path.parent),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                bufsize=0,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)
        self._master_fd = master_fd
        self._selector = selectors.DefaultSelector()
        self._selector.register(master_fd, selectors.EVENT_READ)
        self._read_until_prompt(timeout_s=self.config.startup_timeout_s)

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def command(self, command: str, *, timeout_s: float | None = None) -> str:
        stripped = command.strip().lower()
        with self._lock:
            self._ensure_started()
            if self._master_fd is None:
                raise DebuggerCliError("Debugger PTY is not initialized")
            os.write(self._master_fd, (command.rstrip("\n") + "\n").encode("utf-8", errors="replace"))
            if stripped == "run":
                # 'run' starts the game — the (dbg) prompt won't return while running.
                # Fire-and-forget: drain a tiny bit then return so caller doesn't block.
                time.sleep(0.1)
                try:
                    self._read_some(timeout_s=0.15)
                except Exception:
                    pass
                return "run_sent"
            result = self._read_until_prompt(timeout_s=timeout_s or self.config.command_timeout_s)
            if stripped == "pause":
                # After 'pause' the PTY may emit extra status lines or a second (dbg).
                # Drain any pending output (200ms window) to ensure a clean buffer
                # before the next command (e.g. stateload) is sent.
                time.sleep(0.1)
                drain_deadline = time.monotonic() + 0.2
                while time.monotonic() < drain_deadline:
                    try:
                        chunk = self._read_some(timeout_s=max(0.01, drain_deadline - time.monotonic()))
                        if not chunk:
                            break
                    except Exception:
                        break
                # Also clear internal buffer so stateload doesn't see old (dbg)
                self._buffer.clear()
            return result

    def dump_memory(self, address: int, size: int) -> bytes:
        if size < 0:
            raise ValueError("size must be >= 0")
        if size == 0:
            return b""
        if address < 0:
            raise ValueError("address must be >= 0")

        dump_dir = self.config.dump_dir or (self.config.workdir or self.config.rom_path.parent)
        dump_dir.mkdir(parents=True, exist_ok=True)

        # `dumpmem` takes a virtual address and dumps from RDRAM after translation.
        virt_addr = address if address >= 0x8000_0000 else (0x8000_0000 + address)
        with tempfile.NamedTemporaryFile(
            prefix="m64p_dump_",
            suffix=".bin",
            dir=str(dump_dir),
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            out = self.command(
                f"dumpmem {virt_addr:08x} 0x{size:x} {tmp_path}",
                timeout_s=self.config.dump_timeout_s,
            )
            if "M64P_DUMPMEM_OK" not in out:
                raise DebuggerCliError(f"dumpmem failed or unrecognized output: {out.strip()[-500:]}")
            raw = tmp_path.read_bytes()
            if len(raw) != size:
                raise DebuggerCliError(f"dumpmem length mismatch: expected {size}, got {len(raw)}")
            return raw
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def run(self) -> str:
        return self.command("run")

    def pause(self) -> str:
        return self.command("pause")

    def frame(self, count: int = 1) -> str:
        return self.command(f"frame {max(1, int(count))}")

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            if proc is None:
                return
            try:
                if proc.poll() is None:
                    if self._master_fd is not None:
                        try:
                            os.write(self._master_fd, b"quit\n")
                        except OSError:
                            pass
                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline and proc.poll() is None:
                        # Drain a little output while waiting.
                        try:
                            self._read_some(timeout_s=0.05)
                        except Exception:
                            break
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=2.0)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=2.0)
            finally:
                if self._selector is not None:
                    self._selector.close()
                self._selector = None
                if self._master_fd is not None:
                    try:
                        os.close(self._master_fd)
                    except OSError:
                        pass
                    self._master_fd = None
                self._proc = None
                self._buffer.clear()

    def _ensure_started(self) -> None:
        if not self.is_alive():
            self.start()

    def _read_some(self, *, timeout_s: float) -> bytes:
        proc = self._proc
        sel = self._selector
        if proc is None or sel is None or self._master_fd is None:
            raise DebuggerCliError("Debugger process is not running")
        events = sel.select(timeout_s)
        if not events:
            return b""
        try:
            chunk = os.read(self._master_fd, 4096)
        except OSError as exc:
            if proc.poll() is not None:
                raise DebuggerCliError(f"Debugger process exited with code {proc.returncode}") from exc
            raise
        if not chunk:
            if proc.poll() is None:
                return b""
            raise DebuggerCliError(f"Debugger process exited with code {proc.returncode}")
        self._buffer.extend(chunk)
        return chunk

    def _read_until_prompt(self, *, timeout_s: float) -> str:
        deadline = time.monotonic() + timeout_s
        prompt = self.config.prompt
        while True:
            idx = self._buffer.find(prompt)
            if idx >= 0:
                end = idx + len(prompt)
                payload = bytes(self._buffer[:end])
                del self._buffer[:end]
                return payload.decode("utf-8", errors="replace")
            if time.monotonic() >= deadline:
                tail = bytes(self._buffer[-4096:]).decode("utf-8", errors="replace")
                raise DebuggerCliError(f"Timed out waiting for debugger prompt. Tail:\n{tail}")
            self._read_some(timeout_s=max(0.01, min(0.2, deadline - time.monotonic())))
