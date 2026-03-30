"""
Patent Module 103: Graph Constructor
Builds a dependency graph G=(V, E) where vertices V represent compute nodes
and edges E represent dependencies derived from network traffic analysis,
configuration manifests, and service mesh telemetry.
"""
import networkx as nx
from typing import Dict, List, Any, Optional


class DependencyGraphConstructor:
    """
    Constructs and analyzes the infrastructure dependency graph.
    Patent: "A graph constructor 103 builds a dependency graph G=(V, E)
    where vertices V represent compute nodes and edges E represent dependencies."
    """

    def build(
        self,
        telemetry: List[Dict[str, Any]],
        edges: Optional[List[Dict]] = None
    ) -> nx.DiGraph:
        """
        Build a directed dependency graph from telemetry and edge data.
        """
        graph = nx.DiGraph()

        # Add nodes with telemetry attributes
        for node in telemetry:
            node_id = node["node_id"]
            graph.add_node(
                node_id,
                metrics=node.get("metrics", {}),
                metadata=node.get("metadata", {}),
                utilization_history=node.get("utilization_history", []),
            )

        # Add edges (dependencies)
        if edges:
            for edge in edges:
                source = edge.get("source") or edge.get("from")
                target = edge.get("target") or edge.get("to")
                weight = edge.get("weight", 1.0)
                dep_type = edge.get("type", "runtime")

                if source in graph.nodes() and target in graph.nodes():
                    graph.add_edge(
                        source, target,
                        weight=weight,
                        dependency_type=dep_type
                    )

        # If no edges provided, infer basic dependencies from network metrics
        if not edges and len(graph.nodes()) > 1:
            self._infer_dependencies(graph, telemetry)

        return graph

    def _infer_dependencies(
        self, graph: nx.DiGraph, telemetry: List[Dict]
    ):
        """
        Infer dependencies when explicit edge data is not available.
        Uses co-located services and network throughput correlation.
        """
        nodes = list(graph.nodes())
        for i, source in enumerate(nodes):
            for target in nodes[i + 1:]:
                source_data = graph.nodes[source]
                target_data = graph.nodes[target]

                # Same region/provider suggests potential dependency
                s_meta = source_data.get("metadata", {})
                t_meta = target_data.get("metadata", {})

                if (s_meta.get("region") == t_meta.get("region") and
                        s_meta.get("region") != "unknown"):
                    graph.add_edge(source, target, weight=0.5,
                                   dependency_type="inferred")

    def get_metrics(self, graph: nx.DiGraph) -> Dict[str, Any]:
        """
        Compute graph-level metrics for the dependency topology.
        """
        num_nodes = graph.number_of_nodes()
        num_edges = graph.number_of_edges()

        metrics = {
            "total_nodes": num_nodes,
            "total_edges": num_edges,
            "density": round(nx.density(graph), 4) if num_nodes > 0 else 0,
        }

        if num_nodes > 0:
            # Connected components (treat as undirected for this metric)
            undirected = graph.to_undirected()
            metrics["connected_components"] = nx.number_connected_components(undirected)

            # Average degree
            degrees = [d for _, d in graph.degree()]
            metrics["avg_degree"] = round(sum(degrees) / len(degrees), 2)

            # Identify critical nodes (high in-degree)
            in_degrees = dict(graph.in_degree())
            critical = [n for n, d in in_degrees.items()
                        if d > max(1, sum(in_degrees.values()) / len(in_degrees))]
            metrics["critical_nodes"] = critical

            # DAG check (important for dependency validation)
            metrics["is_dag"] = nx.is_directed_acyclic_graph(graph)

            # Longest path (critical path) if DAG
            if metrics["is_dag"] and num_edges > 0:
                try:
                    metrics["longest_path_length"] = nx.dag_longest_path_length(graph)
                except Exception:
                    metrics["longest_path_length"] = 0
            else:
                metrics["longest_path_length"] = 0

        return metrics

    def get_k_hop_neighborhood(
        self, graph: nx.DiGraph, node_id: str, k: int = 2
    ) -> nx.DiGraph:
        """
        Extract k-hop neighborhood subgraph around a node.
        Used by Pre-Modification Validator (Module 110) for impact simulation.
        """
        if node_id not in graph.nodes():
            return nx.DiGraph()

        # Get all nodes within k hops
        neighbors = set()
        current_level = {node_id}

        for _ in range(k):
            next_level = set()
            for n in current_level:
                next_level.update(graph.successors(n))
                next_level.update(graph.predecessors(n))
            neighbors.update(current_level)
            current_level = next_level - neighbors

        neighbors.update(current_level)

        return graph.subgraph(neighbors).copy()
