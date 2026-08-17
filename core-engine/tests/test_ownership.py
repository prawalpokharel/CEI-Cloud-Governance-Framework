"""Ownerless critical dependency detection."""

import pytest

from src.services.ownership import (
    MANAGER_KEYS,
    OWNER_KEYS,
    analyze,
    image_is_reproducible,
    parse_image,
)


def _workload(name, *, ns="prod", ownership=None, images=("repo/app:1.2.3",),
              kind="Deployment", replicas=2):
    return {
        "key": f"{ns}/{kind}/{name}",
        "name": name,
        "namespace": ns,
        "kind": kind,
        "replicas_desired": replicas,
        "pod_labels": {"app": name},
        "ownership": ownership or {},
        "images": list(images),
        "probes": {"containers": 1, "with_readiness": 1, "with_liveness": 1, "with_startup": 0},
        "spread": {"topology_spread_constraints": 0, "topology_keys": [],
                   "anti_affinity_required": 0, "anti_affinity_preferred": 0,
                   "priority_class": None},
        "config_refs": {"config_maps": [], "secrets": []},
    }


def _snapshot(workloads, edges=()):
    return {"workloads": list(workloads), "pods": [], "edges": list(edges)}


def _edge(source, target, kind="env_reference", confidence=0.9):
    return {"source": source, "target": target, "confidence": confidence,
            "source_kind": kind}


def _kinds(result):
    return {f["kind"] for f in result["findings"]}


def _find(result, kind):
    return next(f for f in result["findings"] if f["kind"] == kind)


CEI = {
    "prod/Deployment/legacy": {"cei_score": 0.82, "classification": "critical"},
    "prod/Deployment/api": {"cei_score": 0.70, "classification": "elevated"},
    "prod/Deployment/worker": {"cei_score": 0.35, "classification": "moderate"},
}


@pytest.fixture
def central_legacy():
    """Two workloads depend on `legacy`, making it load-bearing."""
    return _snapshot(
        workloads=[
            _workload("legacy", ownership={}, images=["internal/legacy:latest"]),
            _workload("api", ownership={"team": "platform",
                                        "app.kubernetes.io/managed-by": "Helm"}),
            _workload("worker", ownership={"team": "platform",
                                           "app.kubernetes.io/managed-by": "Helm"}),
        ],
        edges=[
            _edge("prod/Deployment/api", "prod/Deployment/legacy"),
            _edge("prod/Deployment/worker", "prod/Deployment/legacy"),
        ],
    )


# --- image parsing ---------------------------------------------------------


@pytest.mark.parametrize("image,repository,tag,digest", [
    ("nginx", "nginx", None, None),
    ("nginx:1.25", "nginx", "1.25", None),
    ("repo/app:1.2.3", "repo/app", "1.2.3", None),
    ("ghcr.io/org/app:v2", "ghcr.io/org/app", "v2", None),
    ("localhost:5000/app", "localhost:5000/app", None, None),
    ("localhost:5000/app:dev", "localhost:5000/app", "dev", None),
    ("repo/app@sha256:abc", "repo/app", None, "sha256:abc"),
    ("repo/app:1.0@sha256:abc", "repo/app", "1.0", "sha256:abc"),
])
def test_image_parsing(image, repository, tag, digest):
    parsed = parse_image(image)

    assert parsed["repository"] == repository
    assert parsed["tag"] == tag
    assert parsed["digest"] == digest


def test_registry_port_is_not_mistaken_for_a_tag():
    """A colon before the final slash is a port, not a tag."""
    assert parse_image("registry.internal:5000/team/app")["tag"] is None


def test_empty_image():
    assert parse_image("")["repository"] == ""


@pytest.mark.parametrize("image", [
    "repo/app:1.2.3", "repo/app@sha256:abc", "repo/app:latest@sha256:abc",
    "repo/app:v2.0.1", "repo/app:2026-08-16",
])
def test_reproducible_images(image):
    assert image_is_reproducible(image)[0] is True


