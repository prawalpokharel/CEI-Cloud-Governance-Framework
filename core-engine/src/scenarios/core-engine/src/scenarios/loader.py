"""
Scenario Loader
===============

Loads scenario datasets from the /scenarios directory where each scenario
is organized as separate topology, governance, and telemetry files.
Translates the split-file format into the unified shape expected by the
CEI pipeline modules (Patent Modules 101-112).
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional


def _find_scenarios_dir():
    """Locate the scenarios directory whether running from repo root
    (development) or from the core-engine service (Railway deployment)."""
    here = Path(__file__).resolve()
    # Production layout: core-engine/scenarios/
    candidate = here.parent.parent.parent / "scenarios"
    if candidate.exists():
        return candidate
    # Development layout: <repo-root>/scenarios/
    candidate = here.parent.parent.parent.parent / "scenarios"
    if candidate.exists():
        return candidate
    # Fall back so error is clear if neither exists
    return here.parent.parent.parent.parent / "scenarios"


SCENARIOS_DIR = _find_scenarios_dir()


AVAILABLE_SCENARIOS = [
    "cloud_microservices",
    "gpu_cluster",
    "drone_swarm",
    "underwater_aps",
    "nc3_strategic_comms",
]


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

            try:
                topo = json.loads((sdir / "topology.json").read_text())
                meta["node_count"] = len(topo.get("nodes", []))
                meta["edge_count"] = len(topo.get("edges", []))
            except Exception:
                meta["node_count"] = 0
                meta["edge_count"] = 0

            scenario_md = sdir / "scenario.md"
            meta["has_writeup"] = scenario_md.exists()

            entries.append(meta)
        return entries

    def load(self, scenario_id: str) -> Dict[str, Any]:
        """Load a scenario and return it in the shape the core engine expects."""
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
        """Translate split-file format into the unified request shape
        used by the /analyze endpoint (Patent Modules 101-112 pipeline)."""
        topology = scenario["topology"]
        telemetry = scenario["telemetry"]
        governance = scenario["governance"]

        nodes = []
        for n in topology["nodes"]:
            nid = n["id"]
            node_telemetry = telemetry.get(nid, [])

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
