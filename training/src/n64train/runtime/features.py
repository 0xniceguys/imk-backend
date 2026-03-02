from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FeatureSource(str, Enum):
    FRAME = "frame"
    RAM = "ram"
    EVENT = "event"
    INPUT = "input"
    META = "meta"


class PrivilegeLevel(str, Enum):
    DEPLOY_OBSERVABLE = "deploy_observable"
    TRAIN_ONLY = "train_only"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    source: FeatureSource
    dtype: str
    shape: tuple[int, ...]
    units: str
    normalization: str
    privilege_level: PrivilegeLevel
    confidence: float = 1.0
    version: int = 1


@dataclass
class FeatureRegistry:
    schema_version: str = "0.1"
    _features: dict[str, FeatureSpec] = field(default_factory=dict)

    def add(self, spec: FeatureSpec) -> None:
        if spec.name in self._features:
            raise ValueError(f"Feature already registered: {spec.name}")
        self._features[spec.name] = spec

    def get(self, name: str) -> FeatureSpec:
        return self._features[name]

    def all(self) -> tuple[FeatureSpec, ...]:
        return tuple(self._features.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._features.keys())

    def train_only_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, spec in self._features.items()
            if spec.privilege_level is PrivilegeLevel.TRAIN_ONLY
        )

    def deploy_observable_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, spec in self._features.items()
            if spec.privilege_level is PrivilegeLevel.DEPLOY_OBSERVABLE
        )

    def validate_feature_names(self, names: list[str] | tuple[str, ...]) -> None:
        unknown = [name for name in names if name not in self._features]
        if unknown:
            raise ValueError(f"Unknown feature(s): {', '.join(sorted(unknown))}")

    def validate_student_feature_set(self, names: list[str] | tuple[str, ...]) -> None:
        self.validate_feature_names(names)
        leaked = [
            name
            for name in names
            if self._features[name].privilege_level is PrivilegeLevel.TRAIN_ONLY
        ]
        if leaked:
            raise ValueError(f"Privileged train-only features not allowed in student set: {', '.join(leaked)}")


def mk4_phase0_registry() -> FeatureRegistry:
    reg = FeatureRegistry(schema_version="mk4-phase0")
    reg.add(
        FeatureSpec(
            name="frame_rgb",
            source=FeatureSource.FRAME,
            dtype="uint8",
            shape=(120, 160, 3),
            units="rgb",
            normalization="none",
            privilege_level=PrivilegeLevel.DEPLOY_OBSERVABLE,
        )
    )
    reg.add(
        FeatureSpec(
            name="p1_x",
            source=FeatureSource.RAM,
            dtype="float32",
            shape=(1,),
            units="world_x",
            normalization="standardize",
            privilege_level=PrivilegeLevel.TRAIN_ONLY,
        )
    )
    reg.add(
        FeatureSpec(
            name="p2_x",
            source=FeatureSource.RAM,
            dtype="float32",
            shape=(1,),
            units="world_x",
            normalization="standardize",
            privilege_level=PrivilegeLevel.TRAIN_ONLY,
        )
    )
    reg.add(
        FeatureSpec(
            name="p1_health",
            source=FeatureSource.RAM,
            dtype="int32",
            shape=(1,),
            units="hp",
            normalization="minmax_0_1",
            privilege_level=PrivilegeLevel.TRAIN_ONLY,
        )
    )
    reg.add(
        FeatureSpec(
            name="p2_health",
            source=FeatureSource.RAM,
            dtype="int32",
            shape=(1,),
            units="hp",
            normalization="minmax_0_1",
            privilege_level=PrivilegeLevel.TRAIN_ONLY,
        )
    )
    reg.add(
        FeatureSpec(
            name="timer",
            source=FeatureSource.RAM,
            dtype="int32",
            shape=(1,),
            units="frames_or_seconds",
            normalization="minmax_0_1",
            privilege_level=PrivilegeLevel.TRAIN_ONLY,
        )
    )
    reg.add(
        FeatureSpec(
            name="p1_facing",
            source=FeatureSource.RAM,
            dtype="int8",
            shape=(1,),
            units="direction",
            normalization="none",
            privilege_level=PrivilegeLevel.TRAIN_ONLY,
        )
    )
    reg.add(
        FeatureSpec(
            name="p2_facing",
            source=FeatureSource.RAM,
            dtype="int8",
            shape=(1,),
            units="direction",
            normalization="none",
            privilege_level=PrivilegeLevel.TRAIN_ONLY,
        )
    )
    return reg

