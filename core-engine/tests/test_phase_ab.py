"""
Phase A/B services: recovery amplification, fleet convergence, correlated
availability, control-plane audit, and the chaos extensions.

Grouped in one file because they share fixtures and each is tested at the
level that matters: the property the module exists to hold, not its plumbing.
"""

import pytest

from src.services import availability, control_plane, fleet, recovery
from src.services.chaos import ExperimentResult, calibrate_edges, measure_recovery


def _workload(name, *, ns="prod", kind="Deployment", replicas=2, ready=2,
              images=("repo/app@sha256:abc",), cpu_pod=0.5, mem_pod=512 * 1024**2):
    return {
        "key": f"{ns}/{kind}/{name}", "name": name, "namespace": ns,
        "kind": kind, "replicas_desired": replicas, "replicas_ready": ready,
        "pod_labels": {"app": name}, "labels": {"app": name},
        "images": list(images),
        "cpu_cores_requested": cpu_pod * replicas,
        "memory_bytes_requested": mem_pod * replicas,
        "cpu_cores_requested_per_pod": cpu_pod,
        "memory_bytes_requested_per_pod": mem_pod,
        "config_refs": {"config_maps": [], "secrets": []},
    }


def _edge(src, dst, ns="prod", kind="env_reference", confidence=0.9):
    return {"source": f"{ns}/Deployment/{src}", "target": f"{ns}/Deployment/{dst}",
            "confidence": confidence, "source_kind": kind}


def _node(name="n1", cpu=16.0, mem=64 * 1024**3):
    return {"name": name, "ready": True, "unschedulable": False,
            "allocatable_cpu_cores": cpu, "allocatable_memory_bytes": mem}


def _hpa(target, *, ns="prod", minimum=2, maximum=4, current=2, metrics=None):
    return {"name": f"{target}-hpa", "namespace": ns, "target_kind": "Deployment",
            "target_name": target, "min_replicas": minimum, "max_replicas": maximum,
            "current_replicas": current,
            "metrics": metrics if metrics is not None else [{"type": "Resource", "resource": "cpu"}]}


def _kinds(result):
    return {f["kind"] for f in result["findings"]}


def _find(result, kind):
    return next(f for f in result["findings"] if f["kind"] == kind)


# ═══ recovery amplification ══════════════════════════════════════════════


def test_reconnect_storm_scales_with_dependent_pods():
    """Fifty replicas across five dependents is fifty reconnections, not five."""
    snapshot = {
        "workloads": [_workload("db", kind="StatefulSet")]
        + [_workload(f"svc{i}", replicas=10) for i in range(5)],
        "edges": [
            {"source": f"prod/Deployment/svc{i}", "target": "prod/StatefulSet/db",
             "confidence": 0.9, "source_kind": "env_reference"}
            for i in range(5)
        ],
        "autoscalers": [], "pods": [], "nodes": [],
    }
    amp = recovery.reconnect_amplification(snapshot, "prod/StatefulSet/db")

    assert amp["reconnecting_pods"] == 50
    assert amp["stateful_target"] is True
    assert amp["amplification"] == 100.0  # stateful factor


def test_reconnect_storm_finding_fires_on_load_bearing_stateful():
    snapshot = {
        "workloads": [_workload("db", kind="StatefulSet")]
        + [_workload(f"svc{i}", replicas=8) for i in range(4)],
        "edges": [_edge(f"svc{i}", "db", kind="env_reference") for i in range(4)]
        + [{"source": "prod/Deployment/svc0", "target": "prod/StatefulSet/db",
            "confidence": 0.9, "source_kind": "env_reference"}],
        "autoscalers": [], "pods": [], "nodes": [],
    }
    # Point the edges at the StatefulSet key.
    snapshot["edges"] = [
        {"source": f"prod/Deployment/svc{i}", "target": "prod/StatefulSet/db",
         "confidence": 0.9, "source_kind": "env_reference"}
        for i in range(4)
    ]
    finding = _find(recovery.analyze(snapshot), "reconnect_storm_risk")

    assert "32 simultaneous reconnections" in finding["title"]
    assert "fixed capacity" in finding["detail"]


