"""
CEI pipeline assembly.

The patent modules (101-112) are stateful objects: the adaptive weight
recalibrator carries alpha/beta/gamma across calls, and the oscillation
detector carries its consecutive-episode counter and suppression flag. That
is correct for a single control loop supervising one environment, which is
what the specification describes.

It is NOT correct for a multi-tenant HTTP service. Sharing one instance set
across requests meant an analysis of cluster A shifted the weights used for
cluster B, and repeating an identical request returned different numbers each
time -- beta decayed from 0.27 to 0.09 over four calls, progressively erasing
the entropy term from the score.

This module builds a fresh instance set per request so each analysis is a
pure function of its input. The classes and their patent reference numbers
are preserved unchanged; only instance lifetime differs.

Long-lived state that must genuinely persist across requests (the rollback
manager's snapshot store) is deliberately NOT built here -- see main.py.
"""

from dataclasses import dataclass

from .cei.data_collector import DataCollector
from .cei.cei_calculator import CEICalculator
from .cei.stability_monitor import StabilityMonitor
from .cei.adaptive_weights import AdaptiveWeightRecalibrator
from .graph.dependency_graph import DependencyGraphConstructor
from .governance.policy_store import GovernancePolicyStore
from .oscillation.detector import OscillationDetector
from .fault.propagation import FaultPropagationSimulator
from .simulation.validator import PreModificationValidator
from .recommendation.actuator import RecommendationActuator


@dataclass(frozen=True)
class CEIPipeline:
    """One request's worth of patent modules 101-111."""

    data_collector: DataCollector                    # Module 101
    graph_constructor: DependencyGraphConstructor    # Module 103
    governance_store: GovernancePolicyStore          # Module 104
    stability_monitor: StabilityMonitor              # Module 105
    cei_calculator: CEICalculator                    # Module 106
    weight_recalibrator: AdaptiveWeightRecalibrator  # Module 107
    oscillation_detector: OscillationDetector        # Module 108
    fault_simulator: FaultPropagationSimulator       # Module 109
    pre_mod_validator: PreModificationValidator      # Module 110
    actuator: RecommendationActuator                 # Module 111


def build_pipeline() -> CEIPipeline:
    """
    Construct a fresh, unshared set of pipeline modules.

    Cheap enough to do per request: every constructor here is a few field
    assignments, with no I/O and no precomputation.
    """
    return CEIPipeline(
        data_collector=DataCollector(),
        graph_constructor=DependencyGraphConstructor(),
        governance_store=GovernancePolicyStore(),
        stability_monitor=StabilityMonitor(),
        cei_calculator=CEICalculator(),
        weight_recalibrator=AdaptiveWeightRecalibrator(),
        oscillation_detector=OscillationDetector(),
        fault_simulator=FaultPropagationSimulator(),
        pre_mod_validator=PreModificationValidator(),
        actuator=RecommendationActuator(),
    )
