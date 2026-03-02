from __future__ import annotations

import json
from pathlib import Path

from n64train.paths import PATHS
from n64train.runtime.types import ScenarioSpec


def scenario_bank_dir() -> Path:
    root = PATHS.training_scenarios_root
    root.mkdir(parents=True, exist_ok=True)
    return root


def scenario_manifest_path(scenario_id: str) -> Path:
    return scenario_bank_dir() / f"{scenario_id}.json"


def save_scenario(spec: ScenarioSpec) -> Path:
    path = scenario_manifest_path(spec.scenario_id)
    path.write_text(json.dumps(spec.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_scenario(scenario_id: str) -> ScenarioSpec:
    path = scenario_manifest_path(scenario_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ScenarioSpec.from_dict(payload)


def list_scenarios() -> list[ScenarioSpec]:
    specs: list[ScenarioSpec] = []
    for path in sorted(scenario_bank_dir().glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            specs.append(ScenarioSpec.from_dict(payload))
        except Exception:
            continue
    return sorted(specs, key=lambda spec: spec.scenario_id)


def scenario_exists(scenario_id: str) -> bool:
    return scenario_manifest_path(scenario_id).exists()

