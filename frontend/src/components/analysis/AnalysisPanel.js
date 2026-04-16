import React, { useState } from 'react';

/**
 * Analysis Panel - Configure and run the full CEI analysis pipeline.
 * Maps to the /analyze endpoint which executes Patent Modules 101-111.
 *
 * Light theme to match the /demo and main page treatment.
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

      if (!response.ok)
        throw new Error(`Analysis failed: ${response.statusText}`);
      const data = await response.json();
      onResults(data);
    } catch (err) {
      setError(err.message);
      onResults(generateMockResults(sampleNodes));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={s.wrap}>
      <h2 style={s.heading}>Run CEI Analysis</h2>

      <div style={s.grid}>
        <div style={s.card}>
          <h3 style={s.cardTitle}>Infrastructure</h3>
          <SelectField
            label="Cloud Provider"
            value={provider}
            onChange={setProvider}
            options={[
              ['aws', 'AWS'],
              ['azure', 'Azure'],
              ['gcp', 'Google Cloud'],
              ['kubernetes', 'Kubernetes'],
            ]}
          />
          <SelectField
            label="Region"
            value={region}
            onChange={setRegion}
            options={[
              ['us-east-1', 'US East'],
              ['us-west-2', 'US West'],
              ['us-gov-west-1', 'GovCloud'],
              ['eu-west-1', 'EU West'],
            ]}
          />
          <SelectField
            label="Compliance Framework"
            value={framework}
            onChange={setFramework}
            options={[
              ['fedramp', 'FedRAMP'],
              ['cmmc', 'CMMC'],
              ['hipaa', 'HIPAA'],
              ['standard', 'Standard'],
            ]}
          />
        </div>

        <div style={s.card}>
          <h3 style={s.cardTitle}>Analysis Parameters</h3>
          <RangeField
            label="Analysis Window"
            value={windowDays}
            onChange={setWindowDays}
            min={30}
            max={180}
            unit="days"
          />
          <RangeField
            label="Oscillation Threshold"
            value={oscillationThreshold}
            onChange={setOscillationThreshold}
            min={0.1}
            max={0.9}
            step={0.05}
          />
          <RangeField
            label="Safety Threshold"
            value={safetyThreshold}
            onChange={setSafetyThreshold}
            min={0.3}
            max={0.95}
            step={0.05}
          />
          <RangeField
            label="k-Hop Radius"
            value={kHop}
            onChange={setKHop}
            min={1}
            max={5}
          />
        </div>
      </div>

      <button
        onClick={runAnalysis}
        disabled={loading}
        style={{
          ...s.runButton,
          opacity: loading ? 0.6 : 1,
          cursor: loading ? 'wait' : 'pointer',
        }}
      >
        {loading ? 'Running Analysis Pipeline…' : 'Run Full CEI Analysis →'}
      </button>

      {error && (
        <div style={s.errorBox}>
          Core engine not reachable ({error}). Showing demo results.
        </div>
      )}
    </div>
  );
}

function SelectField({ label, value, onChange, options }) {
  return (
    <div style={s.field}>
      <label style={s.fieldLabel}>{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={s.select}
      >
        {options.map(([val, lbl]) => (
          <option key={val} value={val}>
            {lbl}
          </option>
        ))}
      </select>
    </div>
  );
}

function RangeField({ label, value, onChange, min, max, step = 1, unit = '' }) {
  return (
    <div style={s.field}>
      <div style={s.rangeHeader}>
        <span style={s.fieldLabel}>{label}</span>
        <span style={s.rangeValue}>
          {value}
          {unit ? ` ${unit}` : ''}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        style={s.range}
      />
    </div>
  );
}

const s = {
  wrap: { display: 'flex', flexDirection: 'column', gap: 20 },
  heading: {
    margin: 0,
    fontSize: 18,
    fontWeight: 600,
    color: '#1B4F72',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
    gap: 16,
  },
  card: {
    background: 'white',
    borderRadius: 8,
    border: '1px solid #E5E8EB',
    padding: 18,
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
  },
  cardTitle: {
    margin: 0,
    fontSize: 13,
    fontWeight: 600,
    color: '#566573',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  field: { display: 'flex', flexDirection: 'column', gap: 6 },
  fieldLabel: { fontSize: 12, color: '#7B8A8B' },
  select: {
    width: '100%',
    background: '#FAFBFC',
    border: '1px solid #CFD8DC',
    borderRadius: 4,
    padding: '8px 10px',
    fontSize: 13,
    color: '#1C2833',
  },
  rangeHeader: { display: 'flex', justifyContent: 'space-between' },
  rangeValue: {
    fontFamily: 'monospace',
    fontSize: 12,
    color: '#1B4F72',
    fontWeight: 600,
  },
  range: { width: '100%', accentColor: '#2874A6' },
  runButton: {
    background: '#2874A6',
    color: 'white',
    padding: '14px 24px',
    border: 'none',
    borderRadius: 6,
    fontSize: 14,
    fontWeight: 600,
  },
  errorBox: {
    background: '#FEF9E7',
    border: '1px solid #B7950B',
    color: '#7D6608',
    padding: '10px 14px',
    borderRadius: 6,
    fontSize: 13,
  },
};

/**
 * Mid-market enterprise reference topology (8-node web + data tier).
 * Sized to represent a realistic federal / defense-contractor workload so
 * projected savings read as materially meaningful rather than hobby-scale.
 * Baseline monthly cost ~ $31K/mo across the 8 nodes; 15–27% rightsize/
 * consolidate savings per patent §V.B yield $3–8K/mo depending on which
 * classifications the CEI calculator assigns on a given run.
 */
