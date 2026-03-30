"""
Patent Module 107: Adaptive Weight Recalibration
Implements closed-loop control by adjusting CEI weights based on observed
system behavior. Patent Section 3 / Paper Section V.

Rules:
- If stability decreases: increase alpha (prioritize centrality stabilization)
- If topology changes: increase gamma (emphasize governance risk)
- If oscillation detected: decrease beta (dampen entropy sensitivity)
"""
from typing import Dict


class AdaptiveWeightRecalibrator:
    """
    Adjusts the contribution of centrality (alpha), entropy (beta), and
    governance risk (gamma) based on observed system stability, dependency
    changes, and oscillation state.
    
    Patent: "The adaptive weight recalibration module 107 adjusts weights
    based on observed system behavior."
    """

    # Default weights
    DEFAULT_ALPHA = 0.40  # centrality weight
    DEFAULT_BETA = 0.35   # entropy weight
    DEFAULT_GAMMA = 0.25  # risk weight

    # Adjustment step sizes
    STABILITY_STEP = 0.05
    OSCILLATION_STEP = 0.08
    TOPOLOGY_STEP = 0.05

    def __init__(self):
        self.alpha = self.DEFAULT_ALPHA
        self.beta = self.DEFAULT_BETA
        self.gamma = self.DEFAULT_GAMMA
        self.recalibration_history = []

    def recalibrate(
        self,
        stability_scores: Dict[str, float] = None,
        oscillation_detected: bool = False,
        topology_changed: bool = False,
    ) -> Dict[str, float]:
        """
        Recalibrate CEI weights based on system state.
        Implements the closed-loop control logic from Patent Section 3.
        """
        # Compute aggregate stability
        if stability_scores:
            avg_stability = sum(stability_scores.values()) / len(stability_scores)
        else:
            avg_stability = 0.5

        # Rule 1: If stability decreases, increase alpha
        # "If stability decreases, alpha is increased to give more weight
        # to centrality to stabilize critical nodes."
        if avg_stability < 0.4:
            self.alpha = min(0.6, self.alpha + self.STABILITY_STEP)
            self.beta = max(0.15, self.beta - self.STABILITY_STEP / 2)
            self.gamma = max(0.15, self.gamma - self.STABILITY_STEP / 2)

        # Rule 2: If oscillation detected, decrease beta
        # "When oscillation is detected, beta is temporarily reduced to
        # dampen sensitivity to demand entropy."
        if oscillation_detected:
            self.beta = max(0.10, self.beta - self.OSCILLATION_STEP)
            self.alpha = min(0.6, self.alpha + self.OSCILLATION_STEP / 2)
            self.gamma = min(0.4, self.gamma + self.OSCILLATION_STEP / 2)

        # Rule 3: If topology changes, increase gamma
        # "When dependency topology changes, gamma increases to
        # emphasize governance risk."
        if topology_changed:
            self.gamma = min(0.4, self.gamma + self.TOPOLOGY_STEP)
            self.alpha = max(0.2, self.alpha - self.TOPOLOGY_STEP / 2)
            self.beta = max(0.15, self.beta - self.TOPOLOGY_STEP / 2)

        # Normalize weights to sum to 1.0
        total = self.alpha + self.beta + self.gamma
        self.alpha /= total
        self.beta /= total
        self.gamma /= total

        weights = {
            "alpha": round(self.alpha, 4),
            "beta": round(self.beta, 4),
            "gamma": round(self.gamma, 4),
        }

        self.recalibration_history.append({
            "weights": weights,
            "triggers": {
                "avg_stability": round(avg_stability, 4),
                "oscillation_detected": oscillation_detected,
                "topology_changed": topology_changed,
            }
        })

        return weights

    def get_current_weights(self) -> Dict[str, float]:
        """Return current weight configuration."""
        return {
            "alpha": round(self.alpha, 4),
            "beta": round(self.beta, 4),
            "gamma": round(self.gamma, 4),
        }

    def reset(self):
        """Reset weights to defaults."""
        self.alpha = self.DEFAULT_ALPHA
        self.beta = self.DEFAULT_BETA
        self.gamma = self.DEFAULT_GAMMA
