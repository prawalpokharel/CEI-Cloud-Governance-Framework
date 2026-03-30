"""
Patent Module 112: Rollback Manager
Snapshots state prior to modification and reverts upon anomaly detection.
Patent Section 6 / Paper Section VII.

"After execution, it monitors KPIs (e.g., P99 latency, error rate) for a
validation window V. If KPIs deviate from the baseline by a threshold
percentage, the manager automatically reverts to the snapshot."
"""
import uuid
import copy
from datetime import datetime
from typing import Dict, Any, Optional


class RollbackManager:
    """
    Manages pre-modification snapshots and automated anomaly-triggered reverts.
    Patent: "a rollback manager 112 manages snapshots and anomaly-triggered reverts"
    """

    # KPI deviation thresholds for automatic rollback
    DEFAULT_LATENCY_THRESHOLD = 0.20   # 20% deviation triggers rollback
    DEFAULT_ERROR_RATE_THRESHOLD = 0.10  # 10% increase triggers rollback
    VALIDATION_WINDOW_SECONDS = 300     # 5-minute monitoring window

    def __init__(self):
        self.snapshots = {}
        self.rollback_history = []

    def create_snapshot(self, configuration: Dict[str, Any]) -> str:
        """
        Create a pre-modification snapshot of current configuration and KPI baseline.
        Called before any actuator execution.
        """
        snapshot_id = f"snap_{uuid.uuid4().hex[:12]}"
        self.snapshots[snapshot_id] = {
            "id": snapshot_id,
            "timestamp": datetime.utcnow().isoformat(),
            "configuration": copy.deepcopy(configuration),
            "baseline_kpis": configuration.get("current_kpis", {}),
            "status": "active",
        }
        return snapshot_id

    def check_kpis(
        self,
        snapshot_id: str,
        current_kpis: Dict[str, float]
    ) -> Dict[str, Any]:
        """
        Compare current KPIs against baseline snapshot.
        Returns whether rollback is recommended.
        """
        snapshot = self.snapshots.get(snapshot_id)
        if not snapshot:
            return {"error": "Snapshot not found", "rollback_recommended": False}

        baseline = snapshot.get("baseline_kpis", {})
        deviations = {}
        rollback_recommended = False

        # Check P99 latency deviation
        baseline_latency = baseline.get("latency_p99", 0)
        current_latency = current_kpis.get("latency_p99", 0)
        if baseline_latency > 0:
            latency_dev = (current_latency - baseline_latency) / baseline_latency
            deviations["latency_p99"] = round(latency_dev, 4)
            if latency_dev > self.DEFAULT_LATENCY_THRESHOLD:
                rollback_recommended = True

        # Check error rate deviation
        baseline_errors = baseline.get("error_rate", 0)
        current_errors = current_kpis.get("error_rate", 0)
        if baseline_errors > 0:
            error_dev = (current_errors - baseline_errors) / baseline_errors
            deviations["error_rate"] = round(error_dev, 4)
            if error_dev > self.DEFAULT_ERROR_RATE_THRESHOLD:
                rollback_recommended = True
        elif current_errors > 0.05:
            deviations["error_rate"] = current_errors
            rollback_recommended = True

        return {
            "snapshot_id": snapshot_id,
            "deviations": deviations,
            "rollback_recommended": rollback_recommended,
            "thresholds": {
                "latency_p99": self.DEFAULT_LATENCY_THRESHOLD,
                "error_rate": self.DEFAULT_ERROR_RATE_THRESHOLD,
            }
        }

    def revert(self, snapshot_id: str) -> Dict[str, Any]:
        """
        Revert to a previous snapshot configuration.
        In production, this would re-apply the saved configuration via cloud APIs.
        """
        snapshot = self.snapshots.get(snapshot_id)
        if not snapshot:
            return {"error": "Snapshot not found", "reverted": False}

        if snapshot["status"] != "active":
            return {"error": "Snapshot already consumed", "reverted": False}

        snapshot["status"] = "reverted"
        self.rollback_history.append({
            "snapshot_id": snapshot_id,
            "reverted_at": datetime.utcnow().isoformat(),
            "original_timestamp": snapshot["timestamp"],
        })

        return {
            "reverted": True,
            "snapshot_id": snapshot_id,
            "restored_configuration": snapshot["configuration"],
            "message": "Configuration reverted to pre-modification state",
        }

    def get_snapshot(self, snapshot_id: str) -> Optional[Dict]:
        return self.snapshots.get(snapshot_id)

    def list_snapshots(self) -> list:
        return [
            {"id": s["id"], "timestamp": s["timestamp"], "status": s["status"]}
            for s in self.snapshots.values()
        ]
