"""
Patent Module 101: Data Collection Module
Gathers telemetry from a distributed computing environment (102) including
resource utilization metrics, dependency relationships, and configuration parameters.
"""
from typing import List, Dict, Any
import hashlib
import numpy as np
from datetime import datetime


def _stable_seed(node_id: str) -> int:
    """
    Derive a stable 32-bit seed from a node id.

    Python's built-in hash() is salted per process (PYTHONHASHSEED), so it
    cannot be used for anything that must reproduce across runs.
    """
    digest = hashlib.sha256(node_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


class DataCollector:
    """
    Collects and normalizes telemetry data from cloud infrastructure nodes.
    Implements Patent Reference 101: Data collection module that gathers
    telemetry from a distributed computing environment.
    
    Supports standardized telemetry across AWS, Azure, Google Cloud, and
    Kubernetes environments (platform-agnostic design per Section IX of paper).
    """

    REQUIRED_METRICS = ["cpu_utilization", "memory_utilization"]
    OPTIONAL_METRICS = [
        "storage_io", "network_throughput", "request_rate",
        "error_rate", "latency_p99", "access_frequency"
    ]

    def __init__(self):
        self.collection_history = []
        self.collection_timestamp = None

    def collect(self, raw_nodes: List[Dict]) -> List[Dict[str, Any]]:
        """
        Collect and validate telemetry from raw node data.
        Normalizes metrics to [0, 1] range for CEI computation.
        Reconstructs sustained workload behavior through longitudinal
        telemetry analysis (per paper Section VIII).
        """
        self.collection_timestamp = datetime.utcnow().isoformat()
        processed_nodes = []

        for node in raw_nodes:
            processed = self._process_node(node)
            if processed:
                processed_nodes.append(processed)

        self.collection_history.append({
            "timestamp": self.collection_timestamp,
            "node_count": len(processed_nodes)
        })

        return processed_nodes

    def _process_node(self, node: Dict) -> Dict[str, Any]:
        """Process and validate a single node's telemetry."""
        node_id = node.get("node_id") or node.get("id")
        if not node_id:
            return None

        metrics = node.get("metrics", {})

        # Extract and normalize core metrics
        cpu = self._normalize(metrics.get("cpu_utilization", 0), 0, 100)
        memory = self._normalize(metrics.get("memory_utilization", 0), 0, 100)
        storage_io = self._normalize(metrics.get("storage_io", 0), 0, 100)
        network = self._normalize(metrics.get("network_throughput", 0), 0, 1000)
        request_rate = metrics.get("request_rate", 0)
        error_rate = self._normalize(metrics.get("error_rate", 0), 0, 100)
        latency_p99 = metrics.get("latency_p99", 0)

        # Extract utilization history for longitudinal analysis.
        #
        # History is accepted from either the node's top level or nested under
        # "metrics". Both shapes occur in practice: ScenarioLoader and the
        # backend's cloud discovery put it at the top level, while callers
        # posting directly to /analyze may nest it. Previously only the nested
        # location was read, so every top-level caller silently fell through
        # to the synthetic generator and had its real telemetry discarded.
        raw_history = (
            node.get("utilization_history")
            or metrics.get("utilization_history")
            or []
        )
        utilization_history = self._normalize_history(raw_history)

        if not utilization_history:
            # Only when no history was supplied at all. Seeded per node id so
            # a given node yields the same history on every run and regardless
            # of the order nodes are processed in.
            utilization_history = self._generate_synthetic_history(
                cpu, memory, node_id
            )

        # Extract metadata
        provider = node.get("provider", "unknown")
        region = node.get("region", "unknown")
        instance_type = node.get("instance_type", "unknown")
        monthly_cost = node.get("monthly_cost", 0.0)
        tags = node.get("tags", {})

        return {
            "node_id": node_id,
            "metrics": {
                "cpu_utilization": cpu,
                "memory_utilization": memory,
                "storage_io": storage_io,
                "network_throughput": network,
                "request_rate": request_rate,
                "error_rate": error_rate,
                "latency_p99": latency_p99,
            },
            "utilization_history": utilization_history,
            "metadata": {
                "provider": provider,
                "region": region,
                "instance_type": instance_type,
                "monthly_cost": monthly_cost,
                "tags": tags,
            },
            "collection_timestamp": self.collection_timestamp,
        }

    def _normalize(self, value: float, min_val: float, max_val: float) -> float:
        """Normalize a metric to [0, 1] range."""
        if max_val <= min_val:
            return 0.0
        return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))

    # Downstream consumers (CEI entropy, stability monitor, oscillation
    # detector) all read "cpu" and "memory" from each history point. Callers
    # supply several spellings: scenario telemetry uses {"t", "cpu", "mem"},
    # the synthetic generator emits {"day", "cpu", "memory"}, and cloud
    # discovery uses {"t", "cpu", "mem"}. Without normalization, "mem" is
    # never seen and every memory reading silently defaults to 0.5 -- a flat
    # constant, which reads as perfectly stable and zero-entropy.
    _CPU_KEYS = ("cpu", "cpu_utilization", "cpu_pct")
    _MEM_KEYS = ("memory", "mem", "memory_utilization", "mem_pct")

    def _normalize_history(self, raw_history: List[Dict]) -> List[Dict]:
        """
        Coerce supplied history into the canonical {"cpu", "memory"} shape.

        Values are assumed to be fractions in [0, 1]; a caller supplying
        percentages (0-100) is detected and rescaled, since mixing the two
        would put one node's entropy on a completely different scale from
        its neighbours'.
        """
        if not isinstance(raw_history, list) or not raw_history:
            return []

        points = []
        for entry in raw_history:
            if not isinstance(entry, dict):
                continue
            cpu = self._first_present(entry, self._CPU_KEYS)
            mem = self._first_present(entry, self._MEM_KEYS)
            if cpu is None and mem is None:
                continue
            point = {
                "cpu": cpu if cpu is not None else 0.0,
                "memory": mem if mem is not None else 0.0,
            }
            # Preserve whichever time index the caller used, for the UI.
            for time_key in ("t", "day", "timestamp", "ts"):
                if time_key in entry:
                    point[time_key] = entry[time_key]
                    break
            points.append(point)

        if not points:
            return []

        # Rescale if the caller supplied percentages rather than fractions.
        peak = max(max(p["cpu"], p["memory"]) for p in points)
        if peak > 1.0:
            scale = 100.0 if peak <= 100.0 else peak
            for p in points:
                p["cpu"] = p["cpu"] / scale
                p["memory"] = p["memory"] / scale

        for p in points:
            p["cpu"] = max(0.0, min(1.0, float(p["cpu"])))
            p["memory"] = max(0.0, min(1.0, float(p["memory"])))

        return points

    @staticmethod
    def _first_present(entry: Dict, keys) -> float | None:
        for key in keys:
            value = entry.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    def _generate_synthetic_history(
        self, cpu: float, memory: float, node_id: str = ""
    ) -> List[Dict]:
        """
        Generate synthetic utilization history when longitudinal data
        is not available. Uses Gaussian noise around current metrics.

        Deterministic: the generator is seeded from the node id, so repeated
        analyses of the same topology return identical results. Previously
        this drew from numpy's global RNG, which made every response to an
        identical request different from the last.
        """
        rng = np.random.default_rng(_stable_seed(node_id))
        history = []
        for i in range(90):  # 90-day window per paper Section VIII
            cpu_sample = max(0, min(1, cpu + rng.normal(0, 0.05)))
            mem_sample = max(0, min(1, memory + rng.normal(0, 0.05)))
            history.append({
                "day": i,
                "cpu": float(cpu_sample),
                "memory": float(mem_sample)
            })
        return history
