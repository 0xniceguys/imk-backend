from __future__ import annotations

import os
import subprocess
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from n64train.paths import PATHS
from n64train.runtime.types import SpeedMode


@dataclass(frozen=True)
class LaunchOptions:
    headless_dummy: bool = False
    load_latest_state: bool = False
    rom_path: Path | None = None
    speed_mode: SpeedMode = SpeedMode.DEBUG_VISIBLE
    headed: bool = True
    resolution: str | None = None
    instance_id: str | None = None
    log_path: Path | None = None
    no_speedlimit_in_turbo: bool = True
    window_mode: str | None = None
    dummy_audio_in_turbo: bool = False
    profile_name: str | None = None
    extra_env: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    extra_args: tuple[str, ...] = field(default_factory=tuple)


class Mupen64PlusSession:
    """
    Thin wrapper around the existing shell launchers in /scripts.

    We keep platform-specific logic in the shell script for now, while the training
    code talks to this class. Later we can swap the backend launcher for Linux.
    """

    def __init__(self, options: LaunchOptions | None = None) -> None:
        self.options = options or LaunchOptions()
        self.process: subprocess.Popen[str] | None = None
        self._log_handle: TextIO | None = None

    def _build_command(self) -> list[str]:
        launcher = PATHS.load_latest_launcher if self.options.load_latest_state else PATHS.emulator_launcher
        cmd: list[str] = [str(launcher)]
        if self.options.headless_dummy:
            # Experimental: useful for testing pure process control or RAM-first flows.
            # Pixel-based training usually still needs a real render context.
            cmd.extend(["--gfx", "dummy", "--audio", "dummy"])
        cmd.extend(self.options.extra_args)
        return cmd

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.options.rom_path is not None:
            env["ROM_PATH"] = str(self.options.rom_path)
        instance_id = self.options.instance_id or f"run-{uuid4().hex[:8]}"
        env["M64P_INSTANCE_ID"] = instance_id
        env["M64P_WINDOW_MODE"] = self.options.window_mode or ("windowed" if self.options.headed else "windowed")
        if self.options.resolution is not None:
            env["M64P_RESOLUTION"] = self.options.resolution
        if self.options.speed_mode is SpeedMode.TRAIN_TURBO and self.options.no_speedlimit_in_turbo:
            env["M64P_NOSPEEDLIMIT"] = "1"
            if self.options.dummy_audio_in_turbo and not self.options.headless_dummy:
                env["M64P_AUDIO_PLUGIN"] = "dummy"
        else:
            env["M64P_NOSPEEDLIMIT"] = "0"
        if self.options.profile_name:
            env["M64P_PROFILE_NAME"] = self.options.profile_name
        for key, value in self.options.extra_env:
            env[key] = value
        return env

    def build_launch_spec(self) -> tuple[list[str], dict[str, str]]:
        return self._build_command(), self._build_env()

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("Emulator process is already running")
        cmd, env = self.build_launch_spec()
        popen_kwargs: dict[str, object] = {"env": env, "text": True}
        if self.options.log_path is not None:
            self.options.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.options.log_path.open("a", encoding="utf-8")
            popen_kwargs["stdout"] = self._log_handle
            popen_kwargs["stderr"] = subprocess.STDOUT
        self.process = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[arg-type]

    def poll(self) -> int | None:
        if self.process is None:
            return None
        return self.process.poll()

    def wait(self) -> int:
        if self.process is None:
            raise RuntimeError("Emulator process has not been started")
        try:
            return self.process.wait()
        finally:
            self._close_log()

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is not None:
            self._close_log()
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        finally:
            self._close_log()

    def _close_log(self) -> None:
        if self._log_handle is None:
            return
        with suppress(Exception):
            self._log_handle.flush()
        with suppress(Exception):
            self._log_handle.close()
        self._log_handle = None
