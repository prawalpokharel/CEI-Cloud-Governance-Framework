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
    # Threshold below which a node is considered a rightsizing candidate.
    # Any node with CEI < this AND a "no_action" / "monitor" recommendation
    # is presumed over-provisioned relative to its workload variability.
    RIGHTSIZING_CEI_THRESHOLD = 0.70

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
            # Monthly cost may be nested under metadata (from data_collector)
            # or surfaced at the top level (from the cloud-provider discovery
            # path). Accept both so savings compute correctly regardless of
            # upstream shape.
            monthly_cost = (
                data.get("metadata", {}).get("monthly_cost")
                or data.get("monthly_cost")
                or 0.0
            )
            cei_score = data.get("cei_score", 0.0)
            risk_factor = data.get("risk_factor", 0.0)

            estimated_savings = 0.0
            action_details = ""

            if action == "consolidate":
                estimated_savings = monthly_cost * self.CONSOLIDATION_SAVINGS_PERCENT
                action_details = (
                    f"Consolidate or decommission. CEI score {cei_score:.3f} "
                    f"indicates low criticality and stable workload. "
                    f"Estimated monthly savings: ${estimated_savings:.2f}"
                )
            elif action == "scale_up":
                action_details = (
                    f"Scale up resources. CEI score {cei_score:.3f} "
                    f"indicates high criticality with complex demand patterns. "
                    f"Risk of underprovisioning if current capacity maintained."
                )
            elif action == "monitor":
                # Monitor + moderate CEI → rightsizing opportunity.
                # Patent §V.B: rightsize oversized instances with stable
                # workloads for ~15% savings. Governance-safe because the
                # node stays in place, only instance class is tuned down.
                if (
                    cei_score < self.RIGHTSIZING_CEI_THRESHOLD
                    and risk_factor < 0.7
                    and monthly_cost > 0
                ):
                    estimated_savings = (
                        monthly_cost * self.RIGHTSIZING_SAVINGS_PERCENT
                    )
                    action_details = (
                        f"Rightsize resources. CEI score {cei_score:.3f} is "
                        f"elevated but stable — workload variability does not "
                        f"justify current instance class. "
                        f"Estimated monthly savings: ${estimated_savings:.2f}"
                    )
                else:
                    action_details = (
                        f"Continue monitoring. CEI score {cei_score:.3f} "
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
                elif (
                    cei_score < self.RIGHTSIZING_CEI_THRESHOLD
                    and risk_factor < 0.7
                    and monthly_cost > 0
                ):
                    # Unblocked no_action + moderate CEI → rightsizing candidate.
                    estimated_savings = (
                        monthly_cost * self.RIGHTSIZING_SAVINGS_PERCENT
                    )
                    action_details = (
                        f"Rightsize candidate. CEI score {cei_score:.3f} with "
                        f"stable workload suggests the instance class can be "
                        f"reduced one tier. "
                        f"Estimated monthly savings: ${estimated_savings:.2f}"
                    )
                else:
                    action_details = (
                        "No optimization action required at this time."
                    )

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
