from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "scripts" / "m64p-mk4.sh").exists():
            return parent
    raise RuntimeError("Could not locate repo root (expected scripts/m64p-mk4.sh)")


@dataclass(frozen=True)
class ProjectPaths:
    repo_root: Path

    @property
    def training_root(self) -> Path:
        return self.repo_root / "training"

    @property
    def training_data_root(self) -> Path:
        return self.training_root / "data"

    @property
    def training_logs_root(self) -> Path:
        return self.training_data_root / "logs"

    @property
    def training_scenarios_root(self) -> Path:
        return self.training_data_root / "scenarios"

    @property
    def training_runs_root(self) -> Path:
        return self.training_logs_root / "runs"

    @property
    def emulator_launcher(self) -> Path:
        return self.repo_root / "scripts" / "m64p-mk4.sh"

    @property
    def load_latest_launcher(self) -> Path:
        return self.repo_root / "scripts" / "m64p-load-latest-state.sh"

    @property
    def local_m64p_root(self) -> Path:
        return self.repo_root / ".m64p"

    @property
    def local_m64p_instances_root(self) -> Path:
        return self.local_m64p_root / "instances"

    @property
    def savestate_dir(self) -> Path:
        return self.local_m64p_root / "data" / "savestates"

    @property
    def sram_dir(self) -> Path:
        return self.local_m64p_root / "data" / "sram"

    @property
    def screenshot_dir(self) -> Path:
        return self.local_m64p_root / "data" / "screenshots"

    @property
    def roms(self) -> list[Path]:
        return sorted(self.repo_root.glob("*.z64"))


PATHS = ProjectPaths(repo_root=_find_repo_root())
