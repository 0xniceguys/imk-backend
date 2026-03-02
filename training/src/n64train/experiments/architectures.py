from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArchitectureFamilySpec:
    arch_id: str
    display_name: str
    encoder_backbone: str
    world_model: bool
    hierarchical: bool
    planner: bool
    object_centric: bool
    opponent_belief: bool
    notes: str
    flagship: bool = False


FLAGSHIP_ARCH_ID = "mk4_object_belief_hier_wm"


def fixed_architecture_suite() -> tuple[ArchitectureFamilySpec, ...]:
    suite = (
        ArchitectureFamilySpec(
            arch_id="continuous_rssm_hier_ac",
            display_name="Continuous RSSM + Hierarchical Actor-Critic",
            encoder_backbone="cnn",
            world_model=True,
            hierarchical=True,
            planner=False,
            object_centric=False,
            opponent_belief=False,
            notes="Continuous latent world-model baseline.",
        ),
        ArchitectureFamilySpec(
            arch_id="discrete_rssm_hier_ac",
            display_name="Discrete RSSM + Hierarchical Actor-Critic",
            encoder_backbone="cnn",
            world_model=True,
            hierarchical=True,
            planner=False,
            object_centric=False,
            opponent_belief=False,
            notes="Discrete latent tactical abstraction baseline.",
        ),
        ArchitectureFamilySpec(
            arch_id="transformer_wm_hier_ac",
            display_name="Transformer World Model + Hierarchical Actor-Critic",
            encoder_backbone="transformer",
            world_model=True,
            hierarchical=True,
            planner=False,
            object_centric=False,
            opponent_belief=False,
            notes="Sequence-heavy world-model family (explicit transformer slot).",
        ),
        ArchitectureFamilySpec(
            arch_id=FLAGSHIP_ARCH_ID,
            display_name="Object-Centric + Opponent-Belief + Hierarchical WM",
            encoder_backbone="hybrid_cnn_slots",
            world_model=True,
            hierarchical=True,
            planner=False,
            object_centric=True,
            opponent_belief=True,
            notes="Flagship MK4 family combining object-centric and opponent-belief modeling.",
            flagship=True,
        ),
        ArchitectureFamilySpec(
            arch_id="latent_planner_mpc_prior",
            display_name="Latent Planner (MPC/CEM) + Policy Prior",
            encoder_backbone="cnn",
            world_model=True,
            hierarchical=True,
            planner=True,
            object_centric=False,
            opponent_belief=False,
            notes="Planning-over-latent family for tactical search.",
        ),
        ArchitectureFamilySpec(
            arch_id="cnn_rnn_reactive_baseline",
            display_name="CNN+RNN Reactive Control Baseline",
            encoder_backbone="cnn_rnn",
            world_model=False,
            hierarchical=False,
            planner=False,
            object_centric=False,
            opponent_belief=False,
            notes="Explicit CNN baseline to benchmark representation quality and responsiveness.",
        ),
    )
    if len(suite) != 6:
        raise RuntimeError("Fixed architecture suite must contain exactly 6 families")
    return suite


def architecture_by_id(arch_id: str) -> ArchitectureFamilySpec:
    for spec in fixed_architecture_suite():
        if spec.arch_id == arch_id:
            return spec
    raise KeyError(arch_id)

