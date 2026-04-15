import React, { useState } from 'react';

/**
 * Analysis Panel - Configure and run the full CEI analysis pipeline.
 * Maps to the /analyze endpoint which executes Patent Modules 101-111.
 */
export default function AnalysisPanel({ onResults }) {
  const [loading, setLoading] = useState(false);
  const [provider, setProvider] = useState('aws');
  const [region, setRegion] = useState('us-east-1');
  const [framework, setFramework] = useState('fedramp');
  const [windowDays, setWindowDays] = useState(90);
  const [oscillationThreshold, setOscillationThreshold] = useState(0.3);
  const [safetyThreshold, setSafetyThreshold] = useState(0.7);
  const [kHop, setKHop] = useState(2);
  const [error, setError] = useState(null);

  const runAnalysis = async () => {
    setLoading(true);
    setError(null);

    // Generate sample telemetry for demo (in production, fetched from cloud APIs)
    const sampleNodes = generateSampleNodes(provider, region);
    const sampleEdges = generateSampleEdges(sampleNodes);

    const request = {
      telemetry: {
        nodes: sampleNodes,
        edges: sampleEdges,
        governance_policies: {
          compliance_framework: framework,
          mission_criticality: 'operational',
          min_replicas: framework === 'standard' ? 1 : 2,
          allowed_regions: [region],
          disaster_recovery: { protected_nodes: [sampleNodes[0]?.node_id] },
        },
      },
      analysis_window_days: windowDays,
      oscillation_threshold: oscillationThreshold,
      safety_threshold: safetyThreshold,
      k_hop: kHop,
    };

    try {
      // Route through the backend so we don't depend on
      // NEXT_PUBLIC_CORE_ENGINE_URL being set on the frontend.
      // The backend already knows the core engine URL via CORE_ENGINE_URL
      // and forwards the same payload to /analyze.
      const apiBase =
        process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001';
      const response = await fetch(`${apiBase}/api/demo/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      });

      if (!response.ok) throw new Error(`Analysis failed: ${response.statusText}`);
      const data = await response.json();
      onResults(data);
    } catch (err) {
      setError(err.message);
      // Fallback: generate mock results for UI demonstration
      const mockResults = generateMockResults(sampleNodes);
      onResults(mockResults);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-bold text-white">Run CEI Analysis</h2>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Infrastructure Config */}
        <div className="bg-gray-900 rounded-lg p-4 border border-gray-800 space-y-4">
          <h3 className="text-sm font-semibold text-gray-400">Infrastructure</h3>
          <SelectField label="Cloud Provider" value={provider} onChange={setProvider}
            options={[['aws', 'AWS'], ['azure', 'Azure'], ['gcp', 'Google Cloud'], ['kubernetes', 'Kubernetes']]} />
          <SelectField label="Region" value={region} onChange={setRegion}
            options={[['us-east-1', 'US East'], ['us-west-2', 'US West'], ['us-gov-west-1', 'GovCloud'], ['eu-west-1', 'EU West']]} />
          <SelectField label="Compliance Framework" value={framework} onChange={setFramework}
            options={[['fedramp', 'FedRAMP'], ['cmmc', 'CMMC'], ['hipaa', 'HIPAA'], ['standard', 'Standard']]} />
        </div>

        {/* Analysis Parameters */}
        <div className="bg-gray-900 rounded-lg p-4 border border-gray-800 space-y-4">
          <h3 className="text-sm font-semibold text-gray-400">Analysis Parameters</h3>
          <RangeField label="Analysis Window" value={windowDays} onChange={setWindowDays} min={30} max={180} unit="days" />
          <RangeField label="Oscillation Threshold" value={oscillationThreshold} onChange={setOscillationThreshold} min={0.1} max={0.9} step={0.05} />
          <RangeField label="Safety Threshold" value={safetyThreshold} onChange={setSafetyThreshold} min={0.3} max={0.95} step={0.05} />
          <RangeField label="k-Hop Radius" value={kHop} onChange={setKHop} min={1} max={5} />
        </div>
      </div>

      <button onClick={runAnalysis} disabled={loading}
        className="w-full py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 rounded-lg font-semibold transition-colors">
        {loading ? 'Running Analysis Pipeline...' : 'Run Full CEI Analysis'}
      </button>

      {error && (
        <div className="bg-yellow-900/30 border border-yellow-700 rounded-lg p-3 text-sm text-yellow-300">
          Core engine not reachable ({error}). Showing demo results.
        </div>
      )}
    </div>
  );
}

function SelectField({ label, value, onChange, options }) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-white">
        {options.map(([val, lbl]) => <option key={val} value={val}>{lbl}</option>)}
      </select>
    </div>
  );
}

function RangeField({ label, value, onChange, min, max, step = 1, unit = '' }) {
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-gray-500">{label}</span>
        <span className="text-white font-mono">{value}{unit ? ` ${unit}` : ''}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full h-1 bg-gray-700 rounded-lg appearance-none cursor-pointer" />
    </div>
  );
}

function generateSampleNodes(provider, region) {
  const types = { aws: ['t3.medium', 't3.large', 'm5.xlarge', 'c5.2xlarge', 'r5.large', 'm5.2xlarge', 'c5.xlarge', 'r5.xlarge'],
    azure: ['Standard_B2ms', 'Standard_D4s_v3', 'Standard_E4s_v3', 'Standard_F4s_v2', 'Standard_D8s_v3', 'Standard_E8s_v3', 'Standard_B4ms', 'Standard_D2s_v3'],
    gcp: ['e2-medium', 'n2-standard-4', 'c2-standard-8', 'e2-standard-2', 'n2-standard-8', 'e2-small', 'n2-standard-2', 'c2-standard-4'],
    kubernetes: ['pod-sm', 'pod-md', 'pod-lg', 'pod-xl', 'pod-sm-2', 'pod-md-2', 'pod-lg-2', 'pod-xl-2'] };
  const labels = ['api-gateway', 'auth-service', 'data-pipeline', 'ml-inference', 'cache-layer', 'db-primary', 'worker-pool', 'monitoring'];
  const criticalities = ['mission_critical', 'business_critical', 'operational', 'operational', 'business_critical', 'mission_critical', 'development', 'operational'];
  return labels.map((label, i) => ({
    node_id: `${label}`,
    metrics: { cpu_utilization: Math.round((Math.random() * 70 + 10) * 10) / 10, memory_utilization: Math.round((Math.random() * 65 + 15) * 10) / 10,
      storage_io: Math.round(Math.random() * 50 * 10) / 10, network_throughput: Math.round(Math.random() * 400 + 50),
      request_rate: Math.round(Math.random() * 2000), error_rate: Math.round(Math.random() * 2 * 100) / 100, latency_p99: Math.round(Math.random() * 150 + 20) },
    provider, region, instance_type: (types[provider] || types.aws)[i], monthly_cost: Math.round((Math.random() * 600 + 100) * 100) / 100,
    tags: { criticality: criticalities[i], environment: i < 6 ? 'production' : 'development' },
  }));
}

function generateSampleEdges(nodes) {
  const edges = [];
  if (nodes.length >= 8) {
    edges.push({ source: nodes[0].node_id, target: nodes[1].node_id, weight: 0.9, type: 'runtime' });
    edges.push({ source: nodes[0].node_id, target: nodes[2].node_id, weight: 0.8, type: 'runtime' });
    edges.push({ source: nodes[2].node_id, target: nodes[3].node_id, weight: 0.95, type: 'data' });
    edges.push({ source: nodes[1].node_id, target: nodes[4].node_id, weight: 0.7, type: 'runtime' });
    edges.push({ source: nodes[2].node_id, target: nodes[5].node_id, weight: 0.9, type: 'data' });
    edges.push({ source: nodes[3].node_id, target: nodes[6].node_id, weight: 0.6, type: 'runtime' });
    edges.push({ source: nodes[5].node_id, target: nodes[4].node_id, weight: 0.5, type: 'runtime' });
    edges.push({ source: nodes[7].node_id, target: nodes[0].node_id, weight: 0.3, type: 'monitoring' });
  }
  return edges;
}

function generateMockResults(nodes) {
  const mockNodes = nodes.map((n) => {
    const cei = Math.random() * 0.8 + 0.1;
    const cls = cei > 0.75 ? 'critical' : cei > 0.5 ? 'elevated' : cei > 0.25 ? 'moderate' : 'low';
    const rec = cls === 'low' ? 'consolidate' : cls === 'critical' ? 'scale_up' : cls === 'elevated' ? 'monitor' : 'no_action';
    return { node_id: n.node_id, cei_score: Math.round(cei * 1000) / 1000, centrality: Math.round(Math.random() * 1000) / 1000,
      entropy: Math.round(Math.random() * 1000) / 1000, risk_factor: Math.round(Math.random() * 1000) / 1000, classification: cls, recommendation: rec };
  });
  return {
    nodes: mockNodes, weights: { alpha: 0.4, beta: 0.35, gamma: 0.25 },
    oscillation_status: { suppression_active: false, oscillating_nodes: [], oscillation_ratio: 0 },
    total_potential_savings: Math.round(Math.random() * 4000 + 2000), graph_metrics: { total_nodes: nodes.length, total_edges: 8, density: 0.143, avg_degree: 2.0, is_dag: true },
  };
}
