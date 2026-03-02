from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from n64train.experiments.architectures import ArchitectureFamilySpec, fixed_architecture_suite
from n64train.paths import PATHS
from n64train.runtime.budget import ExperimentBudget
from n64train.runtime.launcher import LaunchOptions, Mupen64PlusSession
from n64train.runtime.types import SpeedMode


@dataclass(frozen=True)
class ArchitectureRunConfig:
    run_id: str
    arch: ArchitectureFamilySpec
    speed_mode: SpeedMode = SpeedMode.TRAIN_TURBO
    headed: bool = True
    resolution: str = "320x240"
    load_latest_state: bool = False
    frame_budget: int = 50_000
    turbo_dummy_audio: bool = True


@dataclass
class ArchitectureRunHandle:
    config: ArchitectureRunConfig
    session: Mupen64PlusSession
    budget: ExperimentBudget

    def status(self) -> dict[str, object]:
        return {
            "run_id": self.config.run_id,
            "arch_id": self.config.arch.arch_id,
            "pid": self.session.process.pid if self.session.process else None,
            "exit_code": self.session.poll(),
            "budget": self.budget.snapshot(),
        }


@dataclass
class ConcurrentArchitectureRunner:
    run_configs: tuple[ArchitectureRunConfig, ...]
    handles: list[ArchitectureRunHandle] = field(default_factory=list)

    @classmethod
    def default_fixed_suite(
        cls,
        *,
        resolution: str = "320x240",
        speed_mode: SpeedMode = SpeedMode.TRAIN_TURBO,
        frame_budget: int = 50_000,
    ) -> "ConcurrentArchitectureRunner":
        configs: list[ArchitectureRunConfig] = []
        for idx, arch in enumerate(fixed_architecture_suite(), start=1):
            configs.append(
                ArchitectureRunConfig(
                    run_id=f"arch{idx:02d}-{arch.arch_id}",
                    arch=arch,
                    speed_mode=speed_mode,
                    resolution=resolution,
                    frame_budget=frame_budget,
                )
            )
        return cls(run_configs=tuple(configs))

    def planned_launch_specs(self) -> list[dict[str, object]]:
        specs: list[dict[str, object]] = []
        for cfg in self.run_configs:
            session = self._make_session(cfg)
            cmd, env = session.build_launch_spec()
            specs.append(
                {
                    "run_id": cfg.run_id,
                    "arch_id": cfg.arch.arch_id,
                    "command": cmd,
                    "instance_id": env.get("M64P_INSTANCE_ID"),
                    "resolution": env.get("M64P_RESOLUTION"),
                    "window_mode": env.get("M64P_WINDOW_MODE"),
                    "nospeedlimit": env.get("M64P_NOSPEEDLIMIT"),
                    "log_path": str(self._log_path(cfg)),
                }
            )
        return specs

    def launch_all(self) -> list[ArchitectureRunHandle]:
        if self.handles:
            raise RuntimeError("Runner already has active handles")
        handles: list[ArchitectureRunHandle] = []
        for cfg in self.run_configs:
            session = self._make_session(cfg)
            session.start()
            handles.append(
                ArchitectureRunHandle(
                    config=cfg,
                    session=session,
                    budget=ExperimentBudget(max_env_frames=cfg.frame_budget),
                )
            )
        self.handles = handles
        return handles

    def stop_all(self) -> None:
        for handle in self.handles:
            handle.session.stop()

    def wait_for(self, seconds: float) -> None:
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            time.sleep(0.1)

    def status(self) -> list[dict[str, object]]:
        return [handle.status() for handle in self.handles]

    def _log_path(self, cfg: ArchitectureRunConfig) -> Path:
        return PATHS.training_runs_root / f"{cfg.run_id}.log"

    def _make_session(self, cfg: ArchitectureRunConfig) -> Mupen64PlusSession:
        extra_env = (("M64P_WINDOW_MODE", "windowed"),)
        options = LaunchOptions(
            load_latest_state=cfg.load_latest_state,
            speed_mode=cfg.speed_mode,
            headed=cfg.headed,
            resolution=cfg.resolution,
            instance_id=cfg.run_id,
            log_path=self._log_path(cfg),
            dummy_audio_in_turbo=cfg.turbo_dummy_audio,
            extra_env=extra_env,
        )
        return Mupen64PlusSession(options)

