"""
The graph below the cluster.

Everything else in this product reasons about a dependency graph whose
vertices are all Kubernetes workloads. That graph is missing the failures that
actually take companies down for a day.

A cluster's real dependency set does not stop at its own boundary. It includes
the RDS instance five services share, the identity provider every request is
validated against, the DNS zone that resolves all of it, and the S3 bucket
holding the assets. None of those appear in any Kubernetes object, so none of
them appear in a graph built from the Kubernetes API -- and the resulting
picture shows fourteen independent services where the truth is fourteen
services with one shared point of failure.

## Two weak sources, joined, make one strong one

Neither input is sufficient alone:

* **Observed egress** (Hubble) says a workload talks to
  `prod-db.abc123.us-east-1.rds.amazonaws.com`. It does not say what that is,
  who owns it, or whether losing it matters.
* **Terraform state** says an `aws_db_instance` named `prod-db` exists with
  that endpoint, in that account, with those parameters. It does not say
  which workloads actually use it -- IaC records intent, and the service that
  stopped calling it two years ago still has the config.

Joined on the endpoint, they answer both halves: this specific provisioned
resource, used by these specific workloads, right now. That join is the whole
design. Either source alone produces a list somebody has to interpret; the
two together produce an edge.

## What the graph is for

**Hidden coupling.** Two workloads with no path between them inside the
cluster, both depending on the same external endpoint, are not independent --
they simply look independent to every tool that stops at the cluster edge.
This is the finding that justifies the module: it is invisible by
construction to Kubernetes-only analysis, and it is exactly the shape of a
correlated outage.

**Category matters more than count.** Losing a metrics endpoint degrades
dashboards. Losing the identity provider fails every authenticated request in
the estate simultaneously, whatever else is healthy. So external nodes carry
a category and blast radius is weighted by it, rather than counting all
external dependencies alike.

**Unmanaged dependencies.** An endpoint that appears in observed traffic but
in no Terraform state is a dependency nobody declared: no owner, no change
process, and nothing to rebuild from. It is the ownerless-workload problem
one level down, where it is harder to see and worse to hit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from .blast_radius import build_dependency_graph

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# External service categories, ordered by how much of a system a failure takes
# with it. Identity is first deliberately: an auth provider outage fails every
# authenticated request everywhere at once, which no amount of internal
# redundancy survives.
CATEGORY_WEIGHT = {
    "identity": 1.0,      # Auth0, Okta, Cognito, Entra -- fails everything
    "dns": 1.0,           # nothing resolves, including retries
    "database": 0.9,      # RDS, Cloud SQL, Atlas -- stateful, no failover by default
    "payments": 0.8,      # revenue stops; usually a narrow blast radius
    "cache": 0.6,         # degraded, often survivable
    "storage": 0.6,       # S3, GCS -- depends entirely on what is stored
    "messaging": 0.6,     # SQS, Kafka, PubSub -- backs up before it breaks
    "email": 0.4,
    "observability": 0.2,  # dashboards go dark; the system keeps serving
    "registry": 0.2,      # only bites on the next pull
    "unknown": 0.5,
}

# Hostname patterns, most specific first. Matched against the suffix of an
# observed destination.
_CATEGORY_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\.rds\.amazonaws\.com$"), "database", "AWS RDS"),
    (re.compile(r"\.cache\.amazonaws\.com$"), "cache", "AWS ElastiCache"),
    (re.compile(r"\.redshift\.amazonaws\.com$"), "database", "AWS Redshift"),
    (re.compile(r"(^|\.)s3[.-][\w-]*\.?amazonaws\.com$"), "storage", "AWS S3"),
    (re.compile(r"\.dynamodb\.[\w-]+\.amazonaws\.com$"), "database", "AWS DynamoDB"),
    (re.compile(r"\.sqs\.[\w-]+\.amazonaws\.com$"), "messaging", "AWS SQS"),
    (re.compile(r"\.sns\.[\w-]+\.amazonaws\.com$"), "messaging", "AWS SNS"),
    (re.compile(r"\.kafka\.[\w-]+\.amazonaws\.com$"), "messaging", "AWS MSK"),
    (re.compile(r"\.elb\.amazonaws\.com$"), "unknown", "AWS load balancer"),
    (re.compile(r"\.secretsmanager\.[\w-]+\.amazonaws\.com$"), "identity", "AWS Secrets Manager"),
    (re.compile(r"\.sts\.amazonaws\.com$|^sts\.amazonaws\.com$"), "identity", "AWS STS"),
    (re.compile(r"\.database\.windows\.net$"), "database", "Azure SQL"),
    (re.compile(r"\.blob\.core\.windows\.net$"), "storage", "Azure Blob Storage"),
    (re.compile(r"\.vault\.azure\.net$"), "identity", "Azure Key Vault"),
    (re.compile(r"\.servicebus\.windows\.net$"), "messaging", "Azure Service Bus"),
    (re.compile(r"\.documents\.azure\.com$"), "database", "Azure Cosmos DB"),
    (re.compile(r"^sqladmin\.googleapis\.com$"), "database", "Cloud SQL"),
    (re.compile(r"^storage\.googleapis\.com$"), "storage", "Google Cloud Storage"),
    (re.compile(r"^pubsub\.googleapis\.com$"), "messaging", "Google Pub/Sub"),
    (re.compile(r"^secretmanager\.googleapis\.com$"), "identity", "GCP Secret Manager"),
    (re.compile(r"\.mongodb\.net$"), "database", "MongoDB Atlas"),
    (re.compile(r"\.redis\.cloud$|\.redislabs\.com$"), "cache", "Redis Cloud"),
    (re.compile(r"\.auth0\.com$"), "identity", "Auth0"),
    (re.compile(r"\.okta\.com$|\.oktapreview\.com$"), "identity", "Okta"),
    (re.compile(r"^login\.microsoftonline\.com$"), "identity", "Microsoft Entra ID"),
    (re.compile(r"\.onelogin\.com$"), "identity", "OneLogin"),
    (re.compile(r"^accounts\.google\.com$"), "identity", "Google Identity"),
    (re.compile(r"\.clerk\.accounts\.dev$|\.clerk\.com$"), "identity", "Clerk"),
    (re.compile(r"^api\.stripe\.com$"), "payments", "Stripe"),
    (re.compile(r"\.braintreegateway\.com$"), "payments", "Braintree"),
    (re.compile(r"^api\.paypal\.com$"), "payments", "PayPal"),
    (re.compile(r"^api\.twilio\.com$"), "email", "Twilio"),
    (re.compile(r"\.sendgrid\.net$|^api\.sendgrid\.com$"), "email", "SendGrid"),
    (re.compile(r"\.mailgun\.(net|org)$"), "email", "Mailgun"),
    (re.compile(r"\.datadoghq\.com$"), "observability", "Datadog"),
    (re.compile(r"\.sentry\.io$"), "observability", "Sentry"),
    (re.compile(r"\.newrelic\.com$"), "observability", "New Relic"),
    (re.compile(r"\.grafana\.net$"), "observability", "Grafana Cloud"),
    (re.compile(r"\.honeycomb\.io$"), "observability", "Honeycomb"),
    (re.compile(r"^(docker\.io|registry-1\.docker\.io)$"), "registry", "Docker Hub"),
    (re.compile(r"^(ghcr\.io|quay\.io|gcr\.io)$|\.pkg\.dev$"), "registry", "container registry"),
    (re.compile(r"^(pypi\.org|registry\.npmjs\.org|rubygems\.org)$"), "registry", "package registry"),
    (re.compile(r"\.pythonhosted\.org$"), "registry", "package registry"),
    (re.compile(r"^(1\.1\.1\.1|8\.8\.8\.8|8\.8\.4\.4)$"), "dns", "public DNS resolver"),
]

# Terraform resource types -> (category, endpoint attribute names). The
# attribute list is ordered: the first present one is the address workloads
# actually connect to.
TERRAFORM_RESOURCES: dict[str, tuple[str, tuple[str, ...]]] = {
    "aws_db_instance": ("database", ("endpoint", "address")),
    "aws_rds_cluster": ("database", ("endpoint", "reader_endpoint")),
    "aws_elasticache_cluster": ("cache", ("cache_nodes", "configuration_endpoint")),
    "aws_elasticache_replication_group": ("cache", ("primary_endpoint_address", "configuration_endpoint_address")),
    "aws_s3_bucket": ("storage", ("bucket_domain_name", "bucket_regional_domain_name", "bucket")),
    "aws_sqs_queue": ("messaging", ("url", "id")),
    "aws_sns_topic": ("messaging", ("arn",)),
    "aws_msk_cluster": ("messaging", ("bootstrap_brokers", "bootstrap_brokers_tls")),
    "aws_dynamodb_table": ("database", ("name", "arn")),
    "aws_lb": ("unknown", ("dns_name",)),
    "aws_elasticsearch_domain": ("database", ("endpoint",)),
    "aws_opensearch_domain": ("database", ("endpoint",)),
    "azurerm_postgresql_flexible_server": ("database", ("fqdn",)),
    "azurerm_mssql_server": ("database", ("fully_qualified_domain_name",)),
    "azurerm_storage_account": ("storage", ("primary_blob_endpoint", "name")),
    "azurerm_redis_cache": ("cache", ("hostname",)),
    "azurerm_key_vault": ("identity", ("vault_uri",)),
    "google_sql_database_instance": ("database", ("connection_name", "first_ip_address")),
    "google_storage_bucket": ("storage", ("url", "name")),
    "google_redis_instance": ("cache", ("host",)),
    "google_pubsub_topic": ("messaging", ("name",)),
    "mongodbatlas_cluster": ("database", ("connection_strings", "srv_address")),
}

# A destination this many workloads reach is shared infrastructure whatever
# it is. Two is coupling; three is a pattern.
CONVERGENCE_THRESHOLD = 3


@dataclass
class ExternalNode:
    endpoint: str
    category: str
    provider: str | None = None
    # "egress" (observed), "terraform" (declared), or "both" (joined).
    provenance: str = "egress"
    terraform_address: str | None = None
    dependents: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    dns_resolved: bool = True

    @property
    def key(self) -> str:
        return f"external/{self.category}/{self.endpoint}"

    @property
    def weight(self) -> float:
        return CATEGORY_WEIGHT.get(self.category, CATEGORY_WEIGHT["unknown"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "endpoint": self.endpoint,
            "category": self.category,
            "provider": self.provider,
            "provenance": self.provenance,
            "terraform_address": self.terraform_address,
            "dependents": sorted(self.dependents),
            "dependent_count": len(self.dependents),
            "ports": sorted(self.ports),
            "category_weight": self.weight,
            "dns_resolved": self.dns_resolved,
        }


@dataclass
class Finding:
    kind: str
    severity: str
    title: str
    detail: str
    endpoint: str | None = None
    category: str | None = None
    evidence: dict = field(default_factory=dict)
    weighted_impact: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "endpoint": self.endpoint,
            "category": self.category,
            "evidence": self.evidence,
            "weighted_impact": round(self.weighted_impact, 4),
        }


def classify(endpoint: str) -> tuple[str, str | None]:
    """Map a hostname to (category, provider name)."""
    host = (endpoint or "").strip().lower().rstrip(".")
    if not host:
        return "unknown", None
    # Strip a scheme and port if the caller passed a URL.
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host).split("/")[0]
    host = re.sub(r":\d+$", "", host)

    for pattern, category, provider in _CATEGORY_PATTERNS:
        if pattern.search(host):
            return category, provider
    return "unknown", None


def _flatten_attribute(value: Any) -> str | None:
    """
    Terraform attributes are not always strings.

    `aws_elasticache_cluster.cache_nodes` is a list of objects,
    `mongodbatlas_cluster.connection_strings` is a list of maps. Take the
    first address-looking thing rather than stringifying the structure.
    """
    if isinstance(value, str):
        return value or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            found = _flatten_attribute(item)
            if found:
                return found
        return None
    if isinstance(value, dict):
        for key in ("address", "endpoint", "host", "standard", "standard_srv", "url"):
            if key in value:
                found = _flatten_attribute(value[key])
                if found:
                    return found
        return None
    return None


def parse_terraform_state(state: dict | str) -> list[dict[str, Any]]:
    """
    Extract managed resources and their endpoints from Terraform state.

    Reads state rather than `.tf` source deliberately. Source describes what
    was asked for, with variables and modules unresolved; state records what
    exists, with the actual endpoint the provider assigned -- and the endpoint
    is the join key against observed traffic. A `.tf` file containing
    `identifier = var.db_name` cannot be matched to anything.

    Supports state format versions 3 and 4 (v4 is current; v3 still turns up
    in older repositories).
    """
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except json.JSONDecodeError:
            return []
    if not isinstance(state, dict):
        return []

    found: list[dict[str, Any]] = []

    def record(resource_type: str, name: str, attributes: dict, module: str | None):
        spec = TERRAFORM_RESOURCES.get(resource_type)
        if not spec or not isinstance(attributes, dict):
            return
        category, attribute_names = spec
        endpoint = None
        for attribute in attribute_names:
            endpoint = _flatten_attribute(attributes.get(attribute))
            if endpoint:
                break
        if not endpoint:
            return
        # Endpoints often carry a port; the hostname is the join key.
        host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", str(endpoint)).split("/")[0]
        host = re.sub(r":\d+$", "", host).lower()
        address = f"{module + '.' if module else ''}{resource_type}.{name}"
        found.append({
            "terraform_address": address,
            "type": resource_type,
            "name": name,
            "endpoint": host,
            "category": category,
        })

    # v4: top-level "resources", each with "instances".
    for resource in state.get("resources") or []:
        if resource.get("mode") == "data":
            continue  # data sources read existing infrastructure, not own it
        for instance in resource.get("instances") or []:
            record(
                resource.get("type") or "",
                resource.get("name") or "",
                instance.get("attributes") or {},
                resource.get("module"),
            )

    # v3: "modules" -> "resources" keyed by address, attributes are flat
    # strings with dotted keys.
    for module in state.get("modules") or []:
        path = ".".join(module.get("path") or [])
        for address, resource in (module.get("resources") or {}).items():
            primary = (resource.get("primary") or {}).get("attributes") or {}
            record(
                resource.get("type") or address.split(".")[0],
                address.split(".")[-1],
                primary,
                path if path and path != "root" else None,
            )

    return found


def build_external_nodes(
    snapshot: dict,
    egress_summary: dict | None = None,
    terraform_state: dict | str | None = None,
) -> dict[str, ExternalNode]:
    """
    Assemble external dependency nodes from observed traffic and IaC state.

    Traffic decides which nodes exist -- a resource nothing calls is not a
    dependency, whatever Terraform says. Terraform then enriches what traffic
    found with an owner, a type, and a name a human recognises.
    """
    nodes: dict[str, ExternalNode] = {}

    declared = {
        entry["endpoint"]: entry
        for entry in parse_terraform_state(terraform_state or {})
    }

    workloads = (egress_summary or {}).get("workloads") or {}
    for workload_key, destinations in workloads.items():
        for entry in destinations:
            endpoint = (entry.get("destination") or "").strip().lower().rstrip(".")
            if not endpoint or endpoint == "unknown":
                continue

            match = declared.get(endpoint)
            category, provider = classify(endpoint)
            if match:
                # Terraform is authoritative about what a resource IS; a
                # hostname pattern is a guess by comparison.
                category = match["category"]
                provider = provider or match["type"]

            node = nodes.get(endpoint)
            if node is None:
                node = ExternalNode(
                    endpoint=endpoint,
                    category=category,
                    provider=provider,
                    provenance="both" if match else "egress",
                    terraform_address=match["terraform_address"] if match else None,
                    dns_resolved=bool(entry.get("dns_resolved", True)),
                )
                nodes[endpoint] = node
            if workload_key not in node.dependents:
                node.dependents.append(workload_key)
            for port in entry.get("ports") or []:
                if port not in node.ports:
                    node.ports.append(port)

    return nodes


def build_combined_graph(
    snapshot: dict, external: dict[str, ExternalNode]
) -> nx.DiGraph:
    """
    The in-cluster graph with external endpoints attached as vertices.

    Edges run workload -> external, matching the existing convention that a
    source depends on its target. That means every traversal already written
    against this graph -- blast radius included -- treats an external endpoint
    as just another thing that can fail.
    """
    graph = build_dependency_graph(snapshot)
    for node in external.values():
        graph.add_node(
            node.key,
            kind="External",
            category=node.category,
            endpoint=node.endpoint,
            is_workload=False,
            is_external=True,
        )
        for dependent in node.dependents:
            if dependent in graph:
                graph.add_edge(
                    dependent, node.key,
                    confidence=1.0,  # observed traffic, not inferred
                    source_kind="observed_egress",
                )
    return graph


def _independent(graph: nx.DiGraph, a: str, b: str) -> bool:
    """
    Whether two workloads look unrelated inside the cluster.

    Undirected reachability over the in-cluster graph only: if neither can
    reach the other by any route, nothing about the Kubernetes topology
    suggests they share a fate. That is precisely the pair worth reporting
    when they turn out to share an external dependency.
    """
    if a == b:
        return False
    internal = graph.subgraph([
        n for n, d in graph.nodes(data=True) if not d.get("is_external")
    ]).to_undirected()
    if a not in internal or b not in internal:
        return True
    return not nx.has_path(internal, a, b)


def analyze(
    snapshot: dict,
    egress_summary: dict | None = None,
    terraform_state: dict | str | None = None,
    cei_by_workload: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """
    Find shared external dependencies and the coupling they create.
    """
    cei_by_workload = cei_by_workload or {}

    if not (egress_summary or {}).get("available", False):
        return {
            "available": False,
            "reason": (
                (egress_summary or {}).get("reason")
                or "No egress data. External dependency mapping requires flow "
                   "observation (Cilium with Hubble) to know what the cluster "
                   "actually talks to."
            ),
            "summary": {"external_nodes": 0},
            "external_nodes": [],
            "findings": [],
        }

    external = build_external_nodes(snapshot, egress_summary, terraform_state)
    graph = build_combined_graph(snapshot, external)
    findings: list[Finding] = []

    for node in external.values():
        count = len(node.dependents)
        weighted = count * node.weight
        cei_mass = sum(
            (cei_by_workload.get(k) or {}).get("cei_score") or 0.0
            for k in node.dependents
        )

        # --- convergence -------------------------------------------------
        if count >= CONVERGENCE_THRESHOLD:
            severity = (
                "critical" if node.weight >= 0.9
                else "warning" if node.weight >= 0.5
                else "info"
            )
            label = node.provider or node.category
            findings.append(Finding(
                kind="external_convergence",
                severity=severity,
                title=(
                    f"{count} workloads depend on {node.endpoint}"
                    + (f" ({label})" if label and label != "unknown" else "")
                ),
                detail=(
                    f"A single {node.category} endpoint outside the cluster is "
                    f"on the path of {count} workloads. Nothing inside "
                    "Kubernetes shows this: each workload's manifest names it "
                    "independently, and no Kubernetes object represents the "
                    "shared resource. When it fails, all "
                    f"{count} fail together, and internal redundancy does not "
                    "help because the redundant copies share the dependency."
                    + (
                        "\n\nIdentity and DNS are the worst case: every "
                        "authenticated request in the estate fails at once, "
                        "regardless of how healthy the rest of the system is."
                        if node.category in ("identity", "dns") else ""
                    )
                ),
                endpoint=node.endpoint,
                category=node.category,
                evidence={
                    "dependents": sorted(node.dependents),
                    "provider": node.provider,
                    "provenance": node.provenance,
                    "terraform_address": node.terraform_address,
                    "ports": sorted(node.ports),
                    "affected_cei_mass": round(cei_mass, 4),
                },
                weighted_impact=weighted,
            ))

        # --- hidden coupling ----------------------------------------------
        # Pairs that share this endpoint while having no in-cluster path
        # between them. Reported as a set rather than pair-by-pair: N
        # mutually-unrelated workloads produce N(N-1)/2 pairs and one fact.
        if count >= 2:
            unrelated = [
                key for key in node.dependents
                if all(
                    _independent(graph, key, other)
                    for other in node.dependents if other != key
                )
            ]
            if len(unrelated) >= 2:
                findings.append(Finding(
                    kind="hidden_coupling",
                    severity="critical" if node.weight >= 0.8 else "warning",
                    title=(
                        f"{len(unrelated)} workloads look independent but share "
                        f"{node.endpoint}"
                    ),
                    detail=(
                        "These workloads have no dependency path between them "
                        "inside the cluster, so every Kubernetes-scoped view "
                        "shows them as unrelated. They are not: they share an "
                        "external "
                        f"{node.category} dependency and will fail together. "
                        "This is the coupling that makes an incident look "
                        "like several unrelated incidents at once."
                    ),
                    endpoint=node.endpoint,
                    category=node.category,
                    evidence={
                        "workloads": sorted(unrelated),
                        "provider": node.provider,
                        "shared_endpoint": node.endpoint,
                    },
                    weighted_impact=len(unrelated) * node.weight,
                ))

        # --- undeclared ----------------------------------------------------
        if (
            node.provenance == "egress"
            and terraform_state
            and node.category not in ("registry", "observability")
            and count >= 1
        ):
            findings.append(Finding(
                kind="undeclared_dependency",
                severity="warning" if node.weight >= 0.8 else "info",
                title=f"{node.endpoint} is used but not declared in Terraform",
                detail=(
                    "Traffic reaches this endpoint, but no resource in the "
                    "supplied state provisions it. Either it is managed "
                    "somewhere else, or nobody manages it -- and a dependency "
                    "with no declaration has no owner, no change process, and "
                    "nothing to rebuild from."
                ),
                endpoint=node.endpoint,
                category=node.category,
                evidence={"dependents": sorted(node.dependents)},
                weighted_impact=weighted * 0.5,
            ))

        # --- bare IP --------------------------------------------------------
        if not node.dns_resolved:
            findings.append(Finding(
                kind="external_bare_ip",
                severity="info",
                title=f"{node.endpoint} is reached by IP with no DNS name",
                detail=(
                    "An external dependency addressed by literal IP cannot "
                    "fail over, cannot be re-pointed without a deploy, and "
                    "breaks silently when the provider changes it."
                ),
                endpoint=node.endpoint,
                category=node.category,
                evidence={"dependents": sorted(node.dependents)},
                weighted_impact=weighted * 0.3,
            ))

    findings.sort(key=lambda f: (
        SEVERITY_ORDER.get(f.severity, 9), -f.weighted_impact, f.endpoint or "",
    ))

    by_category: dict[str, int] = {}
    for node in external.values():
        by_category[node.category] = by_category.get(node.category, 0) + 1

    ranked = sorted(
        external.values(),
        key=lambda n: (-(len(n.dependents) * n.weight), n.endpoint),
    )

    return {
        "available": True,
        "summary": {
            "external_nodes": len(external),
            "by_category": by_category,
            "joined_with_terraform": sum(
                1 for n in external.values() if n.provenance == "both"
            ),
            "total_findings": len(findings),
            "by_severity": {
                s: sum(1 for f in findings if f.severity == s)
                for s in ("critical", "warning", "info")
                if any(f.severity == s for f in findings)
            },
            "note": (
                "External nodes come from observed egress; Terraform state "
                "supplies the resource type and owner where it matches. A "
                "dependency nothing has called during the observation window "
                "does not appear."
            ),
        },
        "external_nodes": [n.to_dict() for n in ranked],
        "findings": [f.to_dict() for f in findings],
    }
