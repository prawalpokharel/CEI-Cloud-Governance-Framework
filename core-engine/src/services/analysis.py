"""
The CEI analysis pipeline (Patent Modules 101-111).

Lives in a service rather than a route handler because three callers need it:
the /analyze endpoint, the scenario analysis endpoint, and the scenario
benchmark endpoint. Previously the latter two invoked the route function
directly, which coupled them to FastAPI's request handling for no reason.
"""

from ..engine import build_pipeline
from ..schemas import AnalysisRequest, AnalysisResponse, NodeCEIResult


def run_analysis(
    request: AnalysisRequest, pipeline=None
) -> AnalysisResponse:
    """
    Execute the complete CEI analysis pipeline:

    1. Collect and validate telemetry (Module 101)
    2. Construct dependency graph (Module 103)
    3. Apply governance policies (Module 104)
    4. Compute stability scores (Module 105)
    5. Calculate CEI with adaptive weights (Modules 106, 107)
    6. Detect oscillations (Module 108)
    7. Model fault propagation (Module 109)
    8. Validate modifications via k-hop simulation (Module 110)
    9. Generate recommendations (Module 111)

    Pure function of its input: the pipeline modules are constructed fresh
    for each call, so repeated invocations return identical results and
    concurrent callers cannot perturb one another.

    ``pipeline`` accepts a pre-configured module set, which the live-cluster
    path uses to select centrality semantics and to suppress the entropy term
    when there is too little history to measure it. It must still be a fresh
    instance per call -- passing a shared one reintroduces exactly the
    cross-request coupling this design removed.
    """
    p = pipeline if pipeline is not None else build_pipeline()

    # Step 1: Data Collection (Patent Module 101)
    telemetry_data = p.data_collector.collect(request.telemetry.nodes)

    # Step 2: Graph Construction (Patent Module 103)
    graph = p.graph_constructor.build(telemetry_data, request.telemetry.edges)

    # Step 3: Governance Policy Application (Patent Module 104)
    p.governance_store.load_policies(request.telemetry.governance_policies)
    risk_factors = p.governance_store.compute_risk_factors(telemetry_data)

    # Step 4: Stability Monitoring (Patent Module 105)
    stability_scores = p.stability_monitor.compute(
        telemetry_data, window_days=request.analysis_window_days
    )

    # Step 5: Adaptive Weight Recalibration (Patent Module 107)
    weights = p.weight_recalibrator.recalibrate(
        stability_scores=stability_scores,
        oscillation_detected=False,
        topology_changed=False,
    )

    # Step 6: CEI Calculation (Patent Module 106)
    cei_results = p.cei_calculator.compute(
        graph=graph,
        telemetry=telemetry_data,
        risk_factors=risk_factors,
        weights=weights,
    )

    # Step 7: Oscillation Detection (Patent Module 108)
    oscillation_status = p.oscillation_detector.detect(
        telemetry_data, threshold=request.oscillation_threshold
    )

    # Update weights if oscillation detected
    if oscillation_status["suppression_active"]:
        weights = p.weight_recalibrator.recalibrate(
            stability_scores=stability_scores,
            oscillation_detected=True,
            topology_changed=False,
        )
        cei_results = p.cei_calculator.compute(
            graph=graph,
            telemetry=telemetry_data,
            risk_factors=risk_factors,
            weights=weights,
        )

    # Step 8: Fault Propagation Modeling (Patent Module 109)
    fault_risks = p.fault_simulator.simulate(graph, cei_results, risk_factors)

    # Step 9: Pre-Modification Validation (Patent Module 110)
    validated_results = p.pre_mod_validator.validate(
        graph=graph,
        cei_results=cei_results,
        fault_risks=fault_risks,
        governance_policies=p.governance_store.get_policies(),
        safety_threshold=request.safety_threshold,
        k_hop=request.k_hop,
    )

    # Step 10: Generate Recommendations (Patent Module 111)
    recommendations = p.actuator.generate_recommendations(validated_results)

    graph_metrics = p.graph_constructor.get_metrics(graph)

    node_results = []
    total_savings = 0.0
    for node_id, data in recommendations.items():
        node_results.append(
            NodeCEIResult(
                node_id=node_id,
                cei_score=data["cei_score"],
                centrality=data["centrality"],
                entropy=data["entropy"],
                risk_factor=data["risk_factor"],
                classification=data["classification"],
                recommendation=data["recommendation"],
                action_type=data.get("action_type"),
                action_details=data.get("action_details", ""),
                estimated_savings=data.get("estimated_savings", 0.0),
                monthly_cost=data.get("monthly_cost", 0.0),
                is_safe=data.get("is_safe", False),
                blocked_reason=data.get("blocked_reason"),
                validation=data.get("validation", {}),
            )
        )
        total_savings += data.get("estimated_savings", 0.0)

    return AnalysisResponse(
        nodes=node_results,
        weights=weights,
        oscillation_status=oscillation_status,
        total_potential_savings=total_savings,
        graph_metrics=graph_metrics,
    )
