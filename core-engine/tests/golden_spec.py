"""
Golden-file request matrix for the NIW / patent demonstration surface.

WHY THIS EXISTS
---------------
The scenario, pricing, and benchmark endpoints are the evidence surface for
USPTO App. No. 19/641,446 and the pending NIW petition. They are reviewed by
people outside this repo, and their numbers must not move silently while the
Phase 1 agent work reshapes the core engine around them.

This module defines one canonical request matrix, shared by:

    tests/regen_golden.py   -- writes tests/golden/*.json
    tests/test_niw_golden.py -- asserts live responses still match

IMPORTANT: golden files record CURRENT behavior, including known defects.
They are a regression harness, not a statement that the numbers are correct.
See tests/golden/README.md for the defects known at capture time.

COLDNESS
--------
Every case is captured against a freshly-reloaded application module. The
patent modules (101-112) are currently instantiated at import time and carry
mutating state across requests, so a warm process returns different numbers
than a cold one. Capturing cold pins the values a reviewer actually sees on a
freshly-deployed Railway instance, and is also the behavior the Week 0
statelessness fix converges on -- so these goldens should survive that fix
unchanged. That is precisely the assertion we want.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

SCENARIO_IDS: List[str] = [
    "cloud_microservices",
    "gpu_cluster",
    "drone_swarm",
    "underwater_aps",
    "nc3_strategic_comms",
]


# --------------------------------------------------------------------------
# Cold client construction
# --------------------------------------------------------------------------

def cold_client():
    """
    Return a TestClient bound to a freshly-imported application module.

    Reloading resets the import-time singletons in src.main. Once those are
    constructed per-request this reload becomes a no-op, and the goldens keep
    passing -- which is how we prove the refactor changed nothing else.
    """
    from fastapi.testclient import TestClient

    import src.main as main_module

    importlib.reload(main_module)
    return TestClient(main_module.app)


# --------------------------------------------------------------------------
# Payload normalization
# --------------------------------------------------------------------------

def _digest(obj: Any) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def compact(payload: Any) -> Any:
    """
    Replace bulk telemetry with a digest.

    A single scenario's telemetry.json is ~15k lines; five of them inlined
    would make the golden files unreviewable and bury real diffs. The digest
    still fails the test if the underlying data changes, while keeping the
    committed files small enough to read in a pull request.
    """
    if not isinstance(payload, dict):
        return payload

    out = dict(payload)

    if isinstance(out.get("telemetry"), dict):
        telemetry = out["telemetry"]
        out["telemetry"] = {
            "__digest__": _digest(telemetry),
            "__series_count__": len(telemetry),
            "__point_count__": sum(
                len(v) for v in telemetry.values() if isinstance(v, list)
            ),
        }

    if isinstance(out.get("writeup"), str):
        out["writeup"] = {
            "__digest__": _digest(out["writeup"]),
            "__chars__": len(out["writeup"]),
        }

    return out


# --------------------------------------------------------------------------
# Request matrix
# --------------------------------------------------------------------------
#
# Each case is (name, fn) where fn(client) -> response JSON. Cases that need
# an analysis result run /analyze first, exactly as the demo page does before
# it calls /pricing/savings and /benchmark/hpa-vs-cei.


def _demo_topology_nodes(scenario: Dict) -> List[Dict]:
    """
    Reproduce the node projection the demo page performs client-side.

    Mirrors frontend/src/pages/demo/[scenario].js. Scenario topologies carry
    no instance_type, so the key is omitted here just as JSON.stringify drops
    an undefined value -- which is what makes the server fall back to
    _default_instance(provider, tier).
    """
    nodes = []
    for n in scenario.get("topology", {}).get("nodes", []):
        node = {
            "id": n["id"],
            "provider": n.get("provider", "aws"),
            "replicas": n.get("replicas", 1),
            "tier": n.get("tier", "supporting"),
        }
        if n.get("instance_type") is not None:
            node["instance_type"] = n["instance_type"]
        nodes.append(node)
    return nodes


def _case_get(path: str) -> Callable:
    def run(client):
        r = client.get(path)
        r.raise_for_status()
        return r.json()

    return run


def _case_post(path: str, body: Optional[Dict] = None) -> Callable:
    def run(client):
        r = client.post(path, json=body) if body is not None else client.post(path)
        r.raise_for_status()
        return r.json()

    return run


def _case_pricing_savings(scenario_id: str) -> Callable:
    def run(client):
        scenario = client.get(f"/scenarios/{scenario_id}").json()
        analysis = client.post(f"/scenarios/{scenario_id}/analyze").json()["analysis"]
        r = client.post(
            "/pricing/savings",
            json={
                "nodes": _demo_topology_nodes(scenario),
                "analysis_nodes": analysis["nodes"],
                "governance": scenario.get("governance"),
            },
        )
        r.raise_for_status()
        return r.json()

    return run


def _case_hpa_vs_cei(scenario_id: str) -> Callable:
    def run(client):
        scenario = client.get(f"/scenarios/{scenario_id}").json()
        analysis = client.post(f"/scenarios/{scenario_id}/analyze").json()["analysis"]
        r = client.post(
            "/benchmark/hpa-vs-cei",
            json={
                "nodes": _demo_topology_nodes(scenario),
                "edges": scenario.get("topology", {}).get("edges", []),
                "analysis_nodes": analysis["nodes"],
                "oscillation_status": analysis["oscillation_status"],
                "governance": scenario.get("governance"),
            },
        )
        r.raise_for_status()
        return r.json()

    return run


def iter_cases() -> Iterator[Tuple[str, Callable]]:
    """Yield (case_name, runner) for the full NIW surface."""
    yield "health", _case_get("/health")
    yield "scenarios_list", _case_get("/scenarios/list")
    yield "pricing_providers", _case_get("/pricing/providers")

    # A representative slice of the pricing table. Guards the cost constants
    # that every savings figure on the demo pages is derived from.
    for provider, instance in [
        ("aws", "m5.xlarge"),
        ("aws", "t3.medium"),
        ("azure", "Standard_D4s_v3"),
        ("gcp", "n2-standard-4"),
    ]:
        yield (
            f"pricing_instance_{provider}_{instance}",
            _case_get(f"/pricing/instance/{provider}/{instance}?replicas=3"),
        )

    for sid in SCENARIO_IDS:
        yield f"scenario_{sid}", _case_get(f"/scenarios/{sid}")
        yield f"analyze_{sid}", _case_post(f"/scenarios/{sid}/analyze")
        yield f"scenario_benchmark_{sid}", _case_post(f"/scenarios/{sid}/benchmark")
        yield f"pricing_savings_{sid}", _case_pricing_savings(sid)
        yield f"hpa_vs_cei_{sid}", _case_hpa_vs_cei(sid)


def capture(name: str, runner: Callable) -> Any:
    """Run one case against a cold client and return its normalized payload."""
    client = cold_client()
    return compact(runner(client))


def case_names() -> List[str]:
    return [name for name, _ in iter_cases()]
