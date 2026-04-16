import React from 'react';

/**
 * CEI Dashboard - Displays Centrality-Entropy Index analysis results
 * with real-time visualization of node classifications, weights, and
 * optimization recommendations.
 *
 * Light theme to match the /demo and main page treatment.
 */

const CLASS_COLORS = {
  critical: { bg: '#FDEDEC', text: '#922B21', dot: '#E74C3C' },
  elevated: { bg: '#FEF9E7', text: '#7D6608', dot: '#F39C12' },
  moderate: { bg: '#EBF5FB', text: '#1B4F72', dot: '#3498DB' },
  low: { bg: '#EAFAF1', text: '#196F3D', dot: '#27AE60' },
};

const SUMMARY_COLOR = {
  blue: '#1B4F72',
  green: '#196F3D',
  red: '#922B21',
  yellow: '#7D6608',
};

function formatMonthly(n) {
  const v = Number(n) || 0;
  if (v >= 1000) return `$${(v / 1000).toFixed(1)}k/mo`;
  return `$${v.toFixed(0)}/mo`;
}

function formatAnnual(monthly) {
  const annual = (Number(monthly) || 0) * 12;
  if (annual >= 1000) return `$${(annual / 1000).toFixed(0)}k/yr`;
  return `$${annual.toFixed(0)}/yr`;
}

export default function CEIDashboard({ results }) {
  if (!results) {
    return (
      <div style={s.empty}>
        <h2 style={s.emptyTitle}>No Analysis Results</h2>
        <p style={s.emptyText}>
          Run an analysis from the &quot;Run Analysis&quot; tab to see CEI metrics.
        </p>
      </div>
    );
  }

  const {
    nodes,
    weights,
    oscillation_status,
    total_potential_savings,
    graph_metrics,
  } = results;

  const counts = { critical: 0, elevated: 0, moderate: 0, low: 0 };
  nodes.forEach((n) => {
    counts[n.classification] = (counts[n.classification] || 0) + 1;
  });

  return (
    <div style={s.wrap}>
      <div style={s.summaryGrid}>
        <SummaryCard label="Total Nodes" value={nodes.length} color="blue" />
        <SummaryCard
          label="Critical Nodes"
          value={counts.critical}
          color="red"
        />
        <SummaryCard
          label="Waste Surfaced by Topology Analysis"
          value={formatMonthly(total_potential_savings)}
          sub={`${formatAnnual(total_potential_savings)} annualized`}
          color="green"
        />
        <SummaryCard
          label="Oscillation (Adaptive)"
          value={oscillation_status.suppression_active ? 'ACTIVE' : 'Clear'}
          color={oscillation_status.suppression_active ? 'yellow' : 'green'}
        />
      </div>

      <div style={s.card}>
        <h3 style={s.cardTitle}>Adaptive CEI Weights (Closed-Loop Control)</h3>
        <div style={s.weightGrid}>
          <WeightBar label="α (Centrality)" value={weights.alpha} color="#3498DB" />
          <WeightBar label="β (Entropy)" value={weights.beta} color="#9B59B6" />
          <WeightBar label="γ (Governance Risk)" value={weights.gamma} color="#E67E22" />
        </div>
      </div>

      <div style={s.card}>
        <h3 style={s.cardTitle}>Node CEI Analysis Results</h3>
        <div style={s.tableWrap}>
          <table style={s.table}>
            <thead>
              <tr>
                <th style={s.th}>Node ID</th>
                <th style={{ ...s.th, textAlign: 'right' }}>CEI Score</th>
                <th style={{ ...s.th, textAlign: 'right' }}>Centrality</th>
                <th style={{ ...s.th, textAlign: 'right' }}>Entropy</th>
                <th style={{ ...s.th, textAlign: 'right' }}>Risk</th>
                <th style={{ ...s.th, textAlign: 'center' }}>Classification</th>
                <th style={s.th}>Recommendation</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((node) => (
                <tr key={node.node_id} style={s.tr}>
                  <td style={{ ...s.td, fontFamily: 'monospace', fontSize: 12 }}>
                    {node.node_id}
                  </td>
                  <td style={{ ...s.td, textAlign: 'right', fontWeight: 700 }}>
                    {node.cei_score.toFixed(3)}
                  </td>
                  <td style={{ ...s.td, textAlign: 'right' }}>
                    {node.centrality.toFixed(3)}
                  </td>
                  <td style={{ ...s.td, textAlign: 'right' }}>
                    {node.entropy.toFixed(3)}
                  </td>
                  <td style={{ ...s.td, textAlign: 'right' }}>
                    {node.risk_factor.toFixed(3)}
                  </td>
                  <td style={{ ...s.td, textAlign: 'center' }}>
                    <ClassBadge classification={node.classification} />
                  </td>
                  <td style={{ ...s.td, fontSize: 12, color: '#566573' }}>
                    {node.recommendation}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div style={s.card}>
        <h3 style={s.cardTitle}>Dependency Graph Metrics</h3>
        <div style={s.metricsGrid}>
          <MetricItem label="Nodes" value={graph_metrics.total_nodes} />
          <MetricItem label="Edges" value={graph_metrics.total_edges} />
          <MetricItem
            label="Density"
            value={graph_metrics.density?.toFixed(3)}
          />
          <MetricItem label="Avg Degree" value={graph_metrics.avg_degree} />
          <MetricItem
            label="DAG"
            value={graph_metrics.is_dag ? 'Yes' : 'No'}
          />
        </div>
      </div>
    </div>
  );
}

function SummaryCard({ label, value, sub, color }) {
  return (
    <div style={s.summaryCard}>
      <p style={s.summaryLabel}>{label}</p>
      <p
        style={{
          ...s.summaryValue,
          color: SUMMARY_COLOR[color] || SUMMARY_COLOR.blue,
        }}
      >
        {value}
      </p>
      {sub && <p style={s.summarySub}>{sub}</p>}
    </div>
  );
}

function WeightBar({ label, value, color }) {
  return (
    <div>
      <div style={s.weightHeader}>
        <span style={s.weightLabel}>{label}</span>
        <span style={s.weightValue}>{(value * 100).toFixed(1)}%</span>
      </div>
      <div style={s.weightTrack}>
        <div
          style={{
            ...s.weightFill,
            width: `${value * 100}%`,
            background: color,
          }}
        />
      </div>
    </div>
  );
}

function ClassBadge({ classification }) {
  const c = CLASS_COLORS[classification] || {
    bg: '#EAEDED',
    text: '#566573',
    dot: '#95A5A6',
  };
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        background: c.bg,
        color: c.text,
        padding: '2px 10px',
        borderRadius: 12,
        fontSize: 11,
        fontWeight: 600,
        textTransform: 'capitalize',
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: c.dot,
          display: 'inline-block',
        }}
      />
      {classification}
    </span>
  );
}

