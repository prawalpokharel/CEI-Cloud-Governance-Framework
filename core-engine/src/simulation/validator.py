"""
Patent Module 110: Pre-Modification Validator
Simulates the impact of proposed changes on a k-hop subgraph before execution.
Patent Section 5 / Paper Section VII.

"If cumulative weighted centrality changes exceed a safety threshold or
violate governance constraints (e.g., minimum replicas), the action is aborted."
"""
import networkx as nx
from typing import Dict, List, Any, Optional


class PreModificationValidator:
    """
    Validates proposed infrastructure modifications by simulating their
    impact on the k-hop neighborhood before allowing execution.
    """

    def validate(
        self,
        graph: nx.DiGraph,
        cei_results: Dict[str, Dict],
        fault_risks: Dict[str, Dict],
        governance_policies: Dict,
        safety_threshold: float = 0.7,
        k_hop: int = 2,
    ) -> Dict[str, Dict[str, Any]]:
        """
        For each node, validate whether proposed modification is safe.
        
        Checks:
        1. k-hop neighborhood impact (cumulative centrality change)
        2. Governance constraint compliance (minimum replicas)
        3. Fault propagation cascade risk
        """
        validated = {}

        # Total centrality across the graph, used to express a node's k-hop
        # blast radius as a fraction of the whole system rather than as a raw
        # sum. See _simulate_k_hop_impact for why.
        total_centrality = sum(
            d.get("centrality", 0.0) for d in cei_results.values()
        )

        for node_id, cei_data in cei_results.items():
            classification = cei_data.get("classification", "moderate")

            # Determine proposed action based on classification
            if classification == "low":
                proposed_action = "consolidate"
            elif classification == "critical":
                proposed_action = "scale_up"
            elif classification == "elevated":
                proposed_action = "monitor"
            else:
                proposed_action = "no_action"

            # Run k-hop impact simulation
            impact = self._simulate_k_hop_impact(
                graph, node_id, cei_results, k_hop, total_centrality
            )

            # Check governance constraints
            governance_check = self._check_governance_constraints(
                node_id, proposed_action, governance_policies
            )

            # Check fault propagation risk
            node_fault = fault_risks.get(node_id, {})
            cascade_risk = node_fault.get("cascade_risk", 0.0)

            # Determine if modification is safe.
            #
            # Compared against the FRACTION of system centrality inside the
            # blast radius, not the raw sum. The sum is unbounded and grows
            # with neighbourhood size (3 neighbours -> 1.33, 10 -> 4.27, while
            # the mean holds at ~0.43), so comparing it to a [0, 1] threshold
            # meant every node with a connected neighbour was blocked and no
            # recommendation was ever issued.
            is_safe = (
                impact["cumulative_centrality_fraction"] < safety_threshold and
                governance_check["compliant"] and
                cascade_risk < safety_threshold
            )

            # Override: never consolidate critical/mission-critical nodes
            if proposed_action == "consolidate" and cei_data.get("risk_factor", 0) > 0.7:
                is_safe = False
                proposed_action = "no_action"

            validated[node_id] = {
                **cei_data,
                "proposed_action": proposed_action,
                "is_safe": is_safe,
                "validation": {
                    "k_hop_impact": impact,
                    "governance_check": governance_check,
                    "cascade_risk": round(cascade_risk, 4),
                    "safety_threshold": safety_threshold,
                },
                "recommendation": proposed_action if is_safe else "no_action",
                "blocked_reason": self._get_block_reason(
                    is_safe, impact, governance_check, cascade_risk, safety_threshold
                ),
            }

        return validated

    def _simulate_k_hop_impact(
        self,
        graph: nx.DiGraph,
        node_id: str,
        cei_results: Dict,
        k: int,
        total_centrality: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Simulate impact on the k-hop neighborhood.

        Reports the cumulative weighted centrality inside the blast radius
        both as a raw sum (retained: it is informative, and appears in the
        API response) and as a fraction of the whole graph's centrality.

        The fraction is what the safety check uses. It answers a question with
        a stable scale -- "what proportion of total system criticality does
        touching this node put at risk?" -- so one threshold is meaningful
        across topologies of different sizes and densities. Measured across
        the five reference scenarios it spans 0.14 to 0.90, and blocks in a
        way that tracks real coupling: a dense tactical mesh medians 0.82
        while a sparse, deliberately redundant network stays below 0.6.
        """
        if node_id not in graph.nodes():
            return {
                "k_hop_nodes": [],
                "k_hop_count": 0,
                "cumulative_centrality_change": 0.0,
                "cumulative_centrality_fraction": 0.0,
                "max_single_impact": 0.0,
            }

        # Get k-hop neighbors
        neighbors = set()
        current = {node_id}
        for _ in range(k):
            next_level = set()
            for n in current:
                next_level.update(graph.successors(n))
                next_level.update(graph.predecessors(n))
            neighbors.update(current)
            current = next_level - neighbors
        neighbors.update(current)
        neighbors.discard(node_id)

        # Compute cumulative centrality in neighborhood. Named distinctly from
        # the total_centrality parameter, which is the whole-graph total.
        neighborhood_centrality = 0.0
        max_impact = 0.0
        for neighbor in neighbors:
            neighbor_cei = cei_results.get(neighbor, {})
            centrality = neighbor_cei.get("centrality", 0.0)
            neighborhood_centrality += centrality
            max_impact = max(max_impact, centrality)

        fraction = (
            neighborhood_centrality / total_centrality
            if total_centrality > 0
            else 0.0
        )

        return {
            # Sorted, not raw set order: Python salts string hashing per
            # process (PYTHONHASHSEED), so list(set) returned the same
            # neighborhood in a different order on every deploy. The values
            # were always correct; only the ordering was unstable, which
            # defeats response diffing and caching for clients.
            "k_hop_nodes": sorted(neighbors),
            "k_hop_count": len(neighbors),
            "cumulative_centrality_change": round(neighborhood_centrality, 4),
            "cumulative_centrality_fraction": round(fraction, 4),
            "max_single_impact": round(max_impact, 4),
        }

    def _check_governance_constraints(
        self,
        node_id: str,
        proposed_action: str,
        policies: Dict
    ) -> Dict[str, Any]:
        """Check if proposed action violates governance constraints."""
        violations = []
        node_overrides = policies.get("node_overrides", {}).get(node_id, {})
        min_replicas = node_overrides.get("min_replicas",
                                          policies.get("min_replicas_global", 1))

        if proposed_action == "consolidate" and min_replicas > 1:
            violations.append(
                f"Cannot consolidate: minimum replicas requirement ({min_replicas})"
            )

        return {
            "compliant": len(violations) == 0,
            "violations": violations,
            "min_replicas": min_replicas,
        }

    def _get_block_reason(
        self, is_safe, impact, governance, cascade_risk, threshold
    ) -> Optional[str]:
        """Generate human-readable reason for blocked modification."""
        if is_safe:
            return None
        reasons = []
        if impact["cumulative_centrality_fraction"] >= threshold:
            reasons.append(
                f"k-hop blast radius covers "
                f"{impact['cumulative_centrality_fraction']:.0%} of system "
                f"centrality (threshold {threshold:.0%})"
            )
        if not governance["compliant"]:
            reasons.append("; ".join(governance["violations"]))
        if cascade_risk >= threshold:
            reasons.append("cascade risk too high")
        return "; ".join(reasons) if reasons else "blocked by safety check"
