import React from 'react';

/**
 * Dependency Graph Visualization
 * Displays the infrastructure dependency graph with CEI-colored nodes.
 * Maps to Patent Module 103: Graph Constructor.
 *
 * Light theme to match the /demo and main page treatment.
 * (The full force-directed D3 version lives under
 * components/visualization/D3DependencyGraph.js — this is the simpler
 * fallback used from the main "Run Analysis" workflow.)
 */
export default function GraphVisualization({ results }) {
  if (!results) {
    return (
      <div style={s.empty}>
        <h2 style={s.emptyTitle}>No Graph Data</h2>
        <p style={s.emptyText}>
          Run an analysis first to visualize the dependency graph.
        </p>
      </div>
    );
  }

  const { nodes, graph_metrics } = results;
  const classColors = {
    critical: '#E74C3C',
    elevated: '#F39C12',
    moderate: '#3498DB',
    low: '#27AE60',
  };

  const centerX = 300;
  const centerY = 250;
  const radius = 180;
  const nodePositions = nodes.map((n, i) => {
    const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
    return {
      ...n,
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
    };
  });

  return (
    <div style={s.wrap}>
      <h2 style={s.heading}>Dependency Graph</h2>

      <div style={s.card}>
        <svg viewBox="0 0 600 500" style={s.svg}>
          {nodePositions.map((n, i) => {
            const next = nodePositions[(i + 1) % nodePositions.length];
            const skip = nodePositions[(i + 3) % nodePositions.length];
            return (
              <g key={`edge-${i}`}>
                <line
                  x1={n.x}
                  y1={n.y}
                  x2={next.x}
                  y2={next.y}
                  stroke="#B0BEC5"
                  strokeWidth="1"
                />
                {i % 2 === 0 && (
                  <line
                    x1={n.x}
                    y1={n.y}
                    x2={skip.x}
                    y2={skip.y}
                    stroke="#CFD8DC"
                    strokeWidth="0.5"
                    strokeDasharray="4"
                  />
                )}
              </g>
            );
          })}

          {nodePositions.map((n) => {
            const nodeRadius = 12 + n.cei_score * 20;
            const color = classColors[n.classification] || '#95A5A6';
            return (
              <g key={n.node_id}>
                <circle cx={n.x} cy={n.y} r={nodeRadius} fill={color} opacity={0.25} />
                <circle
                  cx={n.x}
                  cy={n.y}
                  r={nodeRadius * 0.7}
                  fill={color}
                  opacity={0.85}
                />
                <text
                  x={n.x}
                  y={n.y + nodeRadius + 14}
                  textAnchor="middle"
                  fill="#34495E"
                  fontSize="10"
                >
                  {n.node_id}
                </text>
                <text
                  x={n.x}
                  y={n.y + 3}
                  textAnchor="middle"
                  fill="white"
                  fontSize="9"
                  fontWeight="bold"
                >
                  {n.cei_score.toFixed(2)}
                </text>
              </g>
            );
          })}
        </svg>

        <div style={s.legendRow}>
          {Object.entries(classColors).map(([cls, color]) => (
            <div key={cls} style={s.legendItem}>
              <div
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: '50%',
                  background: color,
                }}
              />
              <span style={s.legendLabel}>{cls}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={s.card}>
        <h3 style={s.cardTitle}>Topology Analysis</h3>
        <p style={s.summaryText}>
          Graph contains {graph_metrics.total_nodes} nodes connected by{' '}
          {graph_metrics.total_edges} dependency edges with density{' '}
          {graph_metrics.density?.toFixed(3)}. Node sizes reflect CEI scores
          — larger nodes have higher centrality-entropy index values and
          require more careful governance-aware handling before modification.
        </p>
      </div>
    </div>
  );
}

const s = {
  wrap: { display: 'flex', flexDirection: 'column', gap: 16 },
  heading: {
    margin: 0,
    fontSize: 18,
    fontWeight: 600,
    color: '#1B4F72',
  },
  empty: { textAlign: 'center', padding: '64px 16px' },
  emptyTitle: {
    fontSize: 22,
    fontWeight: 600,
    color: '#7B8A8B',
    margin: '0 0 8px 0',
  },
  emptyText: { fontSize: 13, color: '#95A5A6', margin: 0 },
  card: {
    background: 'white',
    border: '1px solid #E5E8EB',
    borderRadius: 8,
    padding: 18,
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
  },
  cardTitle: {
    margin: '0 0 12px 0',
    fontSize: 13,
    fontWeight: 600,
    color: '#566573',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  svg: { width: '100%', maxWidth: 720, display: 'block', margin: '0 auto' },
  legendRow: {
    display: 'flex',
    justifyContent: 'center',
    gap: 18,
    marginTop: 12,
  },
  legendItem: { display: 'flex', alignItems: 'center', gap: 6 },
  legendLabel: {
    fontSize: 12,
    color: '#566573',
    textTransform: 'capitalize',
  },
  summaryText: {
    margin: 0,
    fontSize: 13,
    color: '#34495E',
    lineHeight: 1.6,
  },
};