def test_small_fan_in_is_not_a_storm():
    snapshot = {
        "workloads": [_workload("db"), _workload("api", replicas=2)],
        "edges": [_edge("api", "db")],
        "autoscalers": [], "pods": [], "nodes": [],
    }
    assert "reconnect_storm_risk" not in _kinds(recovery.analyze(snapshot))


def test_cpu_only_hpa_on_dependent_is_lagging_signal():
    snapshot = {
        "workloads": [_workload("db"), _workload("api")],
        "edges": [_edge("api", "db")],
        "autoscalers": [_hpa("api")],
        "pods": [], "nodes": [],
    }
    finding = _find(recovery.analyze(snapshot), "hpa_lagging_signal")

    assert finding["workload_key"] == "prod/Deployment/api"
    assert "one step behind" in finding["detail"]


def test_empty_hpa_metrics_means_kubernetes_default_cpu():
    snapshot = {
        "workloads": [_workload("db"), _workload("api")],
        "edges": [_edge("api", "db")],
        "autoscalers": [_hpa("api", metrics=[])],
        "pods": [], "nodes": [],
    }
    assert "hpa_lagging_signal" in _kinds(recovery.analyze(snapshot))


def test_external_metric_hpa_is_not_flagged():
    """A queue-depth metric leads the load; the critique does not apply."""
    snapshot = {
        "workloads": [_workload("db"), _workload("api")],
        "edges": [_edge("api", "db")],
        "autoscalers": [_hpa("api", metrics=[{"type": "External"}])],
        "pods": [], "nodes": [],
    }
    assert "hpa_lagging_signal" not in _kinds(recovery.analyze(snapshot))


def test_hpa_on_workload_without_upstreams_is_not_flagged():
    snapshot = {
        "workloads": [_workload("db"), _workload("api")],
        "edges": [_edge("api", "db")],
        "autoscalers": [_hpa("db")],
        "pods": [], "nodes": [],
    }
    assert "hpa_lagging_signal" not in _kinds(recovery.analyze(snapshot))


# ═══ fleet convergence ═══════════════════════════════════════════════════


def _cluster(name, provider, workloads=None, egress_endpoints=None):
    snapshot = {
        "workloads": workloads if workloads is not None else [
            _workload("api", images=("ghcr.io/acme/api@sha256:abc",)),
        ],
        "edges": [],
    }
    if egress_endpoints:
        snapshot["egress"] = {
            "available": True,
            "workloads": {
                "prod/Deployment/api": [
                    {"destination": e, "ports": [443], "dns_resolved": True,
                     "flow_count": 5, "dropped_count": 0, "ips": []}
                    for e in egress_endpoints
                ]
            },
        }
    return {"name": name, "provider": provider, "snapshot": snapshot}


def test_cross_provider_identity_share_is_critical():
    """The October 2025 case: two providers, one identity tenant."""
    result = fleet.analyze([
        _cluster("eks-prod", "eks", egress_endpoints=["tenant.auth0.com"]),
        _cluster("aks-prod", "aks", egress_endpoints=["tenant.auth0.com"]),
    ])
    finding = _find(result, "fleet_shared_dependency")

    assert finding["severity"] == "critical"
    assert "across providers" in finding["title"]
    assert "one failure domain wearing two logos" in finding["detail"]
    # auth0 (from egress) and ghcr.io (from images) are both genuinely
    # shared across providers; the identity one carries the critical.
    assert result["summary"]["cross_provider_shared"] == 2


def test_same_provider_share_is_not_cross_provider():
    result = fleet.analyze([
        _cluster("a", "eks", egress_endpoints=["tenant.auth0.com"]),
        _cluster("b", "eks", egress_endpoints=["tenant.auth0.com"]),
    ])
    finding = _find(result, "fleet_shared_dependency")

    assert finding["evidence"]["cross_provider"] is False
    assert finding["severity"] == "warning"  # identity weight still elevates


