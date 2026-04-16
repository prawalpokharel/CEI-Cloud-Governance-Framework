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

    # Cost estimation factors (Patent Section V.B)
    CONSOLIDATION_SAVINGS_PERCENT = 0.27  # 27% per paper results
    RIGHTSIZING_SAVINGS_PERCENT = 0.15
    # Any node with CEI below this AND a "no_action" / "monitor" recommendation
    # is presumed over-provisioned relative to its workload variability, and
    # therefore a rightsizing candidate (Patent Section V.B).
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
            # Monthly cost may be nested under metadata (core engine shape)
            # or surfaced at the top level (cloud-provider discovery shape).
            # Accept both so savings compute correctly regardless of source.
            monthly_cost = (
                data.get("metadata", {}).get("monthly_cost")
                or data.get("monthly_cost")
                or 0.0
            )
            cei_score = data.get("cei_score", 0.0)
            risk_factor = data.get("risk_factor", 0.0)

            estimated_savings = 0.0
            action_details = ""
            action_type = action  # normalized action verb for the UI

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
            elif action in ("monitor", "no_action"):
                # Rightsizing is a softer intervention than consolidation
                # or scale_up: it reduces instance class *in place* without
                # changing replica count, region, or connectivity. Because
                # it preserves the topology, the k-hop impact check
                # (Module 110) and cascade risk check don't apply — they
                # only apply to interventions that change the graph shape.
                # So rightsizing eligibility is evaluated independently of
                # the blocked_reason flag, which is set by those same
                # graph-shape checks. Governance-specific blocks (e.g.
                # pinned instance class) still gate rightsizing; those
                # are not currently modeled as a distinct violation type.
                rightsize_eligible = (
                    cei_score < self.RIGHTSIZING_CEI_THRESHOLD
                    and risk_factor < 0.7
                    and monthly_cost > 0
                )
                blocked = data.get("blocked_reason")

                if rightsize_eligible:
                    estimated_savings = (
                        monthly_cost * self.RIGHTSIZING_SAVINGS_PERCENT
                    )
                    action_type = "rightsize"
                    if action == "monitor":
                        action_details = (
                            f"Rightsize resources. CEI score {cei_score:.3f} "
                            f"is elevated but stable — workload variability "
                            f"does not justify current instance class. "
                            f"Estimated monthly savings: ${estimated_savings:.2f}"
                        )
                    else:
                        action_details = (
                            f"Rightsize candidate. CEI score {cei_score:.3f} "
                            f"with stable workload suggests the instance "
                            f"class can be reduced one tier. "
                            f"Estimated monthly savings: ${estimated_savings:.2f}"
                        )
                elif blocked:
                    action_details = (
                        f"No modification permitted. {blocked}. "
                        f"Manual review recommended."
                    )
                elif action == "monitor":
                    action_details = (
                        f"Continue monitoring. CEI score {cei_score:.3f} "
                        f"is elevated but within acceptable range. "
                        f"Re-evaluate in next analysis cycle."
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
                "action_type": action_type,
                "action_details": action_details,
                "estimated_savings": round(estimated_savings, 2),
                "monthly_cost": round(monthly_cost, 2),
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
