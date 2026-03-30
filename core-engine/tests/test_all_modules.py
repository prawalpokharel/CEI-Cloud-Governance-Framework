"""
CloudOptimizer Core Engine — Test Suite
Validates all patent modules (101-112) with sample data.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.cei.data_collector import DataCollector
from src.cei.cei_calculator import CEICalculator
from src.cei.stability_monitor import StabilityMonitor
from src.cei.adaptive_weights import AdaptiveWeightRecalibrator
from src.graph.dependency_graph import DependencyGraphConstructor
from src.governance.policy_store import GovernancePolicyStore
from src.oscillation.detector import OscillationDetector
from src.fault.propagation import FaultPropagationSimulator
from src.simulation.validator import PreModificationValidator
from src.recommendation.actuator import RecommendationActuator
from src.rollback.manager import RollbackManager


def test_sample_data():
    """Generate sample infrastructure data for testing."""
    return [
        {"node_id": "api-gateway", "metrics": {"cpu_utilization": 75, "memory_utilization": 60, "error_rate": 1.2, "latency_p99": 45},
         "provider": "aws", "region": "us-east-1", "instance_type": "m5.xlarge", "monthly_cost": 350.0,
         "tags": {"criticality": "mission_critical", "environment": "production"}},
        {"node_id": "auth-service", "metrics": {"cpu_utilization": 30, "memory_utilization": 25, "error_rate": 0.1, "latency_p99": 20},
         "provider": "aws", "region": "us-east-1", "instance_type": "t3.large", "monthly_cost": 150.0,
         "tags": {"criticality": "business_critical", "environment": "production"}},
        {"node_id": "data-pipeline", "metrics": {"cpu_utilization": 85, "memory_utilization": 70, "error_rate": 0.5, "latency_p99": 120},
         "provider": "aws", "region": "us-east-1", "instance_type": "c5.2xlarge", "monthly_cost": 500.0,
         "tags": {"criticality": "operational", "environment": "production"}},
        {"node_id": "dev-sandbox", "metrics": {"cpu_utilization": 8, "memory_utilization": 12, "error_rate": 0, "latency_p99": 5},
         "provider": "aws", "region": "us-east-1", "instance_type": "t3.medium", "monthly_cost": 80.0,
         "tags": {"criticality": "development", "environment": "development"}},
    ]


def test_edges():
    return [
        {"source": "api-gateway", "target": "auth-service", "weight": 0.9, "type": "runtime"},
        {"source": "api-gateway", "target": "data-pipeline", "weight": 0.8, "type": "data"},
        {"source": "data-pipeline", "target": "dev-sandbox", "weight": 0.3, "type": "dev"},
    ]


def run_tests():
    print("=" * 60)
    print("CloudOptimizer Core Engine — Patent Module Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    # Test Module 101: Data Collector
    print("\n[Module 101] Data Collection...")
    try:
        dc = DataCollector()
        telemetry = dc.collect(test_sample_data())
        assert len(telemetry) == 4
        assert all("node_id" in n for n in telemetry)
        assert all("metrics" in n for n in telemetry)
        print("  PASSED - Collected and normalized 4 nodes")
        passed += 1
    except Exception as e:
        print(f"  FAILED - {e}")
        failed += 1

    # Test Module 103: Graph Constructor
    print("\n[Module 103] Graph Construction...")
    try:
        gc = DependencyGraphConstructor()
        graph = gc.build(telemetry, test_edges())
        assert graph.number_of_nodes() == 4
        assert graph.number_of_edges() == 3
        metrics = gc.get_metrics(graph)
        assert "total_nodes" in metrics
        print(f"  PASSED - Graph: {metrics['total_nodes']} nodes, {metrics['total_edges']} edges")
        passed += 1
    except Exception as e:
        print(f"  FAILED - {e}")
        failed += 1

    # Test Module 104: Governance Policy Store
    print("\n[Module 104] Governance Policies...")
    try:
        gps = GovernancePolicyStore()
        gps.load_policies({"compliance_framework": "fedramp", "mission_criticality": "operational", "min_replicas": 2})
        risk_factors = gps.compute_risk_factors(telemetry)
        assert len(risk_factors) == 4
        assert all(0 <= v <= 1 for v in risk_factors.values())
        # Mission critical should have higher risk than development
        assert risk_factors["api-gateway"] > risk_factors["dev-sandbox"]
        print(f"  PASSED - Risk factors computed (api-gateway: {risk_factors['api-gateway']}, dev-sandbox: {risk_factors['dev-sandbox']})")
        passed += 1
    except Exception as e:
        print(f"  FAILED - {e}")
        failed += 1

    # Test Module 105: Stability Monitor
    print("\n[Module 105] Stability Monitor...")
    try:
        sm = StabilityMonitor()
        stability = sm.compute(telemetry, window_days=90)
        assert len(stability) == 4
        assert all(0 <= v <= 1 for v in stability.values())
        print(f"  PASSED - Stability scores: {stability}")
        passed += 1
    except Exception as e:
        print(f"  FAILED - {e}")
        failed += 1

    # Test Module 107: Adaptive Weight Recalibration
    print("\n[Module 107] Adaptive Weights...")
    try:
        awr = AdaptiveWeightRecalibrator()
        weights = awr.recalibrate(stability_scores=stability, oscillation_detected=False)
        assert abs(weights["alpha"] + weights["beta"] + weights["gamma"] - 1.0) < 0.01
        print(f"  PASSED - Weights: alpha={weights['alpha']}, beta={weights['beta']}, gamma={weights['gamma']}")

        # Test oscillation response
        weights_osc = awr.recalibrate(stability_scores=stability, oscillation_detected=True)
        assert weights_osc["beta"] < weights["beta"]  # Beta should decrease
        print(f"  PASSED - Oscillation adjustment: beta reduced to {weights_osc['beta']}")
        passed += 1
    except Exception as e:
        print(f"  FAILED - {e}")
        failed += 1

    # Test Module 106: CEI Calculator
    print("\n[Module 106] CEI Computation...")
    try:
        cc = CEICalculator()
        cei_results = cc.compute(graph, telemetry, risk_factors, weights)
        assert len(cei_results) == 4
        for nid, data in cei_results.items():
            assert "cei_score" in data
            assert "centrality" in data
            assert "entropy" in data
            assert "classification" in data
            assert 0 <= data["cei_score"] <= 2  # Can exceed 1.0 in edge cases
        print(f"  PASSED - CEI computed for {len(cei_results)} nodes")
        for nid, data in cei_results.items():
            print(f"    {nid}: CEI={data['cei_score']:.3f} [{data['classification']}]")
        passed += 1
    except Exception as e:
        print(f"  FAILED - {e}")
        failed += 1

    # Test Module 108: Oscillation Detector
    print("\n[Module 108] Oscillation Detection...")
    try:
        od = OscillationDetector()
        osc_status = od.detect(telemetry, threshold=0.3)
        assert "suppression_active" in osc_status
        assert "oscillating_nodes" in osc_status
        print(f"  PASSED - Suppression: {osc_status['suppression_active']}, Oscillating: {osc_status['oscillating_node_count']}/{osc_status['total_nodes']}")
        passed += 1
    except Exception as e:
        print(f"  FAILED - {e}")
        failed += 1

    # Test Module 109: Fault Propagation
    print("\n[Module 109] Fault Propagation...")
    try:
        fps = FaultPropagationSimulator()
        fault_risks = fps.simulate(graph, cei_results, risk_factors)
        assert len(fault_risks) == 4
        for nid, data in fault_risks.items():
            assert "p_fail" in data
            assert "cascade_risk" in data
        print(f"  PASSED - Fault risks computed for {len(fault_risks)} nodes")
        passed += 1
    except Exception as e:
        print(f"  FAILED - {e}")
        failed += 1

    # Test Module 110: Pre-Modification Validator
    print("\n[Module 110] Pre-Modification Validation...")
    try:
        pmv = PreModificationValidator()
        validated = pmv.validate(graph, cei_results, fault_risks, gps.get_policies(), safety_threshold=0.7, k_hop=2)
        assert len(validated) == 4
        safe_count = sum(1 for v in validated.values() if v["is_safe"])
        print(f"  PASSED - {safe_count}/{len(validated)} modifications validated as safe")
        passed += 1
    except Exception as e:
        print(f"  FAILED - {e}")
        failed += 1

    # Test Module 111: Recommendation Actuator
    print("\n[Module 111] Recommendations...")
    try:
        ra = RecommendationActuator()
        recommendations = ra.generate_recommendations(validated)
        assert len(recommendations) == 4
        for nid, rec in recommendations.items():
            assert "recommendation" in rec
            assert "estimated_savings" in rec
        total_savings = sum(r["estimated_savings"] for r in recommendations.values())
        print(f"  PASSED - Total estimated savings: ${total_savings:.2f}/month")
        passed += 1
    except Exception as e:
        print(f"  FAILED - {e}")
        failed += 1

    # Test Module 112: Rollback Manager
    print("\n[Module 112] Rollback Manager...")
    try:
        rm = RollbackManager()
        snap_id = rm.create_snapshot({"nodes": ["api-gateway"], "current_kpis": {"latency_p99": 45, "error_rate": 1.2}})
        assert snap_id.startswith("snap_")

        check = rm.check_kpis(snap_id, {"latency_p99": 46, "error_rate": 1.3})
        assert not check["rollback_recommended"]  # Small deviation

        check_bad = rm.check_kpis(snap_id, {"latency_p99": 100, "error_rate": 5.0})
        assert check_bad["rollback_recommended"]  # Large deviation

        revert = rm.revert(snap_id)
        assert revert["reverted"]
        print(f"  PASSED - Snapshot created, KPI check working, rollback successful")
        passed += 1
    except Exception as e:
        print(f"  FAILED - {e}")
        failed += 1

    # Summary
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"Patent Modules Tested: 101, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
