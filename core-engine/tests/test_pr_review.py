"""
Pull-request blast radius.

Every scenario is a change that reviews as small. That is the point: if the
diff looked dangerous, a reviewer would already have caught it.
"""

import pytest

from src.services import manifest_diff, pr_review


# --- fixtures ---------------------------------------------------------------


def _workload(name, *, ns="prod", kind="Deployment", config_maps=(), replicas=3):
    return {
        "key": f"{ns}/{kind}/{name}",
        "name": name,
        "namespace": ns,
        "kind": kind,
        "replicas_desired": replicas,
        "pod_labels": {"app": name},
        "labels": {"app": name},
        "ownership": {"team": "platform", "app.kubernetes.io/managed-by": "Helm"},
        "images": [f"repo/{name}:1.0"],
        "probes": {"containers": 1, "with_readiness": 1, "with_liveness": 1, "with_startup": 0},
        "spread": {"topology_spread_constraints": 1, "topology_keys": ["kubernetes.io/hostname"],
                   "anti_affinity_required": 0, "anti_affinity_preferred": 0,
                   "priority_class": None},
        "config_refs": {"config_maps": list(config_maps), "secrets": []},
    }


def _edge(source, target, kind="env_reference", confidence=0.9):
    return {"source": source, "target": target, "confidence": confidence,
            "source_kind": kind}


@pytest.fixture
def snapshot():
    """api is depended on by three services and sits behind an ingress."""
    return {
        "workloads": [
            _workload("api", config_maps=["app-config"]),
            _workload("checkout", config_maps=["app-config"]),
            _workload("worker", config_maps=["app-config"]),
            _workload("reporting"),
        ],
        "services": [
            {"name": "api", "namespace": "prod", "selector": {"app": "api"}},
        ],
        "pods": [],
        "disruption_budgets": [],
        "autoscalers": [],
        "edges": [
            _edge("prod/Deployment/checkout", "prod/Deployment/api"),
            _edge("prod/Deployment/worker", "prod/Deployment/api"),
            _edge("prod/Deployment/reporting", "prod/Deployment/api"),
            _edge("prod/Ingress/public", "prod/Deployment/checkout", "ingress", 1.0),
        ],
    }


CEI = {
    "prod/Deployment/api": {"cei_score": 0.88, "classification": "critical"},
    "prod/Deployment/checkout": {"cei_score": 0.72, "classification": "elevated"},
    "prod/Deployment/worker": {"cei_score": 0.40, "classification": "moderate"},
    "prod/Deployment/reporting": {"cei_score": 0.25, "classification": "low"},
}


def _deployment(name, *, replicas=3, image="repo/api:1.0", selector=None,
                ns="prod", probe=True, cpu="500m"):
    labels = selector or {"app": name}
    container = {
        "name": "app",
        "image": image,
        "resources": {"requests": {"cpu": cpu, "memory": "512Mi"}},
    }
    if probe:
        container["readinessProbe"] = {"httpGet": {"path": "/healthz", "port": 8080}}
    return f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  namespace: {ns}
