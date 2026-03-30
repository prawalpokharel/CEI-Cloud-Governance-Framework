"""
Patent Module 104: Governance Policy Store
Holds organizational rules including mission-criticality classifications,
disaster recovery dependencies, compliance constraints (FedRAMP, CMMC),
and minimum replica requirements.
"""
from typing import Dict, List, Any, Optional


class GovernancePolicyStore:
    """
    Manages governance policies that constrain optimization decisions.
    Patent: "A governance policy store 104 holds organizational rules"
    Paper Section III: "maintains organizational rules including
    mission-criticality classifications, disaster recovery dependencies,
    compliance constraints, and minimum replica requirements."
    """

    # Predefined compliance frameworks
    COMPLIANCE_FRAMEWORKS = {
        "fedramp": {
            "name": "FedRAMP",
            "min_replicas": 2,
            "data_sovereignty": True,
            "encryption_required": True,
            "audit_logging": True,
            "risk_weight": 0.9,
        },
        "cmmc": {
            "name": "CMMC",
            "min_replicas": 2,
            "data_sovereignty": True,
            "encryption_required": True,
            "cui_handling": True,
            "risk_weight": 0.85,
        },
        "hipaa": {
            "name": "HIPAA",
            "min_replicas": 2,
            "data_sovereignty": True,
            "encryption_required": True,
            "phi_handling": True,
            "risk_weight": 0.8,
        },
        "standard": {
            "name": "Standard",
            "min_replicas": 1,
            "data_sovereignty": False,
            "encryption_required": False,
            "risk_weight": 0.2,
        },
    }

    # Mission criticality levels
    CRITICALITY_LEVELS = {
        "mission_critical": 1.0,
        "business_critical": 0.75,
        "operational": 0.5,
        "development": 0.25,
        "test": 0.1,
    }

    def __init__(self):
        self.policies = {}
        self.node_policies = {}

    def load_policies(self, policy_config: Dict):
        """Load governance policies from configuration."""
        self.policies = {
            "compliance_framework": policy_config.get("compliance_framework", "standard"),
            "mission_criticality_default": policy_config.get("mission_criticality", "operational"),
            "min_replicas_global": policy_config.get("min_replicas", 1),
            "data_sovereignty_regions": policy_config.get("allowed_regions", []),
            "disaster_recovery": policy_config.get("disaster_recovery", {}),
            "node_overrides": policy_config.get("node_overrides", {}),
        }

    def compute_risk_factors(self, telemetry: List[Dict]) -> Dict[str, float]:
        """
        Compute R_i (governance risk factor) for each node.
        Combines mission-criticality, compliance requirements, and
        disaster recovery dependencies into a single [0, 1] risk score.
        
        Patent: "R_i is a risk factor derived from governance policies"
        """
        risk_factors = {}
        framework = self.policies.get("compliance_framework", "standard")
        framework_config = self.COMPLIANCE_FRAMEWORKS.get(
            framework, self.COMPLIANCE_FRAMEWORKS["standard"]
        )

        for node in telemetry:
            node_id = node["node_id"]
            tags = node.get("metadata", {}).get("tags", {})

            # Determine mission criticality
            criticality = tags.get(
                "criticality",
                self.policies.get("mission_criticality_default", "operational")
            )
            criticality_score = self.CRITICALITY_LEVELS.get(criticality, 0.5)

            # Check for node-specific policy overrides
            overrides = self.policies.get("node_overrides", {}).get(node_id, {})
            if "criticality" in overrides:
                criticality_score = self.CRITICALITY_LEVELS.get(
                    overrides["criticality"], criticality_score
                )

            # Compliance risk weight
            compliance_risk = framework_config["risk_weight"]

            # Disaster recovery dependency check
            dr_config = self.policies.get("disaster_recovery", {})
            is_dr_target = node_id in dr_config.get("protected_nodes", [])
            dr_factor = 0.3 if is_dr_target else 0.0

            # Composite risk factor R_i
            r_i = (0.5 * criticality_score +
                   0.3 * compliance_risk +
                   0.2 * dr_factor)

            risk_factors[node_id] = round(min(1.0, r_i), 4)
            self.node_policies[node_id] = {
                "criticality": criticality,
                "compliance_framework": framework,
                "is_dr_target": is_dr_target,
                "min_replicas": overrides.get(
                    "min_replicas", framework_config["min_replicas"]
                ),
            }

        return risk_factors

    def check_compliance(self, telemetry: List[Dict]) -> Dict[str, Dict]:
        """Check compliance status for each node."""
        results = {}
        framework = self.policies.get("compliance_framework", "standard")
        framework_config = self.COMPLIANCE_FRAMEWORKS.get(
            framework, self.COMPLIANCE_FRAMEWORKS["standard"]
        )

        allowed_regions = self.policies.get("data_sovereignty_regions", [])

        for node in telemetry:
            node_id = node["node_id"]
            region = node.get("metadata", {}).get("region", "unknown")
            violations = []

            # Data sovereignty check
            if (framework_config.get("data_sovereignty") and
                    allowed_regions and region not in allowed_regions):
                violations.append(f"Data sovereignty: {region} not in allowed regions")

            results[node_id] = {
                "compliant": len(violations) == 0,
                "violations": violations,
                "framework": framework,
            }

        return results

    def get_policies(self) -> Dict:
        """Return current policy configuration."""
        return self.policies

    def get_node_policy(self, node_id: str) -> Dict:
        """Return policy configuration for a specific node."""
        return self.node_policies.get(node_id, {})