@pytest.mark.parametrize("image,fragment", [
    ("repo/app:latest", "mutable"),
    ("repo/app:main", "mutable"),
    ("repo/app:LATEST", "mutable"),
    ("repo/app", "no tag"),
])
def test_unreproducible_images(image, fragment):
    ok, reason = image_is_reproducible(image)

    assert ok is False
    assert fragment in reason


def test_digest_beats_a_mutable_tag():
    """`latest@sha256:...` pins content however the tag is spelled."""
    assert image_is_reproducible("repo/app:latest@sha256:abc")[0] is True


# --- individual signals ----------------------------------------------------


def test_unowned_central_workload_is_flagged(central_legacy):
    finding = _find(analyze(central_legacy, CEI), "unowned_critical_workload")

    assert finding["workload_key"] == "prod/Deployment/legacy"
    assert "on call" in finding["detail"]


def test_unmanaged_central_workload_is_flagged(central_legacy):
    finding = _find(analyze(central_legacy, CEI), "unmanaged_critical_workload")

    assert "only copy" in finding["detail"]


def test_unreproducible_image_is_flagged(central_legacy):
    finding = _find(analyze(central_legacy, CEI), "unreproducible_image")

    assert finding["evidence"]["images"][0]["image"] == "internal/legacy:latest"


def test_owned_and_managed_workload_is_clean(central_legacy):
    central_legacy["workloads"][0]["ownership"] = {
        "team": "platform", "app.kubernetes.io/managed-by": "Helm",
    }
    central_legacy["workloads"][0]["images"] = ["internal/legacy:2.1.0"]

    assert analyze(central_legacy, CEI)["findings"] == []


@pytest.mark.parametrize("key", OWNER_KEYS)
def test_any_owner_key_satisfies_ownership(central_legacy, key):
    central_legacy["workloads"][0]["ownership"] = {key: "somebody"}

    assert "unowned_critical_workload" not in _kinds(analyze(central_legacy, CEI))


@pytest.mark.parametrize("key", MANAGER_KEYS)
def test_any_manager_key_satisfies_management(central_legacy, key):
    central_legacy["workloads"][0]["ownership"] = {key: "some-release"}

    assert "unmanaged_critical_workload" not in _kinds(analyze(central_legacy, CEI))


def test_owner_key_does_not_satisfy_management(central_legacy):
    """
    A team label says who to page. It does not say where the manifest lives,
    and conflating the two would hide the harder problem.
    """
    central_legacy["workloads"][0]["ownership"] = {"team": "platform"}
    kinds = _kinds(analyze(central_legacy, CEI))

    assert "unowned_critical_workload" not in kinds
    assert "unmanaged_critical_workload" in kinds


# --- centrality gating -----------------------------------------------------


def test_unowned_workload_with_no_dependents_is_ignored():
    """An orphan nothing depends on is trivia, not a finding."""
    snapshot = _snapshot([_workload("scratch", ownership={}, images=["a:latest"])])

    assert analyze(snapshot, {})["findings"] == []


def test_user_facing_workload_counts_as_central():
    """Nothing depends on a frontend, and every user does."""
    snapshot = _snapshot(
        workloads=[_workload("frontend", ownership={}, images=["f:latest"])],
        edges=[_edge("prod/Ingress/public", "prod/Deployment/frontend", "ingress", 1.0)],
    )
    result = analyze(snapshot, {})

    assert "orphaned_critical_dependency" in _kinds(result)
    assert result["orphans"][0]["user_facing"] is True


def _system_snapshot():
    return _snapshot(
        workloads=[
            _workload("coredns", ns="kube-system", ownership={}, images=["coredns:latest"]),
            *(_workload(f"client{i}", ns="kube-system") for i in range(3)),
        ],
        edges=[
            _edge(f"kube-system/Deployment/client{i}", "kube-system/Deployment/coredns")
            for i in range(3)
        ],
    )


def test_system_namespaces_are_skipped_by_default():
    assert analyze(_system_snapshot(), {})["findings"] == []


def test_system_namespaces_can_be_included():
    result = analyze(_system_snapshot(), {}, include_system_namespaces=True)

    assert "orphaned_critical_dependency" in _kinds(result)


