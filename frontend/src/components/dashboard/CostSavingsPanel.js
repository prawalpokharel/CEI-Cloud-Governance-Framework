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
} from 'recharts';

/**
 * Cost Savings Panel (PR 7 — feature/cost-savings-engine)
 *
 * Renders the response from POST /pricing/savings:
 *   - Headline numbers (current, suggested, monthly + annual savings)
 *   - Per-node table: action, current vs. suggested instance/replicas,
 *     monthly delta, annual delta, rationale
 *   - Stacked bar chart: current monthly cost vs. suggested monthly cost
 *     per node, sorted by absolute delta so the biggest changes surface
 *
 * Inputs:
 *   savings – { total_current_monthly_usd, total_suggested_monthly_usd,
 *               total_monthly_savings_usd, total_annual_savings_usd,
 *               node_recommendations: [{node_id, action, current_*,
 *                 suggested_*, monthly_delta_usd, annual_delta_usd,
 *                 rationale}] }
 */

const ACTION_COLORS = {
  downsize: { bg: '#EAFAF1', text: '#196F3D' },
  reduce_replicas: { bg: '#EBF5FB', text: '#1B4F72' },
  upsize: { bg: '#FDEDEC', text: '#922B21' },
  no_action: { bg: '#F4F6F7', text: '#7B8A8B' },
};

