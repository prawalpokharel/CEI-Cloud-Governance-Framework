import React from 'react';

/**
 * Dependency Graph Visualization
 * Displays the infrastructure dependency graph with CEI-colored nodes.
 * Maps to Patent Module 103: Graph Constructor.
 */
export default function GraphVisualization({ results }) {
  if (!results) {
    return (
      <div className="text-center py-20">
        <h2 className="text-2xl font-bold text-gray-400 mb-4">No Graph Data</h2>
        <p className="text-gray-500">Run an analysis first to visualize the dependency graph.</p>
      </div>
    );
  }

  const { nodes, graph_metrics } = results;
  const classColors = { critical: '#ef4444', elevated: '#eab308', moderate: '#3b82f6', low: '#22c55e' };

  // Simple circular layout for nodes
  const centerX = 300;
  const centerY = 250;
  const radius = 180;
  const nodePositions = nodes.map((n, i) => {
    const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
    return { ...n, x: centerX + radius * Math.cos(angle), y: centerY + radius * Math.sin(angle) };
  });

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-bold text-white">Dependency Graph</h2>

      <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <svg viewBox="0 0 600 500" className="w-full max-w-2xl mx-auto">
          {/* Draw edges (simple connections between adjacent nodes for demo) */}
          {nodePositions.map((n, i) => {
            const next = nodePositions[(i + 1) % nodePositions.length];
            const skip = nodePositions[(i + 3) % nodePositions.length];
            return (
              <g key={`edge-${i}`}>
                <line x1={n.x} y1={n.y} x2={next.x} y2={next.y} stroke="#374151" strokeWidth="1" />
                {i % 2 === 0 && <line x1={n.x} y1={n.y} x2={skip.x} y2={skip.y} stroke="#1f2937" strokeWidth="0.5" strokeDasharray="4" />}
              </g>
            );
          })}

          {/* Draw nodes */}
          {nodePositions.map((n) => {
            const nodeRadius = 12 + n.cei_score * 20;
            const color = classColors[n.classification] || '#6b7280';
            return (
              <g key={n.node_id}>
                <circle cx={n.x} cy={n.y} r={nodeRadius} fill={color} opacity={0.3} />
                <circle cx={n.x} cy={n.y} r={nodeRadius * 0.7} fill={color} opacity={0.7} />
                <text x={n.x} y={n.y + nodeRadius + 14} textAnchor="middle" fill="#9ca3af" fontSize="9">{n.node_id}</text>
                <text x={n.x} y={n.y + 3} textAnchor="middle" fill="white" fontSize="8" fontWeight="bold">{n.cei_score.toFixed(2)}</text>
              </g>
            );
          })}
        </svg>

        {/* Legend */}
        <div className="flex justify-center gap-6 mt-4">
          {Object.entries(classColors).map(([cls, color]) => (
            <div key={cls} className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-xs text-gray-400 capitalize">{cls}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Graph Stats */}
      <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
        <h3 className="text-sm font-semibold text-gray-400 mb-3">Topology Analysis</h3>
        <p className="text-sm text-gray-300">
          Graph contains {graph_metrics.total_nodes} nodes connected by {graph_metrics.total_edges} dependency edges
          with density {graph_metrics.density?.toFixed(3)}. Node sizes reflect CEI scores — larger nodes have higher
          centrality-entropy index values and require more careful governance-aware handling before modification.
        </p>
      </div>
    </div>
  );
}