def test_registry_convergence_is_found_without_egress():
    """The one dependency visible from images alone."""
    result = fleet.analyze([
        _cluster("a", "eks"), _cluster("b", "aks"),
    ])
    shared = {d["endpoint"] for d in result["shared_dependencies"]}

    assert "ghcr.io" in shared
    finding = _find(result, "fleet_shared_dependency")
    assert "image pull" in finding["detail"]
    assert "The real overlap is at least" in result["summary"]["note"]


def test_bare_image_name_is_docker_hub():
    result = fleet.analyze([
        _cluster("a", "eks", workloads=[_workload("w", images=("nginx:1.25",))]),
        _cluster("b", "aks", workloads=[_workload("w", images=("nginx:1.25",))]),
    ])
    assert "docker.io" in {d["endpoint"] for d in result["shared_dependencies"]}


def test_single_cluster_fleet_is_unavailable():
    result = fleet.analyze([_cluster("only", "eks")])

    assert result["available"] is False
    assert "at least two clusters" in result["reason"]


def test_independence_score_weighted_by_category():
    """Sharing an identity provider costs more than sharing a registry."""
    a = _cluster("a", "eks", egress_endpoints=["tenant.auth0.com", "a-only.example.com"])
    b = _cluster("b", "aks", egress_endpoints=["tenant.auth0.com", "b-only.example.com"])
    with_identity = fleet.independence_score(a, b)

    c = _cluster("c", "eks", egress_endpoints=["app.datadoghq.com", "a-only.example.com"])
    d = _cluster("d", "aks", egress_endpoints=["app.datadoghq.com", "b-only.example.com"])
    with_observability = fleet.independence_score(c, d)

    assert with_identity["score"] < with_observability["score"]
    assert with_identity["shared"][0]["endpoint"] == "tenant.auth0.com"


def test_independence_with_no_dependencies():
    a = {"name": "a", "provider": "eks", "snapshot": {"workloads": [], "edges": []}}
    assert fleet.independence_score(a, a)["score"] is None


# ═══ correlated availability ═════════════════════════════════════════════


@pytest.fixture
def chain_snapshot():
    """web -> api -> db, plus an isolated worker."""
    return {
        "workloads": [_workload(n) for n in ("web", "api", "db", "worker")],
        "edges": [_edge("web", "api"), _edge("api", "db")],
        "nodes": [_node()], "pods": [],
    }


def test_effective_availability_is_below_own_prior(chain_snapshot):
    result = availability.simulate(chain_snapshot, trials=4000)
    rows = {w["workload_key"]: w for w in result["per_workload"]}

    # web inherits db's and api's failures; worker only its own.
    assert rows["prod/Deployment/web"]["effective"] < rows["prod/Deployment/worker"]["effective"]


def test_dependency_share_of_downtime_is_attributed(chain_snapshot):
    result = availability.simulate(chain_snapshot, trials=6000)
    web = next(w for w in result["per_workload"] if w["workload_key"] == "prod/Deployment/web")

    # Two upstream dependencies vs one self: most downtime arrives from below.
    assert web["downtime_from_dependencies"] > 0.4


def test_simulation_is_deterministic(chain_snapshot):
    a = availability.simulate(chain_snapshot, trials=2000)
    b = availability.simulate(chain_snapshot, trials=2000)

    assert a["per_workload"] == b["per_workload"]


def test_correlation_cost_ranks_the_gap(chain_snapshot):
    result = availability.simulate(chain_snapshot, trials=4000)
    cost = availability.correlation_cost(result)

    assert cost[0]["nines_lost_to_correlation"] >= cost[-1]["nines_lost_to_correlation"]
    assert all("naive" in row and "effective" in row for row in cost)


