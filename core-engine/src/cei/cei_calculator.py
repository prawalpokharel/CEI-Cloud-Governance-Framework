"""
Patent Module 106: Centrality-Entropy Index (CEI) Calculator
Implements the core CEI formula from the patent:
    CEI_i(t) = alpha(t) * C_i(t) + beta(t) * H_i(t) + gamma(t) * R_i(t)

Where:
- C_i = graph centrality metric (PageRank or betweenness)
- H_i = Shannon entropy of resource demand distribution
- R_i = risk factor from governance policies
- alpha, beta, gamma = time-varying adaptive weights
"""
import numpy as np
import networkx as nx
from typing import Dict, List, Any


class CEICalculator:
    """
    Computes the Centrality-Entropy Index for each node in the infrastructure
    dependency graph. The CEI determines modification priority: nodes with
    higher CEI are candidates for scaling up, while nodes with persistently
    low CEI may be consolidated or decommissioned.
    
    Implements Patent Section 2 / Paper Section IV.
    """

    # Classification thresholds
    CRITICAL_THRESHOLD = 0.75
    ELEVATED_THRESHOLD = 0.50
    MODERATE_THRESHOLD = 0.25

    def __init__(
        self,
        centrality_mode: str = "structural",
        suppress_entropy: bool = False,
    ):
        # "structural" is the original composite and remains the default, so
        # the scenario endpoints behind the USPTO/NIW pages are unaffected.
        # "blast_radius" emphasizes dependents and is used for live clusters.
        # See services/live_cei.CentralityMode for why this is explicit.
        self.centrality_mode = centrality_mode

        # When set, the entropy term is withheld and beta is redistributed
        # across alpha and gamma. Used when too little history exists to
        # measure workload variability -- withholding is honest, whereas the
        # alternative is a score built partly on fabricated data.
        self.suppress_entropy = suppress_entropy

    def compute(
        self,
        graph: nx.DiGraph,
        telemetry: List[Dict],
        risk_factors: Dict[str, float],
        weights: Dict[str, float]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Compute CEI for all nodes in the dependency graph.
        
        CEI_i(t) = alpha(t) * C_i(t) + beta(t) * H_i(t) + gamma(t) * R_i(t)
        """
        alpha = weights.get("alpha", 0.4)
        beta = weights.get("beta", 0.35)
        gamma = weights.get("gamma", 0.25)

        # Not enough history to measure variability. Redistribute beta across
        # the two terms that ARE measurable, in their existing proportion, so
        # scores stay on a [0, 1] scale and remain comparable to scores
        # computed once history exists.
        if self.suppress_entropy:
            measurable = alpha + gamma
            if measurable > 0:
                alpha += beta * (alpha / measurable)
                gamma += beta * (gamma / measurable)
            beta = 0.0

        # The weights actually applied, which differ from the recalibrator's
        # output when entropy is suppressed. Reporting the recalibrator's
        # values would have the response claim beta=0.35 while scoring with
        # beta=0 -- stating a number the result was not computed from.
        self.effective_weights = {
            "alpha": round(alpha, 4),
            "beta": round(beta, 4),
            "gamma": round(gamma, 4),
        }

        # Compute centrality metrics for all nodes (Patent: graph centrality)
        centrality_scores = self._compute_centrality(graph)

        # Build telemetry lookup
        telemetry_map = {n["node_id"]: n for n in telemetry}

        results = {}
        for node_id in graph.nodes():
            node_telemetry = telemetry_map.get(node_id, {})
            
            # C_i: Graph centrality
            c_i = centrality_scores.get(node_id, 0.0)

            # H_i: Shannon entropy of resource demand
            h_i = self._compute_entropy(node_telemetry)

            # R_i: Governance risk factor
            r_i = risk_factors.get(node_id, 0.0)

            # CEI computation (Patent core formula)
            cei_score = alpha * c_i + beta * h_i + gamma * r_i

            # Classify node based on CEI
            classification = self._classify(cei_score)

            results[node_id] = {
                "cei_score": round(cei_score, 4),
                "centrality": round(c_i, 4),
                "entropy": round(h_i, 4),
                "risk_factor": round(r_i, 4),
                "classification": classification,
                "components": {
                    "weighted_centrality": round(alpha * c_i, 4),
                    "weighted_entropy": round(beta * h_i, 4),
                    "weighted_risk": round(gamma * r_i, 4),
                },
                "metrics": node_telemetry.get("metrics", {}),
                "metadata": node_telemetry.get("metadata", {}),
            }

        return results

    def _compute_centrality(self, graph: nx.DiGraph) -> Dict[str, float]:
        """
        Compute composite centrality using PageRank and betweenness centrality.
        Patent specifies: "centrality metric (e.g., PageRank, betweenness)"
        We combine both for a more robust centrality score.
        """
        if len(graph.nodes()) == 0:
            return {}

        # PageRank centrality
        try:
            pagerank = nx.pagerank(graph, alpha=0.85)
        except nx.PowerIterationFailedConvergence:
            pagerank = {n: 1.0 / len(graph.nodes()) for n in graph.nodes()}

        if self.centrality_mode == "blast_radius":
            return self._blast_radius_centrality(graph, pagerank)

        # Betweenness centrality
        betweenness = nx.betweenness_centrality(graph)

        # Degree centrality
        degree = nx.degree_centrality(graph)

        # Closeness centrality
        try:
            closeness = nx.closeness_centrality(graph)
        except Exception:
            closeness = {n: 0.0 for n in graph.nodes()}

        # Composite: weighted combination
        composite = {}
        for node in graph.nodes():
            pr = pagerank.get(node, 0)
            bw = betweenness.get(node, 0)
            dg = degree.get(node, 0)
            cl = closeness.get(node, 0)
            # Normalize to [0,1] using weighted average
            composite[node] = 0.35 * pr + 0.30 * bw + 0.20 * dg + 0.15 * cl

        # Normalize composite scores to [0, 1]
        max_score = max(composite.values()) if composite else 1.0
        if max_score > 0:
            composite = {k: v / max_score for k, v in composite.items()}

        return composite

    def _blast_radius_centrality(
        self, graph: nx.DiGraph, pagerank: Dict[str, float]
    ) -> Dict[str, float]:
        """
        Centrality as "how much breaks if this fails".

        Edges run caller -> callee, so a node's DEPENDENTS are its
        predecessors. Two signals, both about being depended upon:

          * PageRank, which flows along edges toward callees, so a workload
            that many important callers reach scores highly.
          * Reachable-dependents: the share of the graph that can reach this
            node, i.e. the set that would be affected by its failure. Direct
            in-degree alone misses transitive impact -- a database behind one
            service that the whole system calls is not a leaf.

        Betweenness, out-degree, and closeness are excluded on purpose. They
        reward being a hub in either direction, which is what ranked an API
        gateway above the database everything depends on.
        """
        nodes = list(graph.nodes())
        total_others = max(1, len(nodes) - 1)

        reachable_dependents = {}
        reversed_graph = graph.reverse(copy=False)
        for node in nodes:
            # Descendants in the reversed graph = everything upstream that
            # transitively depends on this node.
            reachable_dependents[node] = (
                len(nx.descendants(reversed_graph, node)) / total_others
            )

        max_pagerank = max(pagerank.values()) if pagerank else 0.0
        composite = {}
        for node in nodes:
            pr = (pagerank.get(node, 0.0) / max_pagerank) if max_pagerank else 0.0
            composite[node] = 0.5 * pr + 0.5 * reachable_dependents[node]

        peak = max(composite.values()) if composite else 0.0
        if peak > 0:
            composite = {k: v / peak for k, v in composite.items()}
        return composite

    def _compute_entropy(self, node_telemetry: Dict) -> float:
        """
        Compute Shannon entropy of resource demand distribution over a
        sliding window. High entropy = unpredictable/varied workload.
        Low entropy = stable/predictable workload.
        
        Patent: "H_i is the entropy of resource demand distribution"
        Paper Section IV: "Shannon entropy of resource demand distribution
        over a sliding window"
        """
        history = node_telemetry.get("utilization_history", [])
        if not history:
            metrics = node_telemetry.get("metrics", {})
            cpu = metrics.get("cpu_utilization", 0.5)
            mem = metrics.get("memory_utilization", 0.5)
            # Simple entropy from current metrics
            values = [cpu, mem, 1 - cpu, 1 - mem]
            values = [v for v in values if v > 0]
            if not values:
                return 0.0
            total = sum(values)
            probs = [v / total for v in values]
            return float(-sum(p * np.log2(p) for p in probs if p > 0))

        # Compute entropy from utilization history
        cpu_values = [h.get("cpu", 0.5) for h in history]
        mem_values = [h.get("memory", 0.5) for h in history]

        # Discretize into bins for probability distribution
        cpu_entropy = self._shannon_entropy(cpu_values)
        mem_entropy = self._shannon_entropy(mem_values)

        # Combined entropy normalized to [0, 1]
        combined = (cpu_entropy + mem_entropy) / 2.0
        max_entropy = np.log2(10)  # 10 bins
        return min(1.0, combined / max_entropy) if max_entropy > 0 else 0.0

    def _shannon_entropy(self, values: List[float], bins: int = 10) -> float:
        """Compute Shannon entropy from a list of values."""
        if not values:
            return 0.0
        hist, _ = np.histogram(values, bins=bins, range=(0, 1), density=True)
        # Normalize to probability distribution
        total = hist.sum()
        if total == 0:
            return 0.0
        probs = hist / total
        # Shannon entropy: -sum(p * log2(p))
        entropy = -sum(p * np.log2(p) for p in probs if p > 0)
        # A single occupied bin gives p=1 and log2(1)=0, so the negation
        # yields -0.0, which renders as "-0.000" and reads like a defect.
        return max(0.0, float(entropy))

    def _classify(self, cei_score: float) -> str:
        """
        Classify node based on CEI score.
        Higher CEI = more critical/complex = scale up candidate.
        Lower CEI = less critical = consolidation candidate.
        """
        if cei_score >= self.CRITICAL_THRESHOLD:
            return "critical"
        elif cei_score >= self.ELEVATED_THRESHOLD:
            return "elevated"
        elif cei_score >= self.MODERATE_THRESHOLD:
            return "moderate"
        else:
            return "low"