spec:
  replicas: {replicas}
  selector:
    matchLabels:
{chr(10).join(f'      {k}: {v}' for k, v in labels.items())}
  template:
    metadata:
      labels:
{chr(10).join(f'        {k}: {v}' for k, v in labels.items())}
    spec:
      containers:
        - name: app
          image: {image}
          resources:
            requests:
              cpu: {cpu}
              memory: 512Mi
{'          readinessProbe:' + chr(10) + '            httpGet:' + chr(10) + '              path: /healthz' + chr(10) + '              port: 8080' if probe else ''}
""".strip()


def _review(snapshot, before, after, path="k8s/api.yaml"):
    return pr_review.review(
        snapshot, [{"path": path, "before": before, "after": after}], CEI
    )


def _impact(result, name):
    return next(i for i in result["impacts"] if i["name"] == name)


# --- manifest parsing -------------------------------------------------------


def test_replica_reduction_is_detected(snapshot):
    result = _review(snapshot, _deployment("api", replicas=3), _deployment("api", replicas=1))
    impact = _impact(result, "api")

    assert "replicas_reduced" in impact["change_kinds"]
    assert "single replica" in impact["details"][0]


def test_scale_to_zero_is_called_out(snapshot):
    result = _review(snapshot, _deployment("api", replicas=3), _deployment("api", replicas=0))

    assert "stops the workload entirely" in _impact(result, "api")["details"][0]


def test_selector_change_is_critical(snapshot):
    """
    The worst change in Kubernetes: no error, no failed rollout, no unhealthy
    pod. Traffic just stops arriving.
    """
    result = _review(
        snapshot,
        _deployment("api", selector={"app": "api"}),
        _deployment("api", selector={"app": "api-v2"}),
    )
    impact = _impact(result, "api")

    assert "selector_changed" in impact["change_kinds"]
    assert impact["risk"] == "critical"
    assert any("without an error" in n for n in impact["notes"])


def test_image_change_is_detected(snapshot):
    result = _review(
        snapshot,
        _deployment("api", image="repo/api:1.0"),
        _deployment("api", image="repo/api:2.0"),
    )
    assert "image_changed" in _impact(result, "api")["change_kinds"]


def test_resource_reduction_is_detected(snapshot):
    result = _review(
        snapshot, _deployment("api", cpu="500m"), _deployment("api", cpu="100m")
    )
    impact = _impact(result, "api")

    assert "resources_reduced" in impact["change_kinds"]
    assert any("requests.cpu" in d for d in impact["details"])


def test_resource_increase_is_reported_but_not_as_a_reduction(snapshot):
    """
    Raising a request is not a risk, but saying "unclassified change" when the
    direction is known reads as though the analysis gave up.
    """
    result = _review(
        snapshot, _deployment("api", cpu="100m"), _deployment("api", cpu="500m")
    )
    impact = _impact(result, "api")

    assert "resources_increased" in impact["change_kinds"]
    assert "resources_reduced" not in impact["change_kinds"]
    assert "other_change" not in impact["change_kinds"]


def test_probe_removal_is_detected(snapshot):
    result = _review(
        snapshot, _deployment("api", probe=True), _deployment("api", probe=False)
    )
    assert "probe_removed" in _impact(result, "api")["change_kinds"]


def test_deletion_is_critical(snapshot):
    result = _review(snapshot, _deployment("api"), None)
    impact = _impact(result, "api")

    assert impact["change_kinds"] == ["deleted"]
    assert impact["risk"] == "critical"


def test_reformatting_produces_no_findings(snapshot):
    """
    A tool that fires on yamlfmt gets switched off. Semantic comparison means
    whitespace and key order changes are invisible.
    """
    before = _deployment("api")
    after = before.replace("  replicas: 3", "  replicas:   3") + "\n\n"

    assert _review(snapshot, before, after)["impacts"] == []


def test_multi_document_file_is_split(snapshot):
    before = _deployment("api") + "\n---\n" + _deployment("worker", replicas=2)
    after = _deployment("api") + "\n---\n" + _deployment("worker", replicas=1)
    result = _review(snapshot, before, after)

    assert [i["name"] for i in result["impacts"]] == ["worker"]


# --- resolution against the cluster ----------------------------------------


def test_impact_counts_dependents(snapshot):
    result = _review(snapshot, _deployment("api", replicas=3), _deployment("api", replicas=1))
    impact = _impact(result, "api")

    assert impact["resolved_in_cluster"] is True
    assert set(impact["direct_dependents"]) == {
        "prod/Deployment/checkout", "prod/Deployment/worker", "prod/Deployment/reporting",
    }


def test_user_facing_reach_escalates_risk(snapshot):
    """
    api itself has no ingress. checkout depends on it and does, so the change
    reaches a user-facing path two hops away.
    """
    result = _review(snapshot, _deployment("api", replicas=3), _deployment("api", replicas=1))
    impact = _impact(result, "api")

    assert impact["user_facing"] is True
    assert impact["risk"] == "critical"


def test_change_to_isolated_workload_stays_low(snapshot):
    result = _review(
        snapshot,
        _deployment("reporting", replicas=3),
        _deployment("reporting", replicas=2),
        path="k8s/reporting.yaml",
    )
    impact = _impact(result, "reporting")

    assert impact["user_facing"] is False
    assert impact["dependents"] == 1
    assert impact["risk"] in ("low", "moderate")


def test_unknown_workload_is_reported_as_unmeasured(snapshot):
    result = _review(
        snapshot, _deployment("brand-new", replicas=3), _deployment("brand-new", replicas=1),
        path="k8s/new.yaml",
    )
    impact = _impact(result, "brand-new")

    assert impact["resolved_in_cluster"] is False
    assert any("not found in the current cluster" in n.lower() for n in impact["notes"])
    assert result["summary"]["unresolved"] == 1


# --- ConfigMap fan-out ------------------------------------------------------


CONFIGMAP_BEFORE = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
  namespace: prod
data:
  LOG_LEVEL: info
  TIMEOUT: "30"
""".strip()

