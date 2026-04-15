import { useMemo } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
  ReferenceLine,
} from 'recharts';

/**
 * CEI Score Breakdown Chart (Patent Module 106: CEI Calculator)
 *
 * Stacked bar per node showing the three weighted contributions:
 *   α·C_i  (centrality contribution)
 *   β·H_i  (entropy / utilization-spread contribution)
 *   γ·R_i  (governance risk contribution)
 *
 * Threshold reference lines (τ_up / τ_down) make it visually obvious
 * which nodes the CEI controller will scale up vs scale down.
 *
 * Inputs:
 *   nodes    – analysis.nodes (array of {node_id, cei_score, centrality,
 *                                        entropy, risk_factor, classification})
 *   weights  – {alpha, beta, gamma}
 *   tauUp    – decision threshold for scale-up actions (default 0.65)
 *   tauDown  – decision threshold for scale-down actions (default 0.25)
 */

const CLASSIFICATION_COLORS = {
  critical: '#E74C3C',
  elevated: '#F39C12',
  moderate: '#3498DB',
  low: '#27AE60',
};

export default function CEIBreakdownChart({
  nodes,
  weights,
  tauUp = 0.65,
  tauDown = 0.25,
}) {
  const data = useMemo(() => {
    if (!nodes || !weights) return [];
    return nodes.map((n) => ({
      node: n.node_id,
      classification: n.classification,
      alphaC: +(weights.alpha * (n.centrality || 0)).toFixed(4),
      betaH: +(weights.beta * (n.entropy || 0)).toFixed(4),
      gammaR: +(weights.gamma * (n.risk_factor || 0)).toFixed(4),
      cei: n.cei_score,
    }));
  }, [nodes, weights]);

  if (!data.length) return null;

  const tooltipFormatter = (value, name) => [value.toFixed(3), name];

  return (
    <div style={styles.wrap}>
      <div style={styles.headerRow}>
        <div>
          <h3 style={styles.title}>CEI Score Composition</h3>
          <p style={styles.subtitle}>
            Per-node decomposition: CEI = α·C + β·H + γ·R · Patent Module 106
          </p>
        </div>
        <div style={styles.weightSummary}>
          <span style={styles.weightItem}>
            α = <strong>{weights.alpha.toFixed(2)}</strong>
          </span>
          <span style={styles.weightItem}>
            β = <strong>{weights.beta.toFixed(2)}</strong>
          </span>
          <span style={styles.weightItem}>
            γ = <strong>{weights.gamma.toFixed(2)}</strong>
          </span>
        </div>
      </div>

      <div style={styles.chartBox}>
        <ResponsiveContainer width="100%" height={Math.max(280, data.length * 28)}>
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 8, right: 32, bottom: 8, left: 96 }}
            barCategoryGap={6}
          >
            <CartesianGrid stroke="#EAEDED" strokeDasharray="3 3" />
            <XAxis
              type="number"
              domain={[0, 1]}
              tick={{ fontSize: 11, fill: '#566573' }}
              stroke="#B0BEC5"
            />
            <YAxis
              type="category"
              dataKey="node"
              tick={{ fontSize: 11, fill: '#1C2833' }}
              stroke="#B0BEC5"
              width={88}
            />
            <Tooltip formatter={tooltipFormatter} cursor={{ fill: '#F4F6F7' }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <ReferenceLine
              x={tauDown}
              stroke="#27AE60"
              strokeDasharray="4 4"
              label={{
                value: `τ_down=${tauDown}`,
                position: 'insideTopLeft',
                fill: '#196F3D',
                fontSize: 10,
              }}
            />
            <ReferenceLine
              x={tauUp}
              stroke="#E74C3C"
              strokeDasharray="4 4"
              label={{
                value: `τ_up=${tauUp}`,
                position: 'insideTopRight',
                fill: '#922B21',
                fontSize: 10,
              }}
            />
            <Bar dataKey="alphaC" stackId="cei" name="α·C (centrality)" fill="#3498DB">
              {data.map((d, i) => (
                <Cell key={`a-${i}`} />
              ))}
            </Bar>
            <Bar dataKey="betaH" stackId="cei" name="β·H (entropy)" fill="#9B59B6" />
            <Bar dataKey="gammaR" stackId="cei" name="γ·R (governance)" fill="#E67E22" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div style={styles.legendRow}>
        {Object.entries(CLASSIFICATION_COLORS).map(([cls, color]) => {
          const count = data.filter((d) => d.classification === cls).length;
          return (
            <span key={cls} style={styles.classChip}>
              <span style={{ ...styles.classDot, background: color }} />
              <span style={{ textTransform: 'capitalize' }}>{cls}</span>
              <strong style={styles.classCount}>{count}</strong>
            </span>
          );
        })}
      </div>
    </div>
  );
}

const styles = {
  wrap: {
    background: 'white',
    borderRadius: 8,
    padding: 20,
    boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
    border: '1px solid #E5E8EB',
  },
  headerRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    flexWrap: 'wrap',
    gap: 12,
    marginBottom: 12,
  },
  title: { margin: 0, fontSize: 16, fontWeight: 600, color: '#1B4F72' },
  subtitle: { margin: '4px 0 0 0', fontSize: 12, color: '#7B8A8B' },
  weightSummary: { display: 'flex', gap: 12, fontSize: 12, color: '#34495E' },
  weightItem: {
    background: '#F4F6F7',
    padding: '4px 10px',
    borderRadius: 4,
  },
  chartBox: { width: '100%' },
  legendRow: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 12,
    marginTop: 12,
    fontSize: 12,
    color: '#34495E',
  },
  classChip: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    background: '#F8F9FA',
    padding: '4px 10px',
    borderRadius: 12,
  },
  classDot: {
    width: 10,
    height: 10,
    borderRadius: '50%',
    display: 'inline-block',
  },
  classCount: { color: '#1B4F72', marginLeft: 2 },
};
