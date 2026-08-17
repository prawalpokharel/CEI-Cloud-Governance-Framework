"""
Hubble flow parsing.

Fixtures are real `hubble observe --output jsonpb` record shapes. The schema
is stable and documented; this has NOT been validated against a live Hubble,
because Cilium's datapath needs tc/clsact qdisc support that Docker Desktop's
linuxkit kernel does not provide.
"""

from __future__ import annotations

from cloudoptimizer_agent.flows import (
    IDENTITY_WORLD,
    _is_external,
    _workload_key,
    parse_flow_stream,
    summarize_egress,
)


def _flow(*, src_pod, src_ns="default", dst_identity=IDENTITY_WORLD,
          dst_names=None, dst_ip="93.184.216.34", port=443,
          verdict="FORWARDED", direction="EGRESS", dst_ns=None, dst_pod=None):
    return {
        "time": "2026-08-17T01:00:00Z",
        "verdict": verdict,
        "traffic_direction": direction,
        "source": {"namespace": src_ns, "pod_name": src_pod,
                   "labels": [f"k8s:app={src_pod.split('-')[0]}"]},
        "destination": {
            "identity": dst_identity,
            "labels": ["reserved:world"] if dst_identity == IDENTITY_WORLD else [],
            **({"namespace": dst_ns} if dst_ns else {}),
            **({"pod_name": dst_pod} if dst_pod else {}),
        },
        "destination_names": dst_names or [],
        "IP": {"source": "10.244.0.5", "destination": dst_ip, "ipVersion": "IPv4"},
        "l4": {"TCP": {"source_port": 40404, "destination_port": port}},
        "Type": "L3_L4",
        "node_name": "node-1",
    }


# -- parsing ---------------------------------------------------------------

def test_newline_delimited_records_are_parsed():
    import json
    lines = [json.dumps({"flow": _flow(src_pod="api-7d9f8b5c4-x2k9p")}) for _ in range(3)]
    assert len(parse_flow_stream(lines)) == 3


def test_non_flow_lines_are_skipped_without_losing_the_batch():
    """Hubble interleaves status messages; one bad line must not void a window."""
    import json
    lines = [
        "connecting to relay...",
        json.dumps({"flow": _flow(src_pod="api-7d9f8b5c4-x2k9p")}),
        "{not valid json",
        "",
        json.dumps({"flow": _flow(src_pod="web-6c8d9f7b5-a1b2c")}),
    ]
    assert len(parse_flow_stream(lines)) == 2


# -- workload attribution --------------------------------------------------

def test_deployment_pod_names_reduce_to_the_deployment():
    assert _workload_key(
        {"namespace": "shop", "pod_name": "checkout-7d9f8b5c4-x2k9p"}
    ) == "shop/Deployment/checkout"


def test_statefulset_pod_names_reduce_to_the_statefulset():
    assert _workload_key(
        {"namespace": "data", "pod_name": "postgres-0"}
    ) == "data/StatefulSet/postgres"


def test_an_unrecognized_pod_name_is_not_truncated_wrongly():
    """Guessing wrong attributes flows to a workload that does not exist."""
    assert _workload_key(
        {"namespace": "kube-system", "pod_name": "coredns"}
    ) == "kube-system/Pod/coredns"


def test_an_endpoint_without_a_namespace_yields_no_key():
    assert _workload_key({"pod_name": "x"}) is None


# -- external detection ----------------------------------------------------

def test_world_identity_is_external():
    assert _is_external({"identity": IDENTITY_WORLD}) is True


def test_reserved_world_label_is_external_even_without_the_identity():
    assert _is_external({"labels": ["reserved:world"]}) is True


def test_an_in_cluster_pod_is_not_external():
    assert _is_external(
        {"identity": 4242, "namespace": "shop", "pod_name": "cart-abc"}
    ) is False


def test_the_apiserver_is_not_treated_as_external():
    """It is outside the pod network but very much inside the cluster."""
    assert _is_external({"identity": 7}) is False


# -- summarization ---------------------------------------------------------

def test_only_egress_to_external_destinations_is_summarized():
    flows = [
        _flow(src_pod="api-7d9f8b5c4-x2k9p", dst_names=["api.stripe.com"]),
        # in-cluster: already covered by the dependency graph
        _flow(src_pod="api-7d9f8b5c4-x2k9p", dst_identity=4242,
              dst_ns="data", dst_pod="postgres-0"),
        # ingress: not egress
        _flow(src_pod="api-7d9f8b5c4-x2k9p", direction="INGRESS",
              dst_names=["api.stripe.com"]),
    ]
    result = summarize_egress(flows)
    assert result["flows_examined"] == 3
    assert result["external_egress_flows"] == 1
    assert list(result["workloads"]) == ["default/Deployment/api"]


def test_a_dns_name_is_preferred_over_the_ip():
    """A cloud IP tells an operator nothing and changes under the same name."""
    result = summarize_egress(
        [_flow(src_pod="api-7d9f8b5c4-x2k9p", dst_names=["api.stripe.com"])]
    )
    entry = result["workloads"]["default/Deployment/api"][0]
    assert entry["destination"] == "api.stripe.com"
    assert entry["dns_resolved"] is True


def test_a_destination_with_no_dns_falls_back_to_the_ip():
    result = summarize_egress(
        [_flow(src_pod="api-7d9f8b5c4-x2k9p", dst_names=[], dst_ip="203.0.113.9")]
    )
    entry = result["workloads"]["default/Deployment/api"][0]
    assert entry["destination"] == "203.0.113.9"
    assert entry["dns_resolved"] is False


def test_flows_to_the_same_destination_are_aggregated():
    flows = [
        _flow(src_pod="api-7d9f8b5c4-x2k9p", dst_names=["api.stripe.com"], port=443)
        for _ in range(5)
    ] + [
        _flow(src_pod="api-7d9f8b5c4-x2k9p", dst_names=["api.stripe.com"], port=8443)
    ]
    entry = summarize_egress(flows)["workloads"]["default/Deployment/api"][0]
    assert entry["flow_count"] == 6
    assert entry["ports"] == [443, 8443]


def test_dropped_flows_are_counted_separately():
    flows = [
        _flow(src_pod="api-7d9f8b5c4-x2k9p", dst_names=["evil.test"], verdict="DROPPED"),
        _flow(src_pod="api-7d9f8b5c4-x2k9p", dst_names=["evil.test"], verdict="DROPPED"),
        _flow(src_pod="api-7d9f8b5c4-x2k9p", dst_names=["evil.test"]),
    ]
    result = summarize_egress(flows)
    entry = result["workloads"]["default/Deployment/api"][0]
    assert entry["flow_count"] == 3
    assert entry["dropped_count"] == 2
    assert result["dropped_by_workload"]["default/Deployment/api"] == 2


def test_destinations_are_ordered_by_volume():
    flows = (
        [_flow(src_pod="api-7d9f8b5c4-x2k9p", dst_names=["rare.test"])]
        + [_flow(src_pod="api-7d9f8b5c4-x2k9p", dst_names=["busy.test"]) for _ in range(9)]
    )
    entries = summarize_egress(flows)["workloads"]["default/Deployment/api"]
    assert entries[0]["destination"] == "busy.test"