const SAMPLE_NODES = [
  // node_id              instance (AWS)   replicas  monthly_cost  criticality        environment
  ['api-gateway',         'm5.4xlarge',          3,         4800, 'mission_critical', 'production'],
  ['auth-service',        'm5.xlarge',           2,         1200, 'business_critical','production'],
  ['data-pipeline',       'c5.9xlarge',          2,         6500, 'operational',      'production'],
  ['ml-inference',        'g4dn.8xlarge',        2,         8200, 'operational',      'production'],
  ['cache-layer',         'r5.xlarge',           3,          900, 'business_critical','production'],
  ['db-primary',          'r5.8xlarge',          2,         7400, 'mission_critical', 'production'],
  ['worker-pool',         'c5.4xlarge',          4,         2100, 'development',      'production'],
  ['monitoring',          'm5.large',            2,          400, 'operational',      'development'],
];

// Instance family mapping so the sample reads realistically regardless of
// which provider the user selected in the UI. Monthly cost is identical
// across providers — tuning the family label only.
const INSTANCE_ALIASES = {
  aws:        ['m5.4xlarge','m5.xlarge','c5.9xlarge','g4dn.8xlarge','r5.xlarge','r5.8xlarge','c5.4xlarge','m5.large'],
  azure:      ['Standard_D16s_v3','Standard_D4s_v3','Standard_F36s_v2','Standard_NC8as_T4_v3','Standard_E4s_v3','Standard_E32s_v3','Standard_F16s_v2','Standard_D2s_v3'],
  gcp:        ['n2-standard-16','n2-standard-4','c2-standard-30','a2-highgpu-1g','n2-highmem-4','n2-highmem-32','c2-standard-16','n2-standard-2'],
  kubernetes: ['pool-lg','pool-md','pool-xl','pool-gpu','pool-mem-md','pool-mem-lg','pool-cpu-lg','pool-sm'],
};

function generateSampleNodes(provider, region) {
  const aliases = INSTANCE_ALIASES[provider] || INSTANCE_ALIASES.aws;
  return SAMPLE_NODES.map(
    ([label, _defaultInstance, replicas, monthlyCost, criticality, environment], i) => ({
      node_id: label,
      metrics: {
        // Keep telemetry ranges realistic for a mid-market production
        // workload: moderate CPU, memory with headroom, modest variance.
        cpu_utilization: Math.round((Math.random() * 55 + 15) * 10) / 10,
        memory_utilization: Math.round((Math.random() * 50 + 25) * 10) / 10,
        storage_io: Math.round(Math.random() * 60 * 10) / 10,
        network_throughput: Math.round(Math.random() * 600 + 100),
        request_rate: Math.round(Math.random() * 3500),
        error_rate: Math.round(Math.random() * 1.5 * 100) / 100,
        latency_p99: Math.round(Math.random() * 140 + 30),
      },
      provider,
      region,
      instance_type: aliases[i],
      replicas,
      monthly_cost: monthlyCost,
      tags: { criticality, environment },
    }),
  );
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
    return {
      node_id: n.node_id,
      cei_score: Math.round(cei * 1000) / 1000,
      centrality: Math.round(Math.random() * 1000) / 1000,
      entropy: Math.round(Math.random() * 1000) / 1000,
      risk_factor: Math.round(Math.random() * 1000) / 1000,
      classification: cls,
      recommendation: rec,
    };
  });
  return {
    nodes: mockNodes,
    weights: { alpha: 0.4, beta: 0.35, gamma: 0.25 },
    oscillation_status: { suppression_active: false, oscillating_nodes: [], oscillation_ratio: 0 },
    total_potential_savings: Math.round(Math.random() * 4000 + 2000),
    graph_metrics: { total_nodes: nodes.length, total_edges: 8, density: 0.143, avg_degree: 2.0, is_dag: true },
  };
}
