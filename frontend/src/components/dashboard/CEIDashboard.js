import React from 'react';

/**
 * CEI Dashboard - Displays Centrality-Entropy Index analysis results
 * with real-time visualization of node classifications, weights, and
 * optimization recommendations.
 */
export default function CEIDashboard({ results }) {
  if (!results) {
    return (
      <div className="text-center py-20">
        <h2 className="text-2xl font-bold text-gray-400 mb-4">No Analysis Results</h2>
        <p className="text-gray-500">Run an analysis from the &quot;Run Analysis&quot; tab to see CEI metrics.</p>
      </div>
    );
  }

  const { nodes, weights, oscillation_status, total_potential_savings, graph_metrics } = results;

  const classificationCounts = { critical: 0, elevated: 0, moderate: 0, low: 0 };
  nodes.forEach((n) => { classificationCounts[n.classification] = (classificationCounts[n.classification] || 0) + 1; });

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <SummaryCard label="Total Nodes" value={nodes.length} color="blue" />
        <SummaryCard label="Potential Savings" value={`$${total_potential_savings.toFixed(0)}/mo`} color="green" />
        <SummaryCard label="Critical Nodes" value={classificationCounts.critical} color="red" />
        <SummaryCard label="Oscillation" value={oscillation_status.suppression_active ? 'ACTIVE' : 'Clear'} color={oscillation_status.suppression_active ? 'yellow' : 'green'} />
      </div>

      {/* CEI Weights */}
      <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
        <h3 className="text-sm font-semibold text-gray-400 mb-3">Adaptive CEI Weights (Closed-Loop Control)</h3>
        <div className="grid grid-cols-3 gap-4">
          <WeightBar label="α (Centrality)" value={weights.alpha} color="blue" />
          <WeightBar label="β (Entropy)" value={weights.beta} color="purple" />
          <WeightBar label="γ (Governance Risk)" value={weights.gamma} color="orange" />
        </div>
      </div>

      {/* Node Results Table */}
      <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
        <div className="p-4 border-b border-gray-800">
          <h3 className="text-sm font-semibold text-gray-400">Node CEI Analysis Results</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-800">
              <tr>
                <th className="px-4 py-2 text-left text-gray-400">Node ID</th>
                <th className="px-4 py-2 text-right text-gray-400">CEI Score</th>
                <th className="px-4 py-2 text-right text-gray-400">Centrality</th>
                <th className="px-4 py-2 text-right text-gray-400">Entropy</th>
                <th className="px-4 py-2 text-right text-gray-400">Risk</th>
                <th className="px-4 py-2 text-center text-gray-400">Classification</th>
                <th className="px-4 py-2 text-left text-gray-400">Recommendation</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((node) => (
                <tr key={node.node_id} className="border-t border-gray-800 hover:bg-gray-800/50">
                  <td className="px-4 py-2 font-mono text-xs">{node.node_id}</td>
                  <td className="px-4 py-2 text-right font-bold">{node.cei_score.toFixed(3)}</td>
                  <td className="px-4 py-2 text-right">{node.centrality.toFixed(3)}</td>
                  <td className="px-4 py-2 text-right">{node.entropy.toFixed(3)}</td>
                  <td className="px-4 py-2 text-right">{node.risk_factor.toFixed(3)}</td>
                  <td className="px-4 py-2 text-center">
                    <ClassBadge classification={node.classification} />
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-400">{node.recommendation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Graph Metrics */}
      <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
        <h3 className="text-sm font-semibold text-gray-400 mb-3">Dependency Graph Metrics</h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
          <MetricItem label="Nodes" value={graph_metrics.total_nodes} />
          <MetricItem label="Edges" value={graph_metrics.total_edges} />
          <MetricItem label="Density" value={graph_metrics.density?.toFixed(3)} />
          <MetricItem label="Avg Degree" value={graph_metrics.avg_degree} />
          <MetricItem label="DAG" value={graph_metrics.is_dag ? 'Yes' : 'No'} />
        </div>
      </div>
    </div>
  );
}

function SummaryCard({ label, value, color }) {
  const colorMap = { blue: 'text-blue-400', green: 'text-green-400', red: 'text-red-400', yellow: 'text-yellow-400' };
  return (
    <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`text-2xl font-bold ${colorMap[color]}`}>{value}</p>
    </div>
  );
}

function WeightBar({ label, value, color }) {
  const colorMap = { blue: 'bg-blue-500', purple: 'bg-purple-500', orange: 'bg-orange-500' };
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-gray-400">{label}</span>
        <span className="text-white font-mono">{(value * 100).toFixed(1)}%</span>
      </div>
      <div className="h-2 bg-gray-800 rounded-full">
        <div className={`h-2 rounded-full ${colorMap[color]}`} style={{ width: `${value * 100}%` }} />
      </div>
    </div>
  );
}

function ClassBadge({ classification }) {
  const styles = {
    critical: 'bg-red-900 text-red-300',
    elevated: 'bg-yellow-900 text-yellow-300',
    moderate: 'bg-blue-900 text-blue-300',
    low: 'bg-green-900 text-green-300',
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[classification] || 'bg-gray-700 text-gray-300'}`}>
      {classification}
    </span>
  );
}

function MetricItem({ label, value }) {
  return (
    <div className="bg-gray-800 rounded p-2">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-sm font-mono text-white">{value}</p>
    </div>
  );
}