def test_model_declares_itself_a_model(chain_snapshot):
    result = availability.simulate(chain_snapshot, trials=1000)

    assert "A model, not a measurement" in result["summary"]["note"]
    assert result["seed"] == availability.DEFAULT_SEED


def test_empty_snapshot_is_unavailable():
    assert availability.simulate({"workloads": [], "edges": []})["available"] is False


def test_cycle_does_not_hang():
    snapshot = {
        "workloads": [_workload("a"), _workload("b")],
        "edges": [_edge("a", "b"), _edge("b", "a")],
        "nodes": [], "pods": [],
    }
    assert availability.simulate(snapshot, trials=500)["available"] is True


# ═══ control-plane audit ═════════════════════════════════════════════════


def test_scale_up_beyond_capacity_is_flagged():
    """HPA max needs more than the node pool holds -> control-plane dependency."""
    snapshot = {
        "workloads": [_workload("api", replicas=2, cpu_pod=2.0)],
        "edges": [],
        "nodes": [_node(cpu=5.0)],  # 4 used by api, 1 free; max needs 4 more
        "autoscalers": [_hpa("api", maximum=4, current=2)],
        "pods": [],
    }
    finding = _find(control_plane.analyze(snapshot), "scale_up_needs_node_provisioning")

    assert "capacity the cluster does not have" in finding["title"]
    assert finding["evidence"]["fits_in_current_capacity"] is False


def test_scale_up_within_capacity_is_not_flagged():
    snapshot = {
        "workloads": [_workload("api", replicas=2, cpu_pod=0.5)],
        "edges": [],
        "nodes": [_node(cpu=16.0)],
        "autoscalers": [_hpa("api", maximum=4, current=2)],
        "pods": [],
    }
    result = control_plane.analyze(snapshot)

    assert "scale_up_needs_node_provisioning" not in _kinds(result)
    assert result["scale_up_headroom"][0]["fits_in_current_capacity"] is True


def test_mutable_tags_put_registry_on_the_recovery_path():
    snapshot = {
        "workloads": [
            _workload("api", images=("repo/api:latest",)),
            *(_workload(f"c{i}") for i in range(3)),
        ],
        "edges": [_edge(f"c{i}", "api") for i in range(3)],
        "nodes": [_node()], "autoscalers": [], "pods": [],
    }
    finding = _find(control_plane.analyze(snapshot), "recovery_depends_on_registry")

    assert "Pinning by digest" in finding["detail"]


def test_static_stability_score_counts_pinned_workloads():
    snapshot = {
        "workloads": [
            _workload("pinned", images=("repo/a@sha256:abc",)),
            _workload("floating", images=("repo/b:latest",)),
        ],
        "edges": [], "nodes": [_node()], "autoscalers": [], "pods": [],
    }
    stability = control_plane.analyze(snapshot)["static_stability"]

    assert stability["score"] == 0.5
    assert "upper bound" in control_plane.analyze(snapshot)["summary"]["note"]


def test_unschedulable_nodes_do_not_count_as_capacity():
    snapshot = {
        "workloads": [_workload("api", replicas=1, cpu_pod=2.0)],
        "edges": [],
        "nodes": [_node(cpu=4.0), dict(_node("cordoned", cpu=64.0), unschedulable=True)],
        "autoscalers": [_hpa("api", maximum=4, current=1)],
        "pods": [],
    }
    assert "scale_up_needs_node_provisioning" in _kinds(control_plane.analyze(snapshot))


# ═══ chaos: calibration ══════════════════════════════════════════════════


def _experiment(target, measured, ns="prod"):
    return ExperimentResult(
        workload_key=f"{ns}/Deployment/{target}",
        predicted=set(),
        measured={f"{ns}/Deployment/{m}" for m in measured},
    )