function MetricItem({ label, value }) {
  return (
    <div style={s.metricItem}>
      <p style={s.metricLabel}>{label}</p>
      <p style={s.metricValue}>{value}</p>
    </div>
  );
}

const s = {
  wrap: { display: 'flex', flexDirection: 'column', gap: 16 },
  empty: { textAlign: 'center', padding: '64px 16px' },
  emptyTitle: {
    fontSize: 22,
    fontWeight: 600,
    color: '#7B8A8B',
    margin: '0 0 8px 0',
  },
  emptyText: { fontSize: 13, color: '#95A5A6', margin: 0 },
  summaryGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
    gap: 12,
  },
  summaryCard: {
    background: 'white',
    border: '1px solid #E5E8EB',
    borderRadius: 8,
    padding: 16,
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
  },
  summaryLabel: {
    margin: 0,
    fontSize: 11,
    color: '#7B8A8B',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  summaryValue: {
    margin: '6px 0 0 0',
    fontSize: 24,
    fontWeight: 700,
  },
  summarySub: {
    margin: '2px 0 0 0',
    fontSize: 11,
    color: '#16A085',
    fontWeight: 600,
  },
  card: {
    background: 'white',
    border: '1px solid #E5E8EB',
    borderRadius: 8,
    padding: 18,
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
  },
  cardTitle: {
    margin: '0 0 14px 0',
    fontSize: 13,
    fontWeight: 600,
    color: '#566573',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  weightGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
    gap: 16,
  },
  weightHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 12,
    marginBottom: 6,
  },
  weightLabel: { color: '#566573' },
  weightValue: { fontFamily: 'monospace', color: '#1B4F72', fontWeight: 600 },
  weightTrack: {
    height: 8,
    background: '#EAEDED',
    borderRadius: 4,
    overflow: 'hidden',
  },
  weightFill: { height: '100%', borderRadius: 4 },
  tableWrap: { overflowX: 'auto' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: {
    textAlign: 'left',
    padding: '10px 12px',
    background: '#F4F6F7',
    color: '#566573',
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    borderBottom: '1px solid #E5E8EB',
  },
  tr: { borderBottom: '1px solid #F4F6F7' },
  td: { padding: '10px 12px', color: '#1C2833' },
  metricsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
    gap: 10,
  },
  metricItem: {
    background: '#F4F6F7',
    borderRadius: 6,
    padding: '10px 12px',
  },
  metricLabel: {
    margin: 0,
    fontSize: 11,
    color: '#7B8A8B',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  metricValue: {
    margin: '4px 0 0 0',
    fontFamily: 'monospace',
    fontSize: 14,
    color: '#1B4F72',
    fontWeight: 600,
  },
};
