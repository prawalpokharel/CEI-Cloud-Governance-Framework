import { useMemo } from 'react';

/**
 * HPA vs CEI Benchmark (PR 7 — feature/cost-savings-engine)
 *
 * Side-by-side metrics card showing the difference between a naive
 * threshold-based HPA loop and the full CEI pipeline on the same
 * scenario. Comes from POST /benchmark/hpa-vs-cei (or the embedded
 * `benchmark` block in the /scenarios/:id/benchmark response).
 *
 * The component is purely presentational. Negative deltas (CEI does
 * fewer scaling events / fewer cascade failures / fewer governance
 * violations / lower cost / faster recovery) are rendered green;
 * positive deltas red.
 */

const METRICS = [
  {
    key: 'scaling_events',
    label: 'Scaling Events',
    suffix: '',
    lowerIsBetter: true,
    description: 'Total scale-up + scale-down actions issued in this window.',
  },
  {
    key: 'cascade_failures',
    label: 'Cascade Failures',
    suffix: '',
    lowerIsBetter: true,
    description: 'Critical-tier nodes adjacent in the dependency graph (Patent Module 109).',
  },
  {
    key: 'governance_violations',
    label: 'Governance Violations',
    suffix: '',
    lowerIsBetter: true,
    description: 'Modifications that would breach a tier min-replicas constraint (Module 110 blocks these).',
  },
  {
    key: 'total_monthly_cost_usd',
    label: 'Monthly Cost',
    prefix: '$',
    suffix: '',
    lowerIsBetter: true,
    description: 'Sum of monthly_cost across all nodes after the policy applies its recommendations.',
  },
  {
    key: 'recovery_time_minutes',
    label: 'Recovery Time',
    suffix: ' min',
    lowerIsBetter: true,
    description: 'Estimated minutes to converge after a failure: HPA = cooldown × events; CEI = hysteresis × (1 + osc/2).',
  },
];

function formatNum(v, prefix = '', suffix = '') {
  if (v === undefined || v === null) return '—';
  const abs = Math.abs(v);
  const formatted =
    abs >= 1000 ? abs.toLocaleString(undefined, { maximumFractionDigits: 0 }) : abs.toFixed(abs >= 10 ? 0 : 1);
  return `${prefix}${formatted}${suffix}`;
}

function deltaColor(value, lowerIsBetter) {
  if (!value) return '#7B8A8B';
  const better = lowerIsBetter ? value < 0 : value > 0;
  return better ? '#196F3D' : '#922B21';
}

function deltaLabel(value) {
  if (!value) return '0';
  return value > 0 ? `+${value.toFixed(1)}` : value.toFixed(1);
}

