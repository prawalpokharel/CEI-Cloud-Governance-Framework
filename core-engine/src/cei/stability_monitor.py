"""
Patent Module 105: Stability Monitor
Computes stability scores based on historical metric variance over
configurable windows (60-90 days per paper Section III).
"""
import numpy as np
from typing import Dict, List, Any


class StabilityMonitor:
    """
    Monitors infrastructure stability by computing variance-based scores
    from longitudinal telemetry data. Used by the Adaptive Weight
    Recalibration module (107) to adjust CEI weights.
    """

    def __init__(self, default_window: int = 90):
        self.default_window = default_window
        self.stability_history = []

    def compute(
        self, telemetry: List[Dict[str, Any]], window_days: int = None
    ) -> Dict[str, float]:
        """
        Compute stability score for each node based on metric variance.
        Lower variance = higher stability = higher score.
        """
        window = window_days or self.default_window
        scores = {}

        for node in telemetry:
            node_id = node["node_id"]
            history = node.get("utilization_history", [])

            if not history:
                scores[node_id] = 0.5  # neutral stability
                continue

            # Use last N days of history
            recent = history[-window:]
            cpu_vals = [h.get("cpu", 0.5) for h in recent]
            mem_vals = [h.get("memory", 0.5) for h in recent]

            # Compute coefficient of variation (normalized variance)
            cpu_cv = self._coefficient_of_variation(cpu_vals)
            mem_cv = self._coefficient_of_variation(mem_vals)

            # Stability = 1 - normalized_variance (higher = more stable)
            # Cap CV at 1.0 for normalization
            stability = 1.0 - min(1.0, (cpu_cv + mem_cv) / 2.0)
            scores[node_id] = round(max(0.0, min(1.0, stability)), 4)

        self.stability_history.append({
            "scores": scores,
            "window_days": window,
            "node_count": len(scores)
        })

        return scores

    def _coefficient_of_variation(self, values: List[float]) -> float:
        """Compute coefficient of variation (std/mean)."""
        if not values:
            return 0.0
        arr = np.array(values)
        mean = np.mean(arr)
        if mean == 0:
            return 0.0
        return float(np.std(arr) / mean)

    def get_aggregate_stability(self) -> float:
        """Get overall system stability as mean of all node scores."""
        if not self.stability_history:
            return 0.5
        latest = self.stability_history[-1]["scores"]
        if not latest:
            return 0.5
        return round(float(np.mean(list(latest.values()))), 4)
