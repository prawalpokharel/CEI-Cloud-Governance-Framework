"""
Patent Module 108: Oscillation Detector and Suppression
Monitors scaling event frequency and enforces cooldown via hysteresis windows.
Patent Section 4 / Paper Section VI.

"The oscillation detector 108 computes a frequency O(t) by analyzing the
time series of scaling events. If O(t) exceeds threshold theta, the system
enters a suppression mode where a hysteresis window W is enforced."
"""
import numpy as np
from typing import Dict, List, Any
from datetime import datetime


class OscillationDetector:
    """
    Detects resource thrashing (rapid repeated scaling events) and enforces
    suppression windows to stabilize the control plane.
    
    Patent Claim 3: "wherein the oscillation detector enters a suppression
    mode with a hysteresis window during which no modifications are permitted."
    """

    def __init__(
        self,
        default_threshold: float = 0.3,
        base_window_minutes: int = 15,
        max_window_minutes: int = 60,
    ):
        self.default_threshold = default_threshold
        self.base_window = base_window_minutes
        self.max_window = max_window_minutes
        self.suppression_active = False
        self.current_window = base_window_minutes
        self.consecutive_oscillations = 0
        self.detection_history = []

    def detect(
        self,
        telemetry: List[Dict],
        threshold: float = None
    ) -> Dict[str, Any]:
        """
        Analyze scaling event patterns to detect oscillation.
        
        Computes O(t) = scaling event frequency.
        If O(t) > theta, enter suppression mode with hysteresis window W.
        """
        theta = threshold or self.default_threshold

        # Analyze each node for oscillation patterns
        node_oscillations = {}
        system_oscillation_score = 0.0
        oscillating_nodes = []

        for node in telemetry:
            node_id = node["node_id"]
            history = node.get("utilization_history", [])

            if len(history) < 3:
                node_oscillations[node_id] = {
                    "oscillation_frequency": 0.0,
                    "is_oscillating": False
                }
                continue

            # Compute oscillation frequency O(t) from utilization variance
            o_t = self._compute_oscillation_frequency(history)
            is_oscillating = o_t > theta

            node_oscillations[node_id] = {
                "oscillation_frequency": round(o_t, 4),
                "is_oscillating": is_oscillating,
                "direction_changes": self._count_direction_changes(history),
            }

            if is_oscillating:
                oscillating_nodes.append(node_id)
                system_oscillation_score += o_t

        # Determine system-level suppression
        oscillation_ratio = (
            len(oscillating_nodes) / len(telemetry) if telemetry else 0
        )
        system_oscillating = oscillation_ratio > 0.2  # >20% of nodes

        # Manage hysteresis window
        if system_oscillating:
            self.consecutive_oscillations += 1
            self.suppression_active = True
            # Adaptive window: increases during repeated episodes
            self.current_window = min(
                self.max_window,
                self.base_window * (1 + self.consecutive_oscillations * 0.5)
            )
        else:
            self.consecutive_oscillations = max(0, self.consecutive_oscillations - 1)
            if self.consecutive_oscillations == 0:
                self.suppression_active = False
                self.current_window = self.base_window

        result = {
            "system_oscillation_score": round(
                system_oscillation_score / len(telemetry) if telemetry else 0, 4
            ),
            "oscillating_nodes": oscillating_nodes,
            "oscillating_node_count": len(oscillating_nodes),
            "total_nodes": len(telemetry),
            "oscillation_ratio": round(oscillation_ratio, 4),
            "suppression_active": self.suppression_active,
            "hysteresis_window_minutes": int(self.current_window),
            "consecutive_oscillation_episodes": self.consecutive_oscillations,
            "node_details": node_oscillations,
            "threshold": theta,
        }

        self.detection_history.append(result)
        return result

    def _compute_oscillation_frequency(self, history: List[Dict]) -> float:
        """
        Compute O(t) by analyzing direction changes in utilization time series.
        High frequency of direction changes = oscillation.
        """
        cpu_values = [h.get("cpu", 0.5) for h in history]
        if len(cpu_values) < 3:
            return 0.0

        # Count direction changes (sign changes in first derivative)
        diffs = np.diff(cpu_values)
        sign_changes = np.sum(np.abs(np.diff(np.sign(diffs))) > 0)

        # Normalize by number of possible changes
        max_changes = len(diffs) - 1
        if max_changes <= 0:
            return 0.0

        frequency = sign_changes / max_changes

        # Also factor in amplitude of oscillations
        amplitude = np.std(cpu_values)
        
        # Combined score: frequency * amplitude
        return float(frequency * (1 + amplitude))

    def _count_direction_changes(self, history: List[Dict]) -> int:
        """Count raw direction changes in CPU utilization."""
        cpu_values = [h.get("cpu", 0.5) for h in history]
        if len(cpu_values) < 3:
            return 0
        diffs = np.diff(cpu_values)
        return int(np.sum(np.abs(np.diff(np.sign(diffs))) > 0))

    def is_modification_allowed(self) -> bool:
        """Check if modifications are permitted (not in suppression window)."""
        return not self.suppression_active