CONFIGMAP_AFTER = CONFIGMAP_BEFORE.replace('TIMEOUT: "30"', 'TIMEOUT: "5"')


def test_configmap_edit_resolves_to_every_reader(snapshot):
    """
    A one-line ConfigMap edit is a change to three workloads. Nothing in the
    diff, and nothing in Kubernetes, says so.
    """
    result = _review(snapshot, CONFIGMAP_BEFORE, CONFIGMAP_AFTER, path="k8s/config.yaml")
    impact = _impact(result, "app-config")

    assert impact["resolved_in_cluster"] is True
    assert impact["dependents"] >= 3
    assert any("mounted by 3 workload(s)" in n for n in impact["notes"])


def test_configmap_edit_reports_keys_not_values(snapshot):
    """Config values must never be echoed into a public PR comment."""
    result = _review(snapshot, CONFIGMAP_BEFORE, CONFIGMAP_AFTER, path="k8s/config.yaml")
    impact = _impact(result, "app-config")

    assert "TIMEOUT" in impact["details"][0]
    assert "30" not in impact["details"][0]
    assert "5" not in impact["details"][0].replace("key(s)", "")


def test_secret_values_never_appear_in_output(snapshot):
    before = """
apiVersion: v1
kind: Secret
metadata: {name: db-creds, namespace: prod}
stringData: {PASSWORD: hunter2}
""".strip()
    after = before.replace("hunter2", "correct-horse-battery-staple")
    result = _review(snapshot, before, after, path="k8s/secret.yaml")

    rendered = pr_review.render_markdown(result)
    assert "hunter2" not in rendered
    assert "correct-horse" not in rendered
    assert "PASSWORD" in rendered


# --- skipping ---------------------------------------------------------------


def test_helm_template_is_skipped_not_guessed(snapshot):
    template = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-api
spec:
  replicas: {{ .Values.replicaCount }}
""".strip()
    result = pr_review.review(
        snapshot, [{"path": "chart/templates/api.yaml", "before": template,
                    "after": template.replace("replicaCount", "replicas")}], CEI
    )

    assert result["impacts"] == []
    assert result["skipped_files"][0]["reason"].startswith("contains Go template")


def test_malformed_yaml_is_reported_not_silently_dropped(snapshot):
    result = pr_review.review(
        snapshot,
        [{"path": "k8s/broken.yaml", "before": "kind: Deployment\n",
          "after": "kind: Deployment\n  bad indent: ][\n"}],
        CEI,
    )

    assert result["summary"]["files_skipped"] == 1
    assert "YAML parse error" in result["skipped_files"][0]["reason"]


def test_non_manifest_files_are_ignored(snapshot):
    result = pr_review.review(
        snapshot, [{"path": "README.md", "before": "a", "after": "b"}], CEI
    )

    assert result["impacts"] == []
    assert result["skipped_files"] == []


def test_unrelated_kinds_are_ignored(snapshot):
    role = """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: r, namespace: prod}