def test_calibration_measures_transmission_per_edge_kind():
    snapshot = {
        "workloads": [_workload(n) for n in ("db", "a", "b", "c")],
        "edges": [
            _edge("a", "db", kind="env_reference"),
            _edge("b", "db", kind="env_reference"),
            _edge("c", "db", kind="service_selector", confidence=1.0),
        ],
        "pods": [], "nodes": [],
    }
    # a and c transmitted; b did not.
    result = calibrate_edges(snapshot, [_experiment("db", ["a", "c"])])
    env = result["edge_kinds"]["env_reference"]

    assert env["observed_edges"] == 2
    assert env["raw_rate"] == 0.5
    assert result["edge_kinds"]["service_selector"]["raw_rate"] == 1.0


def test_calibration_smooths_small_samples():
    """One experiment must not produce a rate of exactly 0 or 1."""
    snapshot = {
        "workloads": [_workload("db"), _workload("a")],
        "edges": [_edge("a", "db")],
        "pods": [], "nodes": [],
    }
    rate = calibrate_edges(snapshot, [_experiment("db", ["a"])])["edge_kinds"]["env_reference"]

    assert rate["raw_rate"] == 1.0
    assert rate["calibrated_rate"] < 1.0


def test_calibration_only_scores_direct_edges():
    """A transitive outcome depends on the whole path; scoring one edge with it double-counts."""
    snapshot = {
        "workloads": [_workload(n) for n in ("db", "api", "web")],
        "edges": [_edge("api", "db"), _edge("web", "api")],
        "pods": [], "nodes": [],
    }
    result = calibrate_edges(snapshot, [_experiment("db", ["api", "web"])])

    assert result["edge_kinds"]["env_reference"]["observed_edges"] == 1


# ═══ chaos: recovery curves ══════════════════════════════════════════════


def _ready_snapshot(ready_map):
    return {
        "workloads": [
            _workload(n, replicas=2, ready=r) for n, r in ready_map.items()
        ],
        "edges": [],
    }


def test_recovery_times_are_measured():
    baseline = _ready_snapshot({"api": 2, "web": 2})
    timeline = [
        (10, _ready_snapshot({"api": 0, "web": 0})),
        (20, _ready_snapshot({"api": 2, "web": 0})),
        (30, _ready_snapshot({"api": 2, "web": 2})),
    ]
    result = measure_recovery(baseline, timeline, "prod/Deployment/db")
    times = {r["workload_key"]: r["seconds"] for r in result["recovered"]}

    assert times["prod/Deployment/api"] == 20
    assert times["prod/Deployment/web"] == 30
    assert result["slowest_recovery_seconds"] == 30
    assert result["metastable_suspected"] is False


def test_unrecovered_workload_is_the_metastable_signature():
    baseline = _ready_snapshot({"api": 2, "web": 2})
    timeline = [
        (10, _ready_snapshot({"api": 0, "web": 0})),
        (60, _ready_snapshot({"api": 2, "web": 0})),
        (120, _ready_snapshot({"api": 2, "web": 0})),
    ]
    result = measure_recovery(baseline, timeline, "prod/Deployment/db")

    assert result["unrecovered"] == ["prod/Deployment/web"]
    assert result["metastable_suspected"] is True


def test_flapping_is_not_counted_as_recovery():
    """Came back, went down again: the first blip is not recovery."""
    baseline = _ready_snapshot({"api": 2})
    timeline = [
        (10, _ready_snapshot({"api": 2})),
        (20, _ready_snapshot({"api": 0})),
        (30, _ready_snapshot({"api": 2})),
    ]
    result = measure_recovery(baseline, timeline, "prod/Deployment/db")

    assert result["flapped"] == ["prod/Deployment/api"]
    times = {r["workload_key"]: r["seconds"] for r in result["recovered"]}
    assert times["prod/Deployment/api"] == 30


def test_untouched_workload_reports_no_recovery_event():
    baseline = _ready_snapshot({"api": 2})
    timeline = [(10, _ready_snapshot({"api": 2}))]
    result = measure_recovery(baseline, timeline, "prod/Deployment/db")

    assert result["recovered"] == []
    assert result["unrecovered"] == []
