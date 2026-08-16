"""Shared constants."""

# Namespaces holding cluster infrastructure rather than application workloads.
#
# Two rules depend on this and must not diverge: no NetworkPolicy is generated
# for these workloads, and no change to them is ever applied automatically. A
# cluster that reconfigures its own ingress controller or repairs its own
# control plane unattended is a cluster that can lock you out of it.
SYSTEM_NAMESPACES = frozenset({
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "cert-manager",
    "gatekeeper-system",
    "istio-system",
    "linkerd",
    "ingress-nginx",
})