export default function HpaVsCeiBenchmark({ benchmark }) {
  const rows = useMemo(() => {
    if (!benchmark) return [];
    return METRICS.map((m) => {
      const hpa = benchmark.hpa?.[m.key];
      const cei = benchmark.cei?.[m.key];
      const delta = cei !== undefined && hpa !== undefined ? cei - hpa : null;
      return { ...m, hpa, cei, delta };
    });
  }, [benchmark]);

  if (!benchmark) return null;

  const summary = benchmark.scenario_summary || {};
  const annualSavings = benchmark.delta?.annual_savings_vs_hpa_usd ?? 0;

  return (
    <div style={s.wrap}>
      <div style={s.headerRow}>
        <div>
          <h3 style={s.title}>HPA vs. CEI Benchmark</h3>
          <p style={s.subtitle}>
            Same scenario, two control loops. CEI adds centrality-aware
            scaling, governance enforcement, and oscillation suppression on
            top of the HPA threshold model.
          </p>
        </div>
        <div style={s.savingsBadge}>
          <div style={s.savingsLabel}>EST. ANNUAL SAVINGS vs HPA</div>
          <div style={s.savingsValue}>
            ${Math.abs(annualSavings).toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </div>
          <div style={s.savingsAnnual}>
            {annualSavings >= 0 ? 'CEI cheaper than HPA' : 'CEI more expensive (safety trade-off)'}
          </div>
        </div>
      </div>

      <div style={s.summaryRow}>
        <span>
          <strong>{summary.node_count ?? '?'}</strong> nodes ·{' '}
          <strong>{summary.edge_count ?? '?'}</strong> edges
        </span>
        <span>
          <strong>{summary.critical_count ?? 0}</strong> critical ·{' '}
          <strong>{summary.elevated_count ?? 0}</strong> elevated
        </span>
        {summary.suppression_active && (
          <span style={s.suppression}>SUPPRESSION ACTIVE</span>
        )}
      </div>

      <div style={s.tableWrap}>
        <table style={s.table}>
          <thead>
            <tr>
              <th style={s.th}>Metric</th>
              <th style={{ ...s.th, textAlign: 'right' }}>HPA</th>
              <th style={{ ...s.th, textAlign: 'right' }}>CEI</th>
              <th style={{ ...s.th, textAlign: 'right' }}>Δ (CEI − HPA)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} style={s.tr}>
                <td style={s.td}>
                  <div style={s.metricName}>{row.label}</div>
                  <div style={s.metricDesc}>{row.description}</div>
                </td>
                <td
                  style={{
                    ...s.td,
                    textAlign: 'right',
                    fontFamily: 'monospace',
                    fontWeight: 600,
                    color: '#922B21',
                  }}
                >
                  {formatNum(row.hpa, row.prefix, row.suffix)}
                </td>
                <td
                  style={{
                    ...s.td,
                    textAlign: 'right',
                    fontFamily: 'monospace',
                    fontWeight: 600,
                    color: '#196F3D',
                  }}
                >
                  {formatNum(row.cei, row.prefix, row.suffix)}
                </td>
                <td
                  style={{
                    ...s.td,
                    textAlign: 'right',
                    fontFamily: 'monospace',
                    color: deltaColor(row.delta, row.lowerIsBetter),
                    fontWeight: 700,
                  }}
                >
                  {row.delta === null ? '—' : deltaLabel(row.delta)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={s.notesGrid}>
        <div style={s.noteCard}>
          <div style={s.noteHeader}>
            <span style={s.policyDot('#922B21')} /> HPA
          </div>
          <p style={s.noteText}>{benchmark.hpa?.notes}</p>
        </div>
        <div style={s.noteCard}>
          <div style={s.noteHeader}>
            <span style={s.policyDot('#196F3D')} /> CEI
          </div>
          <p style={s.noteText}>{benchmark.cei?.notes}</p>
        </div>
      </div>
    </div>
  );
}

const s = {
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
    marginBottom: 14,
  },
  title: { margin: 0, fontSize: 16, fontWeight: 600, color: '#1B4F72' },
  subtitle: { margin: '4px 0 0 0', fontSize: 12, color: '#7B8A8B', lineHeight: 1.5 },
  savingsBadge: {
    border: '2px solid #27AE60',
    background: '#EAFAF1',
    color: '#196F3D',
    borderRadius: 6,
    padding: '10px 16px',
    minWidth: 220,
    textAlign: 'center',
  },
  savingsLabel: { fontSize: 10, letterSpacing: '1.5px', opacity: 0.85 },
  savingsValue: { fontSize: 22, fontWeight: 700, marginTop: 2 },
  savingsAnnual: { fontSize: 11, marginTop: 2, opacity: 0.85 },
  summaryRow: {
    display: 'flex',
    gap: 16,
    fontSize: 13,
    color: '#34495E',
    marginBottom: 14,
    flexWrap: 'wrap',
    paddingBottom: 12,
    borderBottom: '1px solid #EAEDED',
  },
  suppression: {
    background: '#FDEDEC',
    color: '#922B21',
    fontSize: 11,
    fontWeight: 700,
    padding: '2px 10px',
    borderRadius: 12,
    letterSpacing: '0.5px',
  },
  tableWrap: { overflowX: 'auto', marginBottom: 14 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: {
    textAlign: 'left',
    padding: '8px 12px',
    background: '#F4F6F7',
    color: '#566573',
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    borderBottom: '1px solid #E5E8EB',
  },
  tr: { borderBottom: '1px solid #F4F6F7' },
  td: { padding: '10px 12px', color: '#1C2833', verticalAlign: 'top' },
  metricName: { fontWeight: 600, fontSize: 13 },
  metricDesc: { fontSize: 11, color: '#7B8A8B', marginTop: 2, lineHeight: 1.4 },
  notesGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
    gap: 12,
  },
  noteCard: {
    background: '#F4F6F7',
    borderRadius: 6,
    padding: 12,
  },
  noteHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontWeight: 600,
    fontSize: 13,
    color: '#1B4F72',
    marginBottom: 4,
  },
  policyDot: (color) => ({
    width: 10,
    height: 10,
    borderRadius: '50%',
    background: color,
    display: 'inline-block',
  }),
  noteText: { margin: 0, fontSize: 12, color: '#566573', lineHeight: 1.5 },
};