# --- the combination -------------------------------------------------------


def test_multiple_gaps_escalate_to_critical(central_legacy):
    result = analyze(central_legacy, CEI)
    finding = _find(result, "orphaned_critical_dependency")

    assert finding["severity"] == "critical"
    assert set(finding["evidence"]["gaps"]) == {
        "no owner", "no source of truth", "unidentifiable image",
    }
    assert result["summary"]["orphans"] == 1


def test_single_gap_does_not_escalate(central_legacy):
    """Owned and managed, but the tag is mutable. Worth saying, not critical."""
    central_legacy["workloads"][0]["ownership"] = {
        "team": "platform", "app.kubernetes.io/managed-by": "Helm",
    }
    result = analyze(central_legacy, CEI)

    assert "orphaned_critical_dependency" not in _kinds(result)
    assert "unreproducible_image" in _kinds(result)
    assert result["summary"]["orphans"] == 0


def test_orphans_rank_by_dependents():
    callers = ("a", "b", "c", "d")
    workloads = [
        _workload("shared", ownership={}, images=["s:latest"]),
        _workload("lesser", ownership={}, images=["m:latest"]),
        *(_workload(n) for n in callers),
    ]
    edges = (
        [_edge(f"prod/Deployment/{n}", "prod/Deployment/shared") for n in callers]
        + [_edge(f"prod/Deployment/{n}", "prod/Deployment/lesser") for n in callers[:3]]
    )
    result = analyze(_snapshot(workloads, edges), {})

    assert [o["workload_key"] for o in result["orphans"]] == [
        "prod/Deployment/shared", "prod/Deployment/lesser",
    ]
    assert result["orphans"][0]["dependents"] > result["orphans"][1]["dependents"]


def test_orphan_with_too_few_dependents_is_excluded():
    """
    Two callers is below the load-bearing floor. Reporting it would put a
    genuinely minor workload next to one four services depend on.
    """
    workloads = [
        _workload("minor", ownership={}, images=["m:latest"]),
        _workload("a"), _workload("b"),
    ]
    edges = [_edge(f"prod/Deployment/{n}", "prod/Deployment/minor") for n in ("a", "b")]

    assert analyze(_snapshot(workloads, edges), {})["orphans"] == []


# --- coverage context ------------------------------------------------------


def test_low_ownership_coverage_reframes_the_finding():
    """
    If almost nothing is labelled, the problem is a missing convention, not
    six neglected workloads. Reporting it as the latter sends someone to
    annotate services one at a time.
    """
    workloads = [_workload(f"svc{i}", ownership={}, images=["a:latest"]) for i in range(5)]
    workloads.append(_workload("hub", ownership={}, images=["h:latest"]))
    edges = [_edge(f"prod/Deployment/svc{i}", "prod/Deployment/hub") for i in range(5)]
    summary = analyze(_snapshot(workloads, edges), {})["summary"]

    assert summary["ownership_coverage"] == 0.0
    assert "convention that is not in use" in summary["note"]


def test_high_ownership_coverage_omits_the_note():
    workloads = [
        _workload(f"svc{i}", ownership={"team": "x", "app.kubernetes.io/managed-by": "Helm"})
        for i in range(5)
    ]
    workloads.append(_workload("hub", ownership={}, images=["h:latest"]))
    edges = [_edge(f"prod/Deployment/svc{i}", "prod/Deployment/hub") for i in range(5)]
    summary = analyze(_snapshot(workloads, edges), {})["summary"]

    assert summary["ownership_coverage"] > 0.8
    assert summary["note"] is None


def test_empty_snapshot():
    result = analyze({"workloads": [], "pods": [], "edges": []}, {})

    assert result["summary"]["total"] == 0
    assert result["orphans"] == []


def test_v1_snapshot_without_ownership_field(central_legacy):
    """An old agent sends no `ownership` key at all; absence reads as unowned."""
    for workload in central_legacy["workloads"]:
        workload.pop("ownership", None)
    result = analyze(central_legacy, CEI)

    assert "unowned_critical_workload" in _kinds(result)
