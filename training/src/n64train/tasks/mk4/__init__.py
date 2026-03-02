"""MK4-specific task specs, semantics priorities, and reward shaping placeholders."""

from n64train.tasks.mk4.semantics import SEMANTIC_PRIORITIES
from n64train.tasks.mk4.spec import DEFAULT_LOWLEVEL_TASK, MK4TaskSpec

__all__ = ["DEFAULT_LOWLEVEL_TASK", "MK4TaskSpec", "SEMANTIC_PRIORITIES"]
