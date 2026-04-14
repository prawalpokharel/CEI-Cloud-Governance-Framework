"""
Scenario Loader
===============

Loads scenario datasets from the repository /scenarios directory where each
scenario is organized as separate topology, governance, and telemetry files.
Translates the split-file format into the unified shape expected by the CEI
pipeline modules (Patent Modules 101-112).

This is the integration layer between the standalone scenario test suite
(runnable via scenarios/runner.py) and the core engine's FastAPI service.
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional


# Resolve path to the scenarios directory at repository root.
# core-engine/src/scenarios/loader.py -> ../../../scenarios
SCENARIOS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "scenarios"


AVAILABLE_SCENARIOS = [
    "cloud_microservices",
    "gpu_cluster",
    "drone_swarm",
    "underwater_aps",
    "nc3_strategic_comms",
]


# Maps internal scenario IDs to user-facing metadata used by the demo UI.
# Patent section references tie each scenario to the application domain
# described in the non-provisional specification (19/641,446).
SCENARIO_METADATA = {
    "cloud_microservices": {
        "display_name": "E-Commerce Cloud Microservices",
        "description": (
            "A 15-node e-commerce platform with tier-based availability "
            "requirements and PCI-DSS constraints. Exercises the primary "
            "embodiment of the CEI framework on a dependency-dense "
            "commercial cloud workload."
        ),
        "patent_sections": ["1-6 (primary embodiment)"],
        "domain": "Commercial Cloud",
        "icon": "cloud",
    },
    "gpu_cluster": {
        "display_name": "Heterogeneous GPU Training Cluster",
        "description": (
            "A 15-node ML training infrastructure mixing A100, H100, and "
            "inference-tier accelerators under checkpoint-integrity and "
            "inference-SLO constraints. Exercises the framework on "
            "heterogeneous scheduling workloads."
        ),
        "patent_sections": ["7.4"],
        "domain": "GPU Computing",
        "icon": "chip",
    },
    "drone_swarm": {
        "display_name": "Tactical Drone Swarm",
        "description": (
            "A 14-node tactical UAV mesh with latency budgets, link "
            "redundancy requirements, and intermittent connectivity under "
            "EM jamming. Demonstrates CEI operating on a latency-critical "
            "edge mesh with adversarial conditions."
        ),
        "patent_sections": ["7.2"],
        "domain": "Edge / Tactical",
        "icon": "drone",
    },
    "underwater_aps": {
        "display_name": "Underwater Acoustic Positioning",
        "description": (
            "A 14-node GPS-denied positioning network with severe bandwidth "
            "scarcity, minimum positioning-source enforcement, and ocean "
            "noise adaptivity. Tests framework generalization to "
            "sparse-connectivity environments."
        ),
        "patent_sections": ["7.3"],
        "domain": "Subsurface / GPS-Denied",
        "icon": "waves",
    },
    "nc3_strategic_comms": {
        "display_name": "NC3 Strategic Communications",
        "description": (
            "A 17-node strategic communications network where low utilization "
            "is by design, with path-diversity requirements, crypto two-person "
            "integrity, and network segmentation. Demonstrates framework "
            "correctness on high-redundancy, low-utilization-by-design "
            "infrastructure."
        ),
        "patent_sections": ["7.1"],
        "domain": "Strategic Communications",
        "icon": "shield",
    },
}


class ScenarioLoadError(Exception):
    pass


class ScenarioLoader:
    """Loads and normalizes scenario data for ingestion by the CEI pipeline."""

    def __init__(self, scenarios_dir: Optional[Path] = None):
        self.scenarios_dir = scenarios_dir or SCENARIOS_DIR

    def list_scenarios(self) -> List[Dict[str, Any]]:
        """Return metadata for all available scenarios."""
        entries = []
        for sid in AVAILABLE_SCENARIOS:
            sdir = self.scenarios_dir / sid
            if not sdir.exists():
                continue

            meta = dict(SCENARIO_METADATA.get(sid, {}))
            meta["scenario_id"] = sid

            # Count nodes and edges without loading full telemetry
            try:
                topo = json.loads((sdir / "topology.json").read_text())
                meta["node_count"] = len(topo.get("nodes", []))
                meta["edge_count"] = len(topo.get("edges", []))
            except Exception:
                meta["node_count"] = 0
                meta["edge_count"] = 0

            # Include scenario markdown description if present
            scenario_md = sdir / "scenario.md"
            if scenario_md.exists():
                meta["has_writeup"] = True
            else:
                meta["has_writeup"] = False

            entries.append(meta)
        return entries

    def load(self, scenario_id: str) -> Dict[str, Any]:
        """
        Load a scenario and return it in the shape the core engine expects.

        Output shape:
        {
            "scenario_id": str,
            "metadata": {...},
            "topology": {"nodes": [...], "edges": [...]},
            "governance": {"tiers": {...}, "policies": [...]},
            "telemetry": {"node-id": [{"t": 0, "cpu": 0.45, "mem": 0.52}, ...]},
            "writeup": str | None
        }
        """
        if scenario_id not in AVAILABLE_SCENARIOS:
            raise ScenarioLoadError(f"Unknown scenario: {scenario_id}")

        sdir = self.scenarios_dir / scenario_id
        if not sdir.exists():
            raise ScenarioLoadError(f"Scenario directory missing: {sdir}")

        try:
            topology = json.loads((sdir / "topology.json").read_text())
            governance = json.loads((sdir / "governance.json").read_text())
            telemetry = json.loads((sdir / "telemetry.json").read_text())
        except FileNotFoundError as e:
            raise ScenarioLoadError(f"Missing scenario file: {e}")
        except json.JSONDecodeError as e:
            raise ScenarioLoadError(f"Invalid JSON in scenario: {e}")

        writeup = None
        scenario_md = sdir / "scenario.md"
        if scenario_md.exists():
            writeup = scenario_md.read_text()

        return {
            "scenario_id": scenario_id,
            "metadata": SCENARIO_METADATA.get(scenario_id, {}),
            "topology": topology,
            "governance": governance,
            "telemetry": telemetry,
            "writeup": writeup,
        }

    def to_core_engine_format(self, scenario: Dict[str, Any]) -> Dict[str, Any]:
        """
        Translate the split-file scenario format into the unified request
        shape used by the /analyze endpoint (Patent Modules 101-112 pipeline).

        The core engine expects nodes with embedded metrics and history, plus
        edges as dict objects rather than source/target tuples.
        """
        topology = scenario["topology"]
        telemetry = scenario["telemetry"]
        governance = scenario["governance"]

        # Build nodes with merged telemetry data
        nodes = []
        for n in topology["nodes"]:
            nid = n["id"]
            node_telemetry = telemetry.get(nid, [])

            # Extract most recent metrics (last point in time series)
            if node_telemetry:
                latest = node_telemetry[-1]
                current_metrics = {
                    "cpu_utilization": latest.get("cpu", 0) * 100,
                    "memory_utilization": latest.get("mem", 0) * 100,
                    "network_throughput": 0,
                    "disk_io": 0,
                }
            else:
                current_metrics = {
                    "cpu_utilization": 0,
                    "memory_utilization": 0,
                    "network_throughput": 0,
                    "disk_io": 0,
                }

            nodes.append({
                "node_id": nid,
                "metrics": current_metrics,
                "metadata": {
                    "tier": n.get("tier", "supporting"),
                    "type": n.get("type", "service"),
                    "region": n.get("region", "unknown"),
                    "replicas": n.get("replicas", 1),
                    **{k: v for k, v in n.items()
                       if k not in ("id", "tier", "type", "region", "replicas")},
                },
                "utilization_history": node_telemetry,
            })

        # Normalize edges from [src, dst] tuples to {source, target, weight} dicts
        edges = []
        for e in topology.get("edges", []):
            if isinstance(e, list) and len(e) >= 2:
                edges.append({
                    "source": e[0],
                    "target": e[1],
                    "weight": e[2] if len(e) > 2 else 1.0,
                    "type": "dependency",
                })
            elif isinstance(e, dict):
                edges.append(e)

        # Translate governance tiers and policies into policy dictionary format
        policies = {}
        for tier_name, tier_def in governance.get("tiers", {}).items():
            policies[f"tier_{tier_name}"] = {
                "description": f"Tier: {tier_name}",
                "constraints": tier_def,
                "applies_to_tier": tier_name,
            }
        for policy in governance.get("policies", []):
            pid = policy.get("id", f"policy_{len(policies)}")
            policies[pid] = policy

        return {
            "nodes": nodes,
            "edges": edges,
            "governance_policies": policies,
        }
