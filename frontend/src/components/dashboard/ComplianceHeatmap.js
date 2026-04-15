import { useMemo } from 'react';

/**
 * Governance Compliance Heatmap (Patent Module 104: Governance Policy Store)
 *
 * Groups nodes by governance tier and renders each as a heatmap cell
 * colored by R_i (governance risk factor). Lower R_i = better compliance.
 * Hovering a cell reveals which constraints contributed to its risk
 * (recommendation/classification proxy from the analysis output).
 *
 * Inputs:
 *   topology – { nodes: [{id, tier, ...}] }
 *   analysis – { nodes: [{node_id, risk_factor, classification,
 *                         recommendation, ...}] }
 *   governance – { tiers: {...}, policies: [...] }
 */

const TIER_ORDER = [
  'critical',
  'core',
  'edge',
  'supporting',
  'discretionary',
];

function riskColor(r) {
  // Heatmap: green (low risk) → yellow → red (high risk)
  // r in [0, 1]
  const clamped = Math.max(0, Math.min(1, r));
  if (clamped < 0.33) {
    // green → yellow
    const t = clamped / 0.33;
    return interp([39, 174, 96], [241, 196, 15], t);
  }
  if (clamped < 0.66) {
    // yellow → orange
    const t = (clamped - 0.33) / 0.33;
    return interp([241, 196, 15], [230, 126, 34], t);
  }
  // orange → red
  const t = (clamped - 0.66) / 0.34;
  return interp([230, 126, 34], [231, 76, 60], t);
}
function interp(a, b, t) {
  const r = Math.round(a[0] + (b[0] - a[0]) * t);
  const g = Math.round(a[1] + (b[1] - a[1]) * t);
  const bl = Math.round(a[2] + (b[2] - a[2]) * t);
  return `rgb(${r}, ${g}, ${bl})`;
}

export default function ComplianceHeatmap({ topology, analysis, governance }) {
  const grouped = useMemo(() => {
    const nodes = topology?.nodes || [];
    const byNode = {};
    (analysis?.nodes || []).forEach((n) => {
      byNode[n.node_id] = n;
    });

    const groups = {};
    nodes.forEach((n) => {
      const tier = n.tier || 'supporting';
      if (!groups[tier]) groups[tier] = [];
      const a = byNode[n.id];
      groups[tier].push({
        id: n.id,
        risk: a?.risk_factor ?? 0,
        classification: a?.classification ?? 'unknown',
        recommendation: a?.recommendation ?? 'no_action',
      });
    });
    return groups;
  }, [topology, analysis]);

  const tierNames = useMemo(() => {
    const present = Object.keys(grouped);
    const ordered = TIER_ORDER.filter((t) => present.includes(t));
    const extras = present.filter((t) => !TIER_ORDER.includes(t));
    return [...ordered, ...extras];
  }, [grouped]);

  const policyCount = (governance?.policies || []).length;
  const tierCount = Object.keys(governance?.tiers || {}).length;

  // Aggregate risk per tier for the side-summary
  const tierStats = useMemo(() => {
    const stats = {};
    tierNames.forEach((t) => {
      const arr = grouped[t] || [];
      const avg =
        arr.length === 0
          ? 0
          : arr.reduce((s, x) => s + x.risk, 0) / arr.length;
      const violations = arr.filter((x) => x.classification === 'critical').length;
      stats[t] = { avg, count: arr.length, violations };
    });
    return stats;
  }, [grouped, tierNames]);

  if (!analysis) return null;

  return (
    <div style={styles.wrap}>
      <div style={styles.headerRow}>
        <div>
          <h3 style={styles.title}>Governance Compliance Heatmap</h3>
          <p style={styles.subtitle}>
            Patent Module 104 — per-node R_i grouped by governance tier.
            {policyCount} {policyCount === 1 ? 'policy' : 'policies'} ·{' '}
            {tierCount} tiers loaded.
          </p>
        </div>
        <div style={styles.scaleLegend}>
          <span style={styles.scaleLabel}>R_i</span>
          <div style={styles.scaleBar}>
            <span style={styles.scaleEnd}>0.0</span>
            <span style={styles.scaleEnd}>1.0</span>
          </div>
        </div>
      </div>

      <div style={styles.grid}>
        {tierNames.map((tier) => (
          <div key={tier} style={styles.tierBlock}>
            <div style={styles.tierHeader}>
              <span style={styles.tierName}>{tier}</span>
              <span style={styles.tierMeta}>
                {tierStats[tier].count} nodes · avg R_i ={' '}
                <strong>{tierStats[tier].avg.toFixed(3)}</strong>
                {tierStats[tier].violations > 0 && (
                  <span style={styles.violationBadge}>
                    {tierStats[tier].violations} critical
                  </span>
                )}
              </span>
            </div>
            <div style={styles.tierRow}>
              {grouped[tier].map((cell) => (
                <div
                  key={cell.id}
                  style={{
                    ...styles.cell,
                    background: riskColor(cell.risk),
                  }}
                  title={`${cell.id} · R=${cell.risk.toFixed(
                    3
                  )} · ${cell.classification.toUpperCase()} · ${
                    cell.recommendation
                  }`}
                >
                  <span style={styles.cellName}>{cell.id}</span>
                  <span style={styles.cellRisk}>{cell.risk.toFixed(2)}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const styles = {
  wrap: {
    background: 'white',
    borderRadius: 8,
    padding: 20,
    border: '1px solid #E5E8EB',
    boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
  },
  headerRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    flexWrap: 'wrap',
    gap: 12,
    marginBottom: 16,
  },
  title: { margin: 0, fontSize: 16, fontWeight: 600, color: '#1B4F72' },
  subtitle: { margin: '4px 0 0 0', fontSize: 12, color: '#7B8A8B' },
  scaleLegend: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    minWidth: 140,
  },
  scaleLabel: {
    fontSize: 11,
    color: '#7B8A8B',
    textTransform: 'uppercase',
    letterSpacing: '1px',
  },
  scaleBar: {
    height: 12,
    width: '100%',
    borderRadius: 3,
    background:
      'linear-gradient(90deg, rgb(39,174,96), rgb(241,196,15), rgb(230,126,34), rgb(231,76,60))',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '0 4px',
    color: 'white',
    fontSize: 9,
    fontWeight: 700,
    textShadow: '0 1px 1px rgba(0,0,0,0.3)',
  },
  scaleEnd: {},
  grid: { display: 'flex', flexDirection: 'column', gap: 14 },
  tierBlock: {
    border: '1px solid #EAEDED',
    borderRadius: 6,
    padding: 12,
    background: '#FAFBFC',
  },
  tierHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    marginBottom: 8,
  },
  tierName: {
    fontWeight: 600,
    color: '#1B4F72',
    textTransform: 'capitalize',
    fontSize: 14,
  },
  tierMeta: { fontSize: 12, color: '#566573' },
  violationBadge: {
    background: '#FDEDEC',
    color: '#922B21',
    padding: '2px 8px',
    borderRadius: 10,
    fontSize: 11,
    fontWeight: 600,
    marginLeft: 8,
  },
  tierRow: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
    gap: 6,
  },
  cell: {
    padding: '8px 10px',
    borderRadius: 4,
    color: 'white',
    fontSize: 11,
    cursor: 'help',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    minHeight: 42,
    textShadow: '0 1px 1px rgba(0,0,0,0.2)',
  },
  cellName: { fontWeight: 600, fontSize: 11 },
  cellRisk: { fontSize: 13, fontWeight: 700 },
};
