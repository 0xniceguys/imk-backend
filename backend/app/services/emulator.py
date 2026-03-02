"""
Emulator launcher — boots the bridge server which manages mupen64plus.

Platform-aware:
  - macOS (dev):  .dylib plugins, /opt/homebrew paths, native window
  - Linux (EC2):  .so plugins, /usr/lib paths, Xvfb headless display

The bridge server exposes a Unix socket for JSON commands
(HELLO, LOAD_SAVESTATE, SET_INPUTS, STEP_FRAMES, DEBUGGER_COMMAND).

Self-contained: no imports from the training package.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from uuid import uuid4

logger = logging.getLogger(__name__)

IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

# Repo root is 3 levels up from backend/app/services/
REPO_ROOT = Path(__file__).resolve().parents[3]
M64P_ROOT = REPO_ROOT / ".m64p"

# Bridge server script (training code)
BRIDGE_SERVER_SCRIPT = REPO_ROOT / "training" / "scripts" / "run_bridge_server.py"
TRAINING_SRC = REPO_ROOT / "training" / "src"

# Custom mupen64plus builds with debugger CLI support (stateload/statesave patched in).
# Both Linux and macOS use the vendor build — it has stateload support.
CUSTOM_UI_BINARY = REPO_ROOT / "vendor" / "mupen64plus-ui-console" / "projects" / "unix" / "mupen64plus"

# Platform-specific shared library extension and paths
if IS_LINUX:
    _LIB_EXT = ".so"
    _PLUGIN_DIR = "/usr/lib/x86_64-linux-gnu/mupen64plus"
    _DATA_DIR = "/usr/share/mupen64plus"
    # rice + Xvnc (TigerVNC): Xvnc has built-in Mesa software GLX so rice's
    # OpenGL renders correctly. Xvfb's GLX is broken for direct GL contexts.
    _GFX_PLUGIN = f"mupen64plus-video-rice{_LIB_EXT}"
    _AUDIO_PLUGIN = f"mupen64plus-audio-sdl{_LIB_EXT}"
    _INPUT_PLUGIN_NAME = f"n64train-input{_LIB_EXT}"
    _RSP_PLUGIN = f"mupen64plus-rsp-hle{_LIB_EXT}"
    _CORELIB_NAME = f"libmupen64plus{_LIB_EXT}.2"
else:  # macOS
    _LIB_EXT = ".dylib"
    _PLUGIN_DIR = "/opt/homebrew/lib/mupen64plus"
    _DATA_DIR = "/opt/homebrew/share/mupen64plus"
    _GFX_PLUGIN = f"mupen64plus-video-rice{_LIB_EXT}"
    _AUDIO_PLUGIN = f"mupen64plus-audio-sdl{_LIB_EXT}"
    _INPUT_PLUGIN_NAME = f"n64train-input{_LIB_EXT}"
    _RSP_PLUGIN = f"mupen64plus-rsp-hle{_LIB_EXT}"
    _CORELIB_NAME = f"libmupen64plus{_LIB_EXT}"

# Vendor-built core lib — has DEBUGGER=1, required for stateload/statesave support.
CUSTOM_CORELIB = REPO_ROOT / "vendor" / "mupen64plus-core" / "projects" / "unix" / _CORELIB_NAME
CUSTOM_INPUT_PLUGIN = REPO_ROOT / "vendor" / "n64train-input" / _INPUT_PLUGIN_NAME

# Default ROM
DEFAULT_ROM = REPO_ROOT / "Mortal Kombat 4 (USA).z64"


@dataclass(frozen=True)
class LaunchOptions:
    instance_id: str | None = None
    # Linux: always start Xvfb at 640x480 — z64 video plugin requests this
    # exact SDL video mode. If Xvfb is smaller the window is clipped/invisible.
    resolution: str = "640x480"
    socket_path: str | None = None


class EmulatorSession:
    """Launches the bridge server which manages a mupen64plus instance.

    On Linux EC2, also manages a per-instance Xvfb virtual display so
    the emulator can render headlessly. FFmpeg captures this display.
    """

    def __init__(self, options: LaunchOptions | None = None) -> None:
        self.options = options or LaunchOptions()
        self.instance_id = self.options.instance_id or f"match-{uuid4().hex[:8]}"
        self.process: subprocess.Popen[str] | None = None
        self._xvfb_process: subprocess.Popen | None = None
        self._log_handle: TextIO | None = None

        # Xvfb display number (Linux only)
        self.display: str = ":99"
        self._display_num: int = 99

        # Socket path for the bridge server
        if self.options.socket_path:
            self.socket_path = Path(self.options.socket_path)
        else:
            self.socket_path = REPO_ROOT / "training" / "data" / "bridge" / f"{self.instance_id}.sock"

        # Controller mmap file paths — per-instance so concurrent matches don't collide
        _ctrl_dir = Path(f"/tmp/imk/{self.instance_id}")
        _ctrl_dir.mkdir(parents=True, exist_ok=True)
        self.ctrl_p1_path = str(_ctrl_dir / "ctrl_p1")
        self.ctrl_p2_path = str(_ctrl_dir / "ctrl_p2")

    @property
    def instance_dir(self) -> Path:
        return M64P_ROOT / "instances" / self.instance_id

    @property
    def screenshot_dir(self) -> Path:
        """Screenshot dir — uses tmpfs on both platforms for zero disk I/O.

        Linux: /dev/shm (explicit tmpfs)
        macOS: /tmp (APFS on-SSD for Apple Silicon, fast enough; tmpfs on most CI)
        """
        if IS_LINUX:
            return Path("/dev/shm") / "imk_screenshots" / self.instance_id
        # macOS: /tmp is fast APFS temp storage, avoids writing to the instance dir
        return Path("/tmp") / "imk_screenshots" / self.instance_id

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("Bridge server process is already running")

        if not BRIDGE_SERVER_SCRIPT.exists():
            raise FileNotFoundError(f"Bridge server script not found: {BRIDGE_SERVER_SCRIPT}")
        if not CUSTOM_UI_BINARY.exists():
            raise FileNotFoundError(f"Custom mupen64plus binary not found: {CUSTOM_UI_BINARY}")
        if not DEFAULT_ROM.exists():
            raise FileNotFoundError(f"ROM not found: {DEFAULT_ROM}")

        # On Linux, start Xvfb for headless rendering
        if IS_LINUX:
            self._start_xvfb()

        # Clean up stale socket
        if self.socket_path.exists():
            self.socket_path.unlink()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Config dir for this instance
        cfg_dir = M64P_ROOT / "instances" / self.instance_id / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)

        # Screenshot dir (used as fallback, FFmpeg is primary on Linux)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._patch_screenshot_path(cfg_dir)

        cmd = [
            sys.executable,
            str(BRIDGE_SERVER_SCRIPT),
            "--socket-path", str(self.socket_path),
            "--instance-id", self.instance_id,
            "--resolution", self.options.resolution,
            "--launch-emulator",
            "--memory-reader", "debugger-dump",
            "--rom-path", str(DEFAULT_ROM),
            "--debugger-ui-binary", str(CUSTOM_UI_BINARY),
            "--debugger-corelib", str(CUSTOM_CORELIB),
            "--debugger-plugindir", _PLUGIN_DIR,
            "--debugger-configdir", str(cfg_dir),
            "--debugger-datadir", _DATA_DIR,
            "--debugger-gfx-plugin", _GFX_PLUGIN,
            "--debugger-audio-plugin", _AUDIO_PLUGIN,
            "--debugger-input-plugin", str(CUSTOM_INPUT_PLUGIN),
            "--debugger-rsp-plugin", _RSP_PLUGIN,
            "--debugger-emumode", "0",
        ]

        # Log to file
        log_dir = self.instance_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "bridge_server.log"
        self._log_handle = log_path.open("a", encoding="utf-8")

        # Environment
        env = dict(os.environ)
        existing_pypath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{TRAINING_SRC}:{existing_pypath}" if existing_pypath else str(TRAINING_SRC)
        env["N64TRAIN_CTRL_P1"] = self.ctrl_p1_path
        env["N64TRAIN_CTRL_P2"] = self.ctrl_p2_path

        # On Linux, point the emulator at the Xvfb display
        if IS_LINUX:
            env["DISPLAY"] = self.display
            env["SDL_VIDEODRIVER"] = "x11"
            # Force SDL software path so z64's framebuffer blit reaches Xvfb
            env["SDL_RENDER_DRIVER"] = "software"
            env["SDL_FRAMEBUFFER_ACCELERATION"] = "0"
            # Suppress real audio output — no PulseAudio/ALSA needed on headless server.
            # The SDL audio plugin still loads and satisfies mupen's plugin system,
            # but SDL routes audio to /dev/null internally.
            env["SDL_AUDIODRIVER"] = "dummy"
            # Force Mesa software rasterizer (swrast/llvmpipe) for OpenGL.
            # Do NOT set LIBGL_ALWAYS_INDIRECT — that forces server-side Xvnc GLX
            # which doesn't advertise the visuals rice needs, making glXChooseVisual
            # return NULL and crashing mupen with BadValue on GLXCreateContext.
            env["LIBGL_ALWAYS_SOFTWARE"] = "1"
            logger.info(
                "Linux emulator env: DISPLAY=%s SDL_RENDER_DRIVER=software z64",
                self.display,
            )

        logger.info(
            "Launching bridge server: ctrl_p1=%s, ctrl_p2=%s, display=%s",
            self.ctrl_p1_path, self.ctrl_p2_path,
            self.display if IS_LINUX else "native",
        )

        self.process = subprocess.Popen(
            cmd,
            env=env,
            text=True,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(REPO_ROOT),
            start_new_session=True,
        )
        logger.info(
            "Bridge server started (pid=%d, instance=%s, socket=%s)",
            self.process.pid, self.instance_id, self.socket_path,
        )

        # mupen64plus-video-rice opens a 1x1 window on headless Xvfb (SDL bug).
        # Use xdotool to force-resize it to the correct resolution so FFmpeg
        # x11grab captures actual game frames instead of a black root window.
        if IS_LINUX:
            import threading
            _display = self.display
            _w, _h = self.options.resolution.split("x")

            def _resize_window():
                import time as _t, subprocess as _sp
                # Poll for the mupen64plus window (up to 10s)
                for _attempt in range(20):
                    _t.sleep(0.5)
                    try:
                        r = _sp.run(
                            ["xdotool", "search", "--display", _display,
                             "--name", "Mupen64Plus"],
                            capture_output=True, text=True, timeout=5,
                        )
                        # Also try Z64gl window name
                        if not r.stdout.strip():
                            r = _sp.run(
                                ["xdotool", "search", "--display", _display,
                                 "--name", "Z64gl"],
                                capture_output=True, text=True, timeout=5,
                            )
                        if r.stdout.strip():
                            win_id = r.stdout.strip().split()[0]
                            logger.info(
                                "Resizing mupen64plus window %s → %sx%s on %s",
                                win_id, _w, _h, _display,
                            )
                            _sp.run(
                                ["xdotool", "windowmove", "--display", _display,
                                 win_id, "0", "0"],
                                capture_output=True, timeout=5,
                            )
                            _sp.run(
                                ["xdotool", "windowsize", "--display", _display,
                                 win_id, _w, _h],
                                capture_output=True, timeout=5,
                            )
                            # Log result
                            xi = _sp.run(
                                ["xwininfo", "-id", win_id, "-display", _display],
                                capture_output=True, text=True, timeout=5,
                            )
                            for ln in xi.stdout.splitlines():
                                if any(k in ln for k in ("geometry", "Width", "Height")):
                                    logger.info("Window after resize: %s", ln.strip())
                            return
                    except Exception as _e:
                        logger.warning("xdotool attempt %d failed: %s", _attempt, _e)
                logger.warning(
                    "mupen64plus window not found on %s after 10s "
                    "— FFmpeg may capture black frames",
                    _display,
                )

            threading.Thread(target=_resize_window, daemon=True).start()

    def _start_xvfb(self) -> None:
        """Start Xvnc (TigerVNC) or fall back to Xvfb for headless rendering."""
        w, h = self.options.resolution.split("x")

        # Find a free display number
        for num in range(99, 199):
            if not Path(f"/tmp/.X{num}-lock").exists():
                self._display_num = num
                self.display = f":{num}"
                break

        # Prefer Xvnc (TigerVNC) — has proper Mesa software GLX built-in.
        # Fall back to Xvfb if Xvnc not installed.
        if shutil.which("Xvnc"):
            cmd = [
                "Xvnc", self.display,
                "-geometry", f"{w}x{h}",
                "-depth", "24",
                "-SecurityTypes", "None",  # no VNC password needed
                "-ac",
            ]
            server_name = "Xvnc"
        else:
            logger.warning("Xvnc not found, falling back to Xvfb (GL may not work)")
            cmd = [
                "Xvfb", self.display,
                "-screen", "0", f"{w}x{h}x24",
                "-ac",
                "+extension", "GLX",
            ]
            server_name = "Xvfb"

        self._xvfb_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Poll briefly to catch immediate crashes
        for _ in range(5):
            if self._xvfb_process.poll() is not None:
                raise RuntimeError(
                    f"{server_name} failed to start on display {self.display} "
                    f"(rc={self._xvfb_process.returncode})"
                )
            time.sleep(0.1)
        logger.info("%s started on display %s (pid=%d)", server_name, self.display, self._xvfb_process.pid)

    def wait_for_socket(self, timeout: float = 45.0) -> bool:
        """Wait for the bridge server socket to appear."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.socket_path.exists():
                logger.info("Bridge socket ready: %s", self.socket_path)
                return True
            if self.process and self.process.poll() is not None:
                logger.error(
                    "Bridge server exited early (rc=%d, instance=%s)",
                    self.process.returncode, self.instance_id,
                )
                return False
            time.sleep(0.5)
        logger.error("Timed out waiting for bridge socket: %s", self.socket_path)
        return False

    def poll(self) -> int | None:
        if self.process is None:
            return None
        return self.process.poll()

    def stop(self) -> None:
        import signal

        # Stop bridge server + mupen64plus
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        pgid = os.getpgid(self.process.pid)
                        os.killpg(pgid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        self.process.kill()
                    self.process.wait(timeout=5)

        # Stop Xvfb
        if self._xvfb_process is not None and self._xvfb_process.poll() is None:
            self._xvfb_process.terminate()
            try:
                self._xvfb_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._xvfb_process.kill()
            logger.info("Xvfb stopped (display=%s)", self.display)
            self._xvfb_process = None

        self._cleanup()
        logger.info("Bridge server stopped (instance=%s)", self.instance_id)

    def _cleanup(self) -> None:
        self._close_log()
        if self.socket_path.exists():
            with suppress(OSError):
                self.socket_path.unlink()
        for ctrl_path in (self.ctrl_p1_path, self.ctrl_p2_path):
            with suppress(OSError):
                if os.path.exists(ctrl_path):
                    os.unlink(ctrl_path)
        # Remove the per-instance ctrl dir
        _ctrl_dir = Path(f"/tmp/imk/{self.instance_id}")
        with suppress(OSError):
            import shutil as _shutil2
            _shutil2.rmtree(_ctrl_dir, ignore_errors=True)
        # Clean up tmpfs screenshot dir on Linux
        if IS_LINUX:
            with suppress(OSError):
                import shutil as _shutil
                _shutil.rmtree(self.screenshot_dir, ignore_errors=True)

    def _patch_screenshot_path(self, cfg_dir: Path) -> None:
        """Pre-seed the mupen64plus config with ScreenshotPath + JPEG output + window size.

        Setting ScreenShotFormat=2 makes mupen64plus-video-rice write JPEG
        files directly. ScreenWidth/Height forces the Rice plugin window to
        open at the correct size on headless Xvfb (default is 1x1 which
        causes FFmpeg x11grab to capture black).
        """
        import re
        import shutil as _shutil

        cfg_file = cfg_dir / "mupen64plus.cfg"
        shot_dir_str = str(self.screenshot_dir)
        w, h = self.options.resolution.split("x")
        res_w, res_h = int(w), int(h)

        if not cfg_file.exists():
            template_cfgs = sorted(
                (M64P_ROOT / "instances").glob("*/config/mupen64plus.cfg")
            )
            if template_cfgs:
                _shutil.copy2(template_cfgs[0], cfg_file)
                logger.info("Copied base config from %s", template_cfgs[0])

        if cfg_file.exists():
            content = cfg_file.read_text(encoding="utf-8")
        else:
            # No template — write a minimal config from scratch
            content = ""

        def _set_or_append(txt: str, key: str, value: str, section: str) -> str:
            """Set key=value in section, or append it."""
            pat = rf'^{re.escape(key)}\s*=.*$'
            if re.search(pat, txt, re.MULTILINE):
                return re.sub(pat, f'{key} = {value}', txt, flags=re.MULTILINE)
            if f'[{section}]' in txt:
                return txt.replace(f'[{section}]', f'[{section}]\n{key} = {value}', 1)
            return txt + f'\n[{section}]\n{key} = {value}\n'

        # Screenshot path + format
        content = _set_or_append(content, 'ScreenshotPath', f'"{shot_dir_str}"', 'Core')
        content = _set_or_append(content, 'ScreenShotFormat', '2', 'Video-Rice')

        # Window resolution — forces Rice plugin to open 320x240 on Xvfb
        # Without this it defaults to 1x1 in headless mode → FFmpeg gets black
        content = _set_or_append(content, 'ScreenWidth',      str(res_w), 'Video-General')
        content = _set_or_append(content, 'ScreenHeight',     str(res_h), 'Video-General')
        content = _set_or_append(content, 'FullscreenWidth',  str(res_w), 'Video-General')
        content = _set_or_append(content, 'FullscreenHeight', str(res_h), 'Video-General')
        content = _set_or_append(content, 'Fullscreen',       'False',    'Video-General')

        cfg_file.write_text(content, encoding="utf-8")
        logger.info(
            "Config: ScreenshotPath=%s, format=JPEG, resolution=%dx%d → %s",
            shot_dir_str, res_w, res_h, cfg_file,
        )


    def _close_log(self) -> None:
        if self._log_handle is None:
            return
        with suppress(Exception):
            self._log_handle.flush()
        with suppress(Exception):
            self._log_handle.close()
        self._log_handle = None