export default function CostSavingsPanel({ savings }) {
  const sortedRecs = useMemo(() => {
    if (!savings?.node_recommendations) return [];
    return [...savings.node_recommendations].sort(
      (a, b) => Math.abs(b.monthly_delta_usd) - Math.abs(a.monthly_delta_usd)
    );
  }, [savings]);

  const chartData = useMemo(() => {
    return sortedRecs.slice(0, 14).map((r) => ({
      node: r.node_id,
      current: r.current_monthly_usd,
      suggested: r.suggested_monthly_usd,
    }));
  }, [sortedRecs]);

  if (!savings) return null;

  const monthly = savings.total_monthly_savings_usd ?? 0;
  const annual = savings.total_annual_savings_usd ?? 0;
  const isSaving = monthly >= 0;

  return (
    <div style={s.wrap}>
      <div style={s.headerRow}>
        <div>
          <h3 style={s.title}>Cost Savings (CEI Rightsizing)</h3>
          <p style={s.subtitle}>
            PR 7 — per-node action derived from the analysis recommendation +
            embedded provider price tables (AWS / Azure / GCP / Kubernetes).
          </p>
        </div>
        <div
          style={{
            ...s.savingsBadge,
            background: isSaving ? '#EAFAF1' : '#FDEDEC',
            color: isSaving ? '#196F3D' : '#922B21',
            borderColor: isSaving ? '#27AE60' : '#E74C3C',
          }}
        >
          <div style={s.savingsLabel}>{isSaving ? 'MONTHLY SAVINGS' : 'EXTRA SPEND'}</div>
          <div style={s.savingsValue}>
            ${Math.abs(monthly).toLocaleString(undefined, { maximumFractionDigits: 0 })}
            <span style={s.perMo}>/mo</span>
          </div>
          <div style={s.savingsAnnual}>
            ${Math.abs(annual).toLocaleString(undefined, { maximumFractionDigits: 0 })} per year
          </div>
        </div>
      </div>

      <div style={s.statRow}>
        <Stat
          label="Current spend"
          value={`$${(savings.total_current_monthly_usd ?? 0).toLocaleString()}`}
          sub="per month"
        />
        <Stat
          label="Suggested spend"
          value={`$${(savings.total_suggested_monthly_usd ?? 0).toLocaleString()}`}
          sub="per month"
        />
        <Stat
          label="Nodes adjusted"
          value={sortedRecs.filter((r) => r.action !== 'no_action').length}
          sub={`of ${sortedRecs.length} total`}
        />
        <Stat
          label="Hours / month"
          value={savings.hours_per_month ?? 730}
          sub="billing convention"
        />
      </div>

      {chartData.length > 0 && (
        <div style={s.chartBox}>
          <h4 style={s.chartTitle}>Current vs Suggested Monthly Cost</h4>
          <ResponsiveContainer width="100%" height={Math.max(220, chartData.length * 26)}>
            <BarChart
              data={chartData}
              layout="vertical"
              margin={{ top: 8, right: 24, bottom: 8, left: 96 }}
              barCategoryGap={6}
            >
              <CartesianGrid stroke="#EAEDED" strokeDasharray="3 3" />
              <XAxis
                type="number"
                tick={{ fontSize: 11, fill: '#566573' }}
                stroke="#B0BEC5"
                tickFormatter={(v) => `$${v.toFixed(0)}`}
              />
              <YAxis
                type="category"
                dataKey="node"
                tick={{ fontSize: 11, fill: '#1C2833' }}
                stroke="#B0BEC5"
                width={88}
              />
              <Tooltip formatter={(v) => `$${v.toFixed(2)}`} cursor={{ fill: '#F4F6F7' }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="current" name="Current $/mo" fill="#3498DB" />
              <Bar dataKey="suggested" name="Suggested $/mo" fill="#27AE60" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      <div style={s.tableWrap}>
        <table style={s.table}>
          <thead>
            <tr>
              <th style={s.th}>Node</th>
              <th style={s.th}>Action</th>
              <th style={s.th}>Current</th>
              <th style={s.th}>Suggested</th>
              <th style={{ ...s.th, textAlign: 'right' }}>Δ $/mo</th>
              <th style={{ ...s.th, textAlign: 'right' }}>Δ $/yr</th>
              <th style={s.th}>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {sortedRecs.map((r) => {
              const action = ACTION_COLORS[r.action] || ACTION_COLORS.no_action;
              const positive = r.monthly_delta_usd > 0;
              const negative = r.monthly_delta_usd < 0;
              return (
                <tr key={r.node_id} style={s.tr}>
                  <td style={{ ...s.td, fontFamily: 'monospace', fontSize: 12 }}>
                    {r.node_id}
                  </td>
                  <td style={s.td}>
                    <span
                      style={{
                        ...s.actionPill,
                        background: action.bg,
                        color: action.text,
                      }}
                    >
                      {r.action.replace('_', ' ')}
                    </span>
                  </td>
                  <td style={s.td}>
                    <div style={s.cell}>
                      <strong>{r.current_instance}</strong>
                      <span style={s.cellSub}>
                        ×{r.current_replicas} · ${r.current_monthly_usd}/mo
                      </span>
                    </div>
                  </td>
                  <td style={s.td}>
                    {r.suggested_instance || r.suggested_replicas !== r.current_replicas ? (
                      <div style={s.cell}>
                        <strong>{r.suggested_instance || r.current_instance}</strong>
                        <span style={s.cellSub}>
                          ×{r.suggested_replicas} · ${r.suggested_monthly_usd}/mo
                        </span>
                      </div>
                    ) : (
                      <span style={{ color: '#7B8A8B' }}>—</span>
                    )}
                  </td>
                  <td
                    style={{
                      ...s.td,
                      textAlign: 'right',
                      fontFamily: 'monospace',
                      color: negative ? '#196F3D' : positive ? '#922B21' : '#7B8A8B',
                      fontWeight: 600,
                    }}
                  >
                    {negative ? '−' : positive ? '+' : ''}$
                    {Math.abs(r.monthly_delta_usd).toFixed(2)}
                  </td>
                  <td
                    style={{
                      ...s.td,
                      textAlign: 'right',
                      fontFamily: 'monospace',
                      color: negative ? '#196F3D' : positive ? '#922B21' : '#7B8A8B',
                    }}
                  >
                    {negative ? '−' : positive ? '+' : ''}$
                    {Math.abs(r.annual_delta_usd).toFixed(0)}
                  </td>
                  <td style={{ ...s.td, fontSize: 12, color: '#566573' }}>
                    {r.rationale}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ label, value, sub }) {
  return (
    <div style={s.statCard}>
      <div style={s.statLabel}>{label}</div>
      <div style={s.statValue}>{value}</div>
      <div style={s.statSub}>{sub}</div>
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
    marginBottom: 16,
  },
  title: { margin: 0, fontSize: 16, fontWeight: 600, color: '#1B4F72' },
  subtitle: { margin: '4px 0 0 0', fontSize: 12, color: '#7B8A8B', lineHeight: 1.5 },
  savingsBadge: {
    border: '2px solid',
    borderRadius: 6,
    padding: '10px 16px',
    minWidth: 220,
    textAlign: 'center',
  },
  savingsLabel: { fontSize: 10, letterSpacing: '1.5px', opacity: 0.85 },
  savingsValue: { fontSize: 24, fontWeight: 700, marginTop: 2 },
  perMo: { fontSize: 12, fontWeight: 500, marginLeft: 4 },
  savingsAnnual: { fontSize: 11, marginTop: 2, opacity: 0.85 },
  statRow: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
    gap: 10,
    marginBottom: 18,
  },
  statCard: { background: '#F4F6F7', borderRadius: 6, padding: '10px 12px' },
  statLabel: {
    fontSize: 11,
    color: '#7B8A8B',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  statValue: { fontSize: 18, fontWeight: 700, color: '#1B4F72', marginTop: 2 },
  statSub: { fontSize: 11, color: '#7B8A8B', marginTop: 2 },
  chartBox: { marginBottom: 18 },
  chartTitle: {
    margin: '0 0 8px 0',
    fontSize: 13,
    fontWeight: 600,
    color: '#566573',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  tableWrap: { overflowX: 'auto' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: {
    textAlign: 'left',
    padding: '8px 10px',
    background: '#F4F6F7',
    color: '#566573',
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    borderBottom: '1px solid #E5E8EB',
  },
  tr: { borderBottom: '1px solid #F4F6F7' },
  td: { padding: '8px 10px', color: '#1C2833', verticalAlign: 'top' },
  cell: { display: 'flex', flexDirection: 'column' },
  cellSub: { fontSize: 11, color: '#7B8A8B', marginTop: 2 },
  actionPill: {
    display: 'inline-block',
    padding: '2px 10px',
    borderRadius: 12,
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'capitalize',
  },
};
