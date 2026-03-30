"""
Patent Module 111: Actuator / Recommendation Engine
Executes modifications by calling cloud provider APIs.
Generates actionable recommendations with cost projections.
"""
from typing import Dict, List, Any


class RecommendationActuator:
    """
    Generates optimization recommendations based on validated CEI results.
    In production, this module would invoke cloud provider APIs for execution.
    Patent: "An actuator 111 executes modifications by calling cloud provider APIs"
    """

    # Cost estimation factors
    CONSOLIDATION_SAVINGS_PERCENT = 0.27  # 27% per paper results
    RIGHTSIZING_SAVINGS_PERCENT = 0.15

    def generate_recommendations(
        self, validated_results: Dict[str, Dict]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Generate actionable recommendations from validated analysis results.
        Each recommendation includes estimated savings and risk assessment.
        """
        recommendations = {}

        for node_id, data in validated_results.items():
            action = data.get("recommendation", "no_action")
            monthly_cost = data.get("metadata", {}).get("monthly_cost", 0.0)

            estimated_savings = 0.0
            action_details = ""

            if action == "consolidate":
                estimated_savings = monthly_cost * self.CONSOLIDATION_SAVINGS_PERCENT
                action_details = (
                    f"Consolidate or decommission. CEI score {data['cei_score']:.3f} "
                    f"indicates low criticality and stable workload. "
                    f"Estimated monthly savings: ${estimated_savings:.2f}"
                )
            elif action == "scale_up":
                action_details = (
                    f"Scale up resources. CEI score {data['cei_score']:.3f} "
                    f"indicates high criticality with complex demand patterns. "
                    f"Risk of underprovisioning if current capacity maintained."
                )
            elif action == "monitor":
                action_details = (
                    f"Continue monitoring. CEI score {data['cei_score']:.3f} "
                    f"is elevated but within acceptable range. "
                    f"Re-evaluate in next analysis cycle."
                )
            elif action == "no_action":
                blocked = data.get("blocked_reason")
                if blocked:
                    action_details = (
                        f"No modification permitted. {blocked}. "
                        f"Manual review recommended."
                    )
                else:
                    action_details = "No optimization action required at this time."

            recommendations[node_id] = {
                "node_id": node_id,
                "cei_score": data.get("cei_score", 0.0),
                "centrality": data.get("centrality", 0.0),
                "entropy": data.get("entropy", 0.0),
                "risk_factor": data.get("risk_factor", 0.0),
                "classification": data.get("classification", "unknown"),
                "recommendation": action,
                "action_details": action_details,
                "estimated_savings": round(estimated_savings, 2),
                "is_safe": data.get("is_safe", False),
                "blocked_reason": data.get("blocked_reason"),
                "validation": data.get("validation", {}),
            }

        return recommendations

    def execute(self, node_id: str, action: str, provider: str) -> Dict:
        """
        Execute a recommendation via cloud provider API.
        In production, this would call AWS/Azure/GCP APIs.
        Currently returns a simulation result.
        """
        return {
            "node_id": node_id,
            "action": action,
            "provider": provider,
            "status": "simulated",
            "message": f"Action '{action}' would be executed via {provider} API",
        }
