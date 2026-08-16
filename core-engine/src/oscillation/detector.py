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

    # Smallest utilization reversal treated as a real scaling event rather
    # than measurement noise, expressed as a fraction of full utilization.
    # A workload wobbling by 3 points is not thrashing; one swinging by 30 is.
    #
    # Calibrated at 0.10 against synthetic series: white noise up to
    # sigma=0.05 -- ordinary jitter for a steady pod -- stays below the
    # threshold, while sigma=0.10 and genuine square-wave thrashing are
    # flagged. A smaller deadband re-flags stable-but-noisy workloads, which
    # is the failure mode this replaces.
    DEFAULT_MIN_AMPLITUDE = 0.10

    # Peak-to-trough swing treated as full-amplitude oscillation. Reversals
    # of this size or larger contribute their full weight to O(t).
    DEFAULT_REFERENCE_AMPLITUDE = 0.25

    def __init__(
        self,
        default_threshold: float = 0.3,
        base_window_minutes: int = 15,
        max_window_minutes: int = 60,
        min_amplitude: float = DEFAULT_MIN_AMPLITUDE,
        reference_amplitude: float = DEFAULT_REFERENCE_AMPLITUDE,
    ):
        self.default_threshold = default_threshold
        self.base_window = base_window_minutes
        self.max_window = max_window_minutes
        self.min_amplitude = min_amplitude
        self.reference_amplitude = reference_amplitude
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

    def _find_pivots(self, values: List[float]) -> List[float]:
        """
        Reduce a series to its significant turning points.

        Standard deadband (zigzag) reduction: a reversal is registered only
        once the series has moved back from its running extreme by at least
        ``min_amplitude``. Fluctuations smaller than that never create a
        pivot, so sensor noise is filtered out rather than counted.
        """
        if len(values) < 2:
            return list(values)

        pivots: List[float] = []
        trend = 0          # 0 undetermined, +1 rising, -1 falling
        hi = lo = values[0]
        ext = values[0]

        for v in values[1:]:
            if trend == 0:
                hi, lo = max(hi, v), min(lo, v)
                if v - lo >= self.min_amplitude:
                    trend, ext = 1, v
                    pivots.append(lo)
                elif hi - v >= self.min_amplitude:
                    trend, ext = -1, v
                    pivots.append(hi)
            elif trend > 0:
                if v > ext:
                    ext = v
                elif ext - v >= self.min_amplitude:
                    pivots.append(ext)
                    trend, ext = -1, v
            else:
                if v < ext:
                    ext = v
                elif v - ext >= self.min_amplitude:
                    pivots.append(ext)
                    trend, ext = 1, v

        pivots.append(ext)
        return pivots

    def _compute_oscillation_frequency(self, history: List[Dict]) -> float:
        """
        Compute O(t) from significant reversals in the utilization series.

        Oscillation requires BOTH frequent reversals AND meaningful amplitude:

            O(t) = reversal_rate * amplitude_factor

        The previous formulation counted every sign change in the first
        derivative and used amplitude only as an additive bonus
        (``frequency * (1 + std)``). Because a noisy series reverses on
        roughly half its samples regardless of how small the noise is, that
        scored ~0.5-0.7 for essentially any real telemetry -- a series with
        sigma=0.001 scored *higher* than one with sigma=0.05. Only perfectly
        flat or perfectly monotonic input scored zero, so in practice every
        node was flagged, suppression was permanently active, and every
        recommendation collapsed to no_action.
        """
        cpu_values = [h.get("cpu", 0.5) for h in history]
        if len(cpu_values) < 3:
            return 0.0

        pivots = self._find_pivots(cpu_values)

        # Interior pivots are the reversals; the first and last are endpoints.
        reversals = max(0, len(pivots) - 2)
        if reversals == 0:
            return 0.0

        # A reversal needs at least two samples, bounding how many can occur.
        max_reversals = max(1, (len(cpu_values) - 1) // 2)
        reversal_rate = min(1.0, reversals / max_reversals)

        swings = [abs(b - a) for a, b in zip(pivots, pivots[1:])]
        mean_swing = float(np.mean(swings)) if swings else 0.0
        amplitude_factor = min(1.0, mean_swing / self.reference_amplitude)

        return float(reversal_rate * amplitude_factor)

    def _count_direction_changes(self, history: List[Dict]) -> int:
        """
        Count significant direction changes in CPU utilization.

        Uses the same deadband as O(t), so this diagnostic agrees with the
        decision rather than reporting raw noise crossings alongside it.
        """
        cpu_values = [h.get("cpu", 0.5) for h in history]
        if len(cpu_values) < 3:
            return 0
        return max(0, len(self._find_pivots(cpu_values)) - 2)

    def is_modification_allowed(self) -> bool:
        """Check if modifications are permitted (not in suppression window)."""
        return not self.suppression_active
