"""
Patent Module 101: Data Collection Module
Gathers telemetry from a distributed computing environment (102) including
resource utilization metrics, dependency relationships, and configuration parameters.
"""
from typing import List, Dict, Any
import numpy as np
from datetime import datetime


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

        # Extract utilization history for longitudinal analysis
        utilization_history = metrics.get("utilization_history", [])
        if not utilization_history:
            # Generate synthetic history from current metrics for analysis
            utilization_history = self._generate_synthetic_history(cpu, memory)

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

    def _generate_synthetic_history(self, cpu: float, memory: float) -> List[Dict]:
        """
        Generate synthetic utilization history when longitudinal data
        is not available. Uses Gaussian noise around current metrics.
        """
        history = []
        for i in range(90):  # 90-day window per paper Section VIII
            cpu_sample = max(0, min(1, cpu + np.random.normal(0, 0.05)))
            mem_sample = max(0, min(1, memory + np.random.normal(0, 0.05)))
            history.append({
                "day": i,
                "cpu": float(cpu_sample),
                "memory": float(mem_sample)
            })
        return history
