"""
Patent Module 109: Fault Propagation Simulator
Models the spread of failures through the dependency graph.
Patent Section 5 / Paper Section VII.

"The simulator computes P_fail(i) as a function of centrality,
redundancy, and risk weight."
"""
import networkx as nx
import numpy as np
from typing import Dict, List, Any


class FaultPropagationSimulator:
    """
    Estimates failure propagation probability through the dependency graph.
    Used to preemptively dampen modifications that could cause cascading failures.
    """

    def __init__(self, propagation_decay: float = 0.7):
        self.propagation_decay = propagation_decay

    def simulate(
        self,
        graph: nx.DiGraph,
        cei_results: Dict[str, Dict],
        risk_factors: Dict[str, float]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Compute P_fail(i) for each node and simulate propagation paths.
        
        P_fail(i) = f(centrality, redundancy, risk_weight)
        """
        fault_risks = {}

        for node_id in graph.nodes():
            cei_data = cei_results.get(node_id, {})
            centrality = cei_data.get("centrality", 0.0)
            risk = risk_factors.get(node_id, 0.0)

            # Redundancy: inverse of in-degree (more dependencies = less redundancy)
            in_degree = graph.in_degree(node_id)
            out_degree = graph.out_degree(node_id)
            redundancy = 1.0 / (1 + out_degree)  # nodes with many dependents are less redundant

            # P_fail(i) = f(centrality, redundancy, risk)
            p_fail = self._compute_failure_probability(
                centrality, redundancy, risk
            )

            # Simulate propagation impact
            propagation_impact = self._simulate_propagation(
                graph, node_id, p_fail
            )

            fault_risks[node_id] = {
                "p_fail": round(p_fail, 4),
                "redundancy_score": round(redundancy, 4),
                "propagation_impact": propagation_impact,
                "affected_nodes": propagation_impact["affected_nodes"],
                "max_propagation_depth": propagation_impact["max_depth"],
                "cascade_risk": round(propagation_impact["cascade_score"], 4),
            }

        return fault_risks

    def _compute_failure_probability(
        self, centrality: float, redundancy: float, risk: float
    ) -> float:
        """
        Compute P_fail based on centrality, redundancy, and governance risk.
        High centrality + low redundancy + high risk = high failure probability.
        """
        p_fail = (0.4 * centrality +
                  0.3 * (1 - redundancy) +
                  0.3 * risk)
        return min(1.0, max(0.0, p_fail))

    def _simulate_propagation(
        self,
        graph: nx.DiGraph,
        source_node: str,
        initial_probability: float
    ) -> Dict[str, Any]:
        """
        Simulate failure propagation from a source node through the graph.
        Probability decays with each hop.
        """
        affected = {}
        visited = set()
        queue = [(source_node, initial_probability, 0)]

        while queue:
            node, prob, depth = queue.pop(0)
            if node in visited or prob < 0.01:
                continue
            visited.add(node)

            if node != source_node:
                affected[node] = {
                    "probability": round(prob, 4),
                    "depth": depth
                }

            # Propagate to successors with decay
            for successor in graph.successors(node):
                if successor not in visited:
                    edge_weight = graph[node][successor].get("weight", 1.0)
                    propagated_prob = prob * self.propagation_decay * edge_weight
                    queue.append((successor, propagated_prob, depth + 1))

        # Compute cascade score
        cascade_score = sum(
            a["probability"] for a in affected.values()
        ) / max(1, len(graph.nodes()))

        return {
            "affected_nodes": list(affected.keys()),
            "affected_details": affected,
            "max_depth": max((a["depth"] for a in affected.values()), default=0),
            "cascade_score": cascade_score,
        }
