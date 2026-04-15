/**
 * DetailedAnalysisView — full nodes + dependency graph + breakdown
 * Self-contained (no D3 / PR4 component dependencies) so it ships
 * standalone with the OAuth flow.
 *
 * Used by /connect/[provider] to show the in-depth analysis on its own
 * page, without cluttering the provider tile on /connect.
 */

const CLASS_COLORS = {
  critical: { bg: '#FDEDEC', text: '#922B21', dot: '#E74C3C' },
  elevated: { bg: '#FEF9E7', text: '#7D6608', dot: '#F39C12' },
  moderate: { bg: '#EBF5FB', text: '#1B4F72', dot: '#3498DB' },
  low: { bg: '#EAFAF1', text: '#196F3D', dot: '#27AE60' },
};

export default function DetailedAnalysisView({ topology, analysis }) {
  if (!analysis || !analysis.nodes) return null;
  const nodes = analysis.nodes;
  const edges = topology?.edges || [];
  const w = analysis.weights || { alpha: 0.4, beta: 0.35, gamma: 0.25 };

  const positionedNodes = layoutCircular(nodes, 360, 280, 110);
  const nodeIndex = {};
  positionedNodes.forEach((n) => {
    nodeIndex[n.node_id] = n;
  });

  return (
    <div style={dv.wrap}>
      <div style={dv.summaryBand}>
        <SummaryPill label="Nodes" value={nodes.length} color="#1B4F72" />
        <SummaryPill
          label="Critical"
          value={nodes.filter((n) => n.classification === 'critical').length}
          color="#922B21"
        />
        <SummaryPill
          label="Elevated"
          value={nodes.filter((n) => n.classification === 'elevated').length}
          color="#7D6608"
        />
        <SummaryPill
          label="Moderate"
          value={nodes.filter((n) => n.classification === 'moderate').length}
          color="#1B4F72"
        />
        <SummaryPill
          label="Low"
          value={nodes.filter((n) => n.classification === 'low').length}
          color="#196F3D"
        />
        <SummaryPill
          label="Total savings"
          value={`$${(analysis.total_potential_savings || 0).toFixed(0)}/mo`}
          color="#196F3D"
        />
      </div>

      <div style={dv.section}>
        <h4 style={dv.sectionTitle}>Adaptive CEI weights (Patent Module 107)</h4>
        <div style={dv.weightRow}>
          <WeightBar label="α (centrality)" value={w.alpha} color="#3498DB" />
          <WeightBar label="β (entropy)" value={w.beta} color="#9B59B6" />
          <WeightBar label="γ (governance)" value={w.gamma} color="#E67E22" />
        </div>
      </div>

      <div style={dv.section}>
        <h4 style={dv.sectionTitle}>Dependency graph</h4>
        <p style={dv.sectionSub}>
          Discovered topology with CEI-classified nodes. Node fill =
          classification, label below each node.
        </p>
        <div style={dv.svgWrap}>
          <svg viewBox="0 0 720 560" width="100%" style={dv.svg}>
            {edges.map((e, i) => {
              const src = nodeIndex[Array.isArray(e) ? e[0] : e.source];
              const dst = nodeIndex[Array.isArray(e) ? e[1] : e.target];
              if (!src || !dst) return null;
              return (
                <line
                  key={`e-${i}`}
                  x1={src.x}
                  y1={src.y}
                  x2={dst.x}
                  y2={dst.y}
                  stroke="#B0BEC5"
                  strokeWidth={1.4}
                  strokeOpacity={0.7}
                />
              );
            })}
            {positionedNodes.map((n) => {
              const c =
                CLASS_COLORS[n.classification] || CLASS_COLORS.moderate;
              const r = 12 + (n.centrality || 0) * 22;
              return (
                <g key={n.node_id}>
                  <circle cx={n.x} cy={n.y} r={r} fill={c.dot} opacity={0.25} />
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r={r * 0.7}
                    fill={c.dot}
                    stroke={c.text}
                    strokeWidth={1.2}
                  />
                  <text
                    x={n.x}
                    y={n.y + r + 14}
                    textAnchor="middle"
                    fontSize={11}
                    fill="#1C2833"
                    style={{
                      paintOrder: 'stroke',
                      stroke: 'white',
                      strokeWidth: 3,
                      strokeLinejoin: 'round',
                    }}
                  >
                    {n.node_id}
                  </text>
                  <text
                    x={n.x}
                    y={n.y + 4}
                    textAnchor="middle"
                    fontSize={10}
                    fontWeight={700}
                    fill="white"
                  >
                    {n.cei_score.toFixed(2)}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
        <div style={dv.legendRow}>
          {Object.entries(CLASS_COLORS).map(([cls, c]) => (
            <span key={cls} style={dv.legendItem}>
              <span style={{ ...dv.legendDot, background: c.dot }} />
              <span style={dv.legendLabel}>{cls}</span>
            </span>
          ))}
        </div>
      </div>

      <div style={dv.section}>
        <h4 style={dv.sectionTitle}>Per-node CEI scores</h4>
        <div style={dv.tableWrap}>
          <table style={dv.table}>
            <thead>
              <tr>
                <th style={dv.th}>Node</th>
                <th style={{ ...dv.th, textAlign: 'right' }}>C</th>
                <th style={{ ...dv.th, textAlign: 'right' }}>H</th>
                <th style={{ ...dv.th, textAlign: 'right' }}>R</th>
                <th style={{ ...dv.th, textAlign: 'right' }}>CEI</th>
                <th style={{ ...dv.th, textAlign: 'center' }}>Class</th>
                <th style={dv.th}>Recommendation</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((n) => {
                const c =
                  CLASS_COLORS[n.classification] || CLASS_COLORS.moderate;
                return (
                  <tr key={n.node_id} style={dv.tr}>
                    <td
                      style={{
                        ...dv.td,
                        fontFamily: 'monospace',
                        fontSize: 12,
                      }}
                    >
                      {n.node_id}
                    </td>
                    <td
                      style={{
                        ...dv.td,
                        textAlign: 'right',
                        fontVariantNumeric: 'tabular-nums',
                      }}
                    >
                      {n.centrality?.toFixed(3)}
                    </td>
                    <td
                      style={{
                        ...dv.td,
                        textAlign: 'right',
                        fontVariantNumeric: 'tabular-nums',
                      }}
                    >
                      {n.entropy?.toFixed(3)}
                    </td>
                    <td
                      style={{
                        ...dv.td,
                        textAlign: 'right',
                        fontVariantNumeric: 'tabular-nums',
                      }}
                    >
                      {n.risk_factor?.toFixed(3)}
                    </td>
                    <td
                      style={{
                        ...dv.td,
                        textAlign: 'right',
                        fontWeight: 700,
                        fontVariantNumeric: 'tabular-nums',
                      }}
                    >
                      {n.cei_score?.toFixed(3)}
                    </td>
                    <td style={{ ...dv.td, textAlign: 'center' }}>
                      <span
                        style={{
                          background: c.bg,
                          color: c.text,
                          padding: '2px 10px',
                          borderRadius: 12,
                          fontSize: 11,
                          fontWeight: 600,
                          textTransform: 'capitalize',
                        }}
                      >
                        {n.classification}
                      </span>
                    </td>
                    <td style={{ ...dv.td, fontSize: 12, color: '#566573' }}>
                      {n.recommendation}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {analysis.oscillation_status && (
        <div style={dv.section}>
          <h4 style={dv.sectionTitle}>Oscillation status (Module 108)</h4>
          <div style={dv.osciRow}>
            <SummaryPill
              label="Suppression"
              value={
                analysis.oscillation_status.suppression_active
                  ? 'ACTIVE'
                  : 'CLEAR'
              }
              color={
                analysis.oscillation_status.suppression_active
                  ? '#922B21'
                  : '#196F3D'
              }
            />
            <SummaryPill
              label="Oscillating"
              value={`${analysis.oscillation_status.oscillating_node_count ?? 0} / ${
                analysis.oscillation_status.total_nodes ?? nodes.length
              }`}
              color="#1B4F72"
            />
            <SummaryPill
              label="Hysteresis window"
              value={`${
                analysis.oscillation_status.hysteresis_window_minutes ?? 15
              } min`}
              color="#1B4F72"
            />
          </div>
        </div>
      )}
    </div>
  );
}

function SummaryPill({ label, value, color }) {
  return (
    <div style={dv.pill}>
      <div style={dv.pillLabel}>{label}</div>
      <div style={{ ...dv.pillValue, color }}>{value}</div>
    </div>
  );
}

function WeightBar({ label, value, color }) {
  return (
    <div style={dv.weightItem}>
      <div style={dv.weightHead}>
        <span style={dv.weightLabel}>{label}</span>
        <span style={dv.weightVal}>{(value * 100).toFixed(1)}%</span>
      </div>
      <div style={dv.weightTrack}>
        <div
          style={{
            ...dv.weightFill,
            width: `${value * 100}%`,
            background: color,
          }}
        />
      </div>
    </div>
  );
}

function layoutCircular(nodes, cx, cy, r) {
  const n = nodes.length;
  return nodes.map((node, i) => {
    const angle = (2 * Math.PI * i) / n - Math.PI / 2;
    return {
      ...node,
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
    };
  });
}

const dv = {
  wrap: { display: 'flex', flexDirection: 'column', gap: 20 },
  summaryBand: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
    gap: 10,
  },
  pill: {
    background: 'white',
    border: '1px solid #E5E8EB',
    borderRadius: 6,
    padding: '12px 14px',
    textAlign: 'center',
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
  },
  pillLabel: {
    fontSize: 11,
    color: '#7B8A8B',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  pillValue: { fontSize: 22, fontWeight: 700, marginTop: 4 },
  section: {
    background: 'white',
    border: '1px solid #E5E8EB',
    borderRadius: 8,
    padding: 18,
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
  },
  sectionTitle: {
    margin: '0 0 12px 0',
    fontSize: 13,
    fontWeight: 600,
    color: '#566573',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  sectionSub: { margin: '0 0 12px 0', fontSize: 12, color: '#7B8A8B' },
  weightRow: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
    gap: 14,
  },
  weightItem: {},
  weightHead: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 12,
    marginBottom: 4,
  },
  weightLabel: { color: '#566573' },
  weightVal: { fontFamily: 'monospace', color: '#1B4F72', fontWeight: 600 },
  weightTrack: {
    height: 8,
    background: '#EAEDED',
    borderRadius: 4,
    overflow: 'hidden',
  },
  weightFill: { height: '100%', borderRadius: 4 },
  svgWrap: {
    background: '#FAFBFC',
    border: '1px solid #E5E8EB',
    borderRadius: 6,
    padding: 12,
  },
  svg: { display: 'block', maxWidth: '100%' },
  legendRow: { display: 'flex', gap: 16, marginTop: 10, flexWrap: 'wrap' },
  legendItem: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 },
  legendDot: {
    width: 10,
    height: 10,
    borderRadius: '50%',
    display: 'inline-block',
  },
  legendLabel: { color: '#566573', textTransform: 'capitalize' },
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
  osciRow: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
    gap: 10,
  },
};