rules: []
""".strip()
    result = _review(snapshot, role, role.replace("rules: []", "rules: [{verbs: [get]}]"))

    assert result["impacts"] == []


# --- verdict and rendering --------------------------------------------------


def test_verdict_is_the_worst_impact(snapshot):
    before = _deployment("api", replicas=3) + "\n---\n" + _deployment("reporting", replicas=3)
    after = _deployment("api", replicas=1) + "\n---\n" + _deployment("reporting", replicas=2)
    result = _review(snapshot, before, after)

    assert result["verdict"] == "critical"


def test_clean_pr_verdict_is_none(snapshot):
    result = pr_review.review(snapshot, [], CEI)

    assert result["verdict"] == "none"
    assert result["summary"]["objects_changed"] == 0


def test_markdown_contains_the_marker_for_idempotent_upsert(snapshot):
    result = _review(snapshot, _deployment("api", replicas=3), _deployment("api", replicas=1))
    body = pr_review.render_markdown(result)

    assert body.startswith(pr_review.COMMENT_MARKER)


def test_markdown_names_dependents_and_ingress(snapshot):
    result = _review(snapshot, _deployment("api", replicas=3), _deployment("api", replicas=1))
    body = pr_review.render_markdown(result)

    assert "checkout" in body
    assert "ingress" in body.lower()
    assert "Blast radius" in body


def test_markdown_states_the_graph_limitation(snapshot):
    result = _review(snapshot, _deployment("api", replicas=3), _deployment("api", replicas=1))

    assert "hardcoded in application code" in pr_review.render_markdown(result)


def test_markdown_for_empty_review(snapshot):
    body = pr_review.render_markdown(pr_review.review(snapshot, [], CEI))

    assert "No Kubernetes object changes" in body


@pytest.mark.parametrize("verdict,blocking,expected", [
    ("critical", False, "neutral"),
    ("critical", True, "failure"),
    ("high", True, "failure"),
    ("low", False, "success"),
    ("moderate", False, "neutral"),
])
def test_check_conclusion(verdict, blocking, expected):
    result = {"verdict": verdict, "summary": {"objects_changed": 1}}

    assert pr_review.check_conclusion(result, blocking=blocking) == expected


def test_check_is_not_blocking_by_default(snapshot):
    """
    A check that fails the build on a heuristic gets bypassed and then
    ignored. Enforcement is opt-in.
    """
    result = _review(snapshot, _deployment("api"), None)

    assert result["verdict"] == "critical"
    assert pr_review.check_conclusion(result) == "neutral"


# --- quantity parsing -------------------------------------------------------


@pytest.mark.parametrize("before,after,reduced", [
    ("500m", "100m", True), ("1", "500m", True), ("100m", "1", False),
    ("1Gi", "512Mi", True), ("512Mi", "1Gi", False), ("1", "1", False),
])
def test_resource_direction(before, after, reduced):
    change = manifest_diff.compare(
        {"kind": "Deployment", "metadata": {"name": "a"},
         "spec": {"template": {"spec": {"containers": [
             {"name": "c", "resources": {"requests": {"cpu": before}}}]}}}},
        {"kind": "Deployment", "metadata": {"name": "a"},
         "spec": {"template": {"spec": {"containers": [
             {"name": "c", "resources": {"requests": {"cpu": after}}}]}}}},
        "p.yaml",
    )
    kinds = change.change_kinds if change else []

    assert ("resources_reduced" in kinds) is reduced
