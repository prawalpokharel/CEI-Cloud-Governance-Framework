import React, { useMemo, useState } from 'react';

/**
 * Quick Wins — surfaces the top actionable optimization opportunities
 * from a CEI analysis, plus a read-only k-hop simulation modal backed by
 * Patent Module 110 (pre-modification validator).
 *
 * The "Blocked by governance" section is a first-class part of the view
 * because it demonstrates the patent's Module 104 + 110 working together:
 * savings that would otherwise be pursued are correctly withheld when
 * they violate policy or exceed the safety threshold.
 *
 * Light theme to match the rest of the dashboard.
 */
export default function QuickWins({ results }) {
  const [selectedNode, setSelectedNode] = useState(null);

  const { actionable, blocked, totals } = useMemo(
    () => partitionOpportunities(results),
    [results],
  );

  if (!results) {
    return (
      <div style={s.empty}>
        <h2 style={s.emptyTitle}>Quick Wins</h2>
        <p style={s.emptyText}>
          Run an analysis from the <strong>Run Analysis</strong> tab to see
          the top optimization opportunities in your topology.
        </p>
      </div>
    );
  }

  return (
    <div style={s.wrap}>
      <header style={s.header}>
        <div>
          <h2 style={s.title}>Quick Wins</h2>
          <p style={s.subtitle}>
            Top optimization opportunities, ranked by projected monthly
            savings. Every opportunity is validated against governance
            constraints and k-hop blast radius before it is offered.
          </p>
        </div>
        <div style={s.headerStats}>
          <Stat
            label="Actionable opportunities"
            value={totals.actionableCount}
            color="#16A085"
          />
          <Stat
            label="Projected monthly savings"
            value={formatUsd(totals.actionableSavings)}
            color="#1B4F72"
          />
          <Stat
            label="Blocked by governance"
            value={totals.blockedCount}
            color="#B7950B"
          />
        </div>
      </header>

      {/* ---------- Actionable ---------- */}
      <section style={s.section}>
        <h3 style={s.sectionTitle}>
          Actionable ({actionable.length})
        </h3>
        {actionable.length === 0 ? (
          <p style={s.emptyText}>
            No actionable savings in the current topology. Every node is
            either mission-critical, over safety threshold, or already
            rightsized.
          </p>
        ) : (
          <div style={s.list}>
            {actionable.map((n) => (
              <OpportunityCard
                key={n.node_id}
                node={n}
                kind="actionable"
                onSimulate={() => setSelectedNode(n)}
              />
            ))}
          </div>
        )}
      </section>

      {/* ---------- Blocked ---------- */}
      {blocked.length > 0 && (
        <section style={s.section}>
          <h3 style={s.sectionTitle}>
            Blocked by governance ({blocked.length})
          </h3>
          <p style={s.sectionHint}>
            These would be cost-saving actions, but the pre-modification
            validator (Patent Module 110) blocked them because they
            violated a governance constraint, exceeded the k-hop safety
            threshold, or would propagate cascade risk. Shown here for
            transparency.
          </p>
          <div style={s.list}>
            {blocked.map((n) => (
              <OpportunityCard
                key={n.node_id}
                node={n}
                kind="blocked"
                onSimulate={() => setSelectedNode(n)}
              />
            ))}
          </div>
        </section>
      )}

      {/* ---------- Disclosure ---------- */}
      <footer style={s.disclosure}>
        <strong>Methodology.</strong> Projected savings apply the patent&apos;s
        §V.B factors: <strong>27%</strong> for consolidation and{' '}
        <strong>15%</strong> for rightsizing of nodes with CEI&nbsp;&lt;&nbsp;0.70
        and governance risk&nbsp;&lt;&nbsp;0.70. Monthly cost is taken from the
        telemetry payload (cloud-provider discovery, scenario dataset, or
        uploaded template). Savings shown are projections on the current
        analyzed topology, not measured production outcomes.
      </footer>

      {selectedNode && (
        <SimulationModal
          node={selectedNode}
          onClose={() => setSelectedNode(null)}
        />
      )}
    </div>
  );
}

/* ---------- helpers ---------- */

function partitionOpportunities(results) {
  if (!results || !Array.isArray(results.nodes)) {
    return {
      actionable: [],
      blocked: [],
      totals: { actionableCount: 0, actionableSavings: 0, blockedCount: 0 },
    };
  }
  const actionable = results.nodes
    .filter(
      (n) =>
        (Number(n.estimated_savings) || 0) > 0 &&
        n.is_safe !== false &&
        !n.blocked_reason,
    )
    .sort(
      (a, b) =>
        (Number(b.estimated_savings) || 0) -
        (Number(a.estimated_savings) || 0),
    )
    .slice(0, 5);

  const blocked = results.nodes
    .filter((n) => n.blocked_reason)
    .slice(0, 5);

  const actionableSavings = actionable.reduce(
    (s, n) => s + (Number(n.estimated_savings) || 0),
    0,
  );

  return {
    actionable,
    blocked,
    totals: {
      actionableCount: actionable.length,
      actionableSavings,
      blockedCount: results.nodes.filter((n) => n.blocked_reason).length,
    },
  };
}

function actionLabel(node) {
  const t = node.action_type || node.recommendation || 'review';
  const map = {
    consolidate: 'Consolidate',
    rightsize: 'Rightsize',
    scale_up: 'Scale up',
    monitor: 'Monitor',
    no_action: 'No action',
  };
  return map[t] || t.replace(/_/g, ' ');
}

function actionColor(node) {
  const t = node.action_type || node.recommendation;
  if (t === 'consolidate') return { bg: '#D5F5E3', fg: '#0E6655' };
  if (t === 'rightsize') return { bg: '#D6EAF8', fg: '#1B4F72' };
  if (t === 'scale_up') return { bg: '#FADBD8', fg: '#922B21' };
  if (t === 'monitor') return { bg: '#FEF9E7', fg: '#7D6608' };
  return { bg: '#EAEDED', fg: '#566573' };
}

function formatUsd(n) {
  const v = Number(n) || 0;
  if (v >= 1000) return `$${(v / 1000).toFixed(1)}k/mo`;
  return `$${v.toFixed(0)}/mo`;
}

/* ---------- subcomponents ---------- */

function Stat({ label, value, color }) {
  return (
    <div style={s.stat}>
      <div style={{ ...s.statValue, color }}>{value}</div>
      <div style={s.statLabel}>{label}</div>
    </div>
  );
}

function OpportunityCard({ node, kind, onSimulate }) {
  const tag = actionColor(node);
  const monthlyCost = Number(node.monthly_cost) || 0;
  const savings = Number(node.estimated_savings) || 0;
  const borderColor = kind === 'blocked' ? '#B7950B' : '#16A085';

  return (
    <article
      style={{
        ...s.card,
        borderLeft: `4px solid ${borderColor}`,
      }}
    >
      <div style={s.cardTop}>
        <div style={s.cardIdent}>
          <div style={s.nodeId}>{node.node_id}</div>
          <div style={s.meta}>
            <span style={{ ...s.tag, background: tag.bg, color: tag.fg }}>
              {actionLabel(node)}
            </span>
            <span style={s.metaItem}>
              CEI <strong>{Number(node.cei_score).toFixed(3)}</strong>
            </span>
            <span style={s.metaItem}>
              Risk <strong>{Number(node.risk_factor).toFixed(3)}</strong>
            </span>
          </div>
        </div>
        <div style={s.cardRight}>
          {kind === 'actionable' ? (
            <>
              <div style={s.savings}>{formatUsd(savings)}</div>
              <div style={s.savingsHint}>
                from ${monthlyCost.toFixed(0)}/mo cost
              </div>
            </>
          ) : (
            <div style={s.blockedTag}>Blocked</div>
          )}
        </div>
      </div>

      {node.action_details && (
        <p style={s.cardBody}>{node.action_details}</p>
      )}

      {kind === 'blocked' && node.blocked_reason && (
        <div style={s.blockedReason}>
          <strong>Why blocked:</strong> {node.blocked_reason}
        </div>
      )}

      <div style={s.cardFooter}>
        <button style={s.simulateBtn} onClick={onSimulate}>
          Simulate modification
        </button>
      </div>
    </article>
  );
}

function SimulationModal({ node, onClose }) {
  const v = node.validation || {};
  const khop = v.k_hop_impact || {};
  const governance = v.governance_check || {};
  const cascadeRisk = Number(v.cascade_risk || 0);
  const safetyThreshold = Number(v.safety_threshold || 0.7);
  const impact = Number(khop.cumulative_centrality_change || 0);

  const isSafe = node.is_safe && !node.blocked_reason;

  return (
    <div style={s.backdrop} onClick={onClose}>
      <div style={s.modal} onClick={(e) => e.stopPropagation()}>
        <div style={s.modalHeader}>
          <div>
            <div style={s.modalEyebrow}>
              Patent Module 110 · Pre-Modification Validation
            </div>
            <h3 style={s.modalTitle}>
              Simulate {actionLabel(node).toLowerCase()} on{' '}
              <code style={s.code}>{node.node_id}</code>
            </h3>
          </div>
          <button style={s.closeBtn} onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div
          style={{
            ...s.verdict,
            background: isSafe ? '#D5F5E3' : '#FADBD8',
            color: isSafe ? '#0E6655' : '#922B21',
          }}
        >
          {isSafe ? '✓ SAFE — modification would be permitted' : '✗ BLOCKED — modification prevented'}
          {node.blocked_reason && (
            <div style={s.verdictReason}>{node.blocked_reason}</div>
          )}
        </div>

        <dl style={s.kvGrid}>
          <Row
            label="Cumulative centrality change (k-hop)"
            value={impact.toFixed(4)}
            barValue={impact}
            barMax={safetyThreshold}
            inverse
          />
          <Row
            label="Safety threshold"
            value={safetyThreshold.toFixed(2)}
          />
          <Row
            label="Cascade risk"
            value={cascadeRisk.toFixed(4)}
            barValue={cascadeRisk}
            barMax={safetyThreshold}
            inverse
          />
          <Row
            label="Governance compliance"
            value={governance.compliant ? '✓ compliant' : '✗ violation'}
            valueColor={governance.compliant ? '#0E6655' : '#922B21'}
          />
          {Array.isArray(khop.k_hop_nodes) && khop.k_hop_nodes.length > 0 && (
            <Row
              label={`K-hop neighborhood (${khop.k_hop_nodes.length} nodes)`}
              value={khop.k_hop_nodes.join(', ')}
              wrap
            />
          )}
          {governance.violations &&
            Array.isArray(governance.violations) &&
            governance.violations.length > 0 && (
              <Row
                label="Governance violations"
                value={governance.violations.join('; ')}
                valueColor="#922B21"
                wrap
              />
            )}
        </dl>

        <div style={s.modalFooter}>
          <div style={s.footerNote}>
            This is a read-only simulation. No infrastructure modification
            is executed. Per patent Module 112, any permitted execution
            would be paired with rollback state for atomic revert.
          </div>
          <button style={s.closeBtnPrimary} onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, valueColor, barValue, barMax, inverse, wrap }) {
  const hasBar =
    typeof barValue === 'number' && typeof barMax === 'number' && barMax > 0;
  const pct = hasBar
    ? Math.max(0, Math.min(100, (barValue / barMax) * 100))
    : 0;
  const barColor = inverse
    ? pct > 80
      ? '#E74C3C'
      : pct > 50
      ? '#E67E22'
      : '#16A085'
    : '#3498DB';

  return (
    <div style={s.kvRow}>
      <dt style={s.kvLabel}>{label}</dt>
      <dd style={s.kvValueBlock}>
        <span style={{ ...s.kvValue, color: valueColor, whiteSpace: wrap ? 'normal' : 'nowrap' }}>
          {value}
        </span>
        {hasBar && (
          <div style={s.barWrap}>
            <div
              style={{
                ...s.barFill,
                width: `${pct}%`,
                background: barColor,
              }}
            />
          </div>
        )}
      </dd>
    </div>
  );
}

/* ---------- styles ---------- */

const s = {
  wrap: { display: 'flex', flexDirection: 'column', gap: 24 },
  header: {
    background: 'white',
    borderRadius: 8,
    padding: 24,
    boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    flexWrap: 'wrap',
    gap: 20,
  },
  title: {
    margin: 0,
    fontSize: 22,
    fontWeight: 700,
    color: '#1B4F72',
    letterSpacing: '-0.3px',
  },
  subtitle: {
    margin: '6px 0 0 0',
    fontSize: 13,
    color: '#566573',
    lineHeight: 1.5,
    maxWidth: 640,
  },
  headerStats: {
    display: 'flex',
    gap: 24,
    alignItems: 'flex-start',
  },
  stat: { textAlign: 'right' },
  statValue: { fontSize: 24, fontWeight: 700, letterSpacing: '-0.5px' },
  statLabel: {
    fontSize: 11,
    color: '#7B8A8B',
    letterSpacing: '0.5px',
    textTransform: 'uppercase',
    marginTop: 2,
  },
  section: {
    background: 'white',
    borderRadius: 8,
    padding: 20,
    boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
  },
  sectionTitle: {
    margin: '0 0 12px 0',
    fontSize: 15,
    fontWeight: 600,
    color: '#1B4F72',
    letterSpacing: '0.3px',
    textTransform: 'uppercase',
  },
  sectionHint: {
    margin: '0 0 14px 0',
    fontSize: 13,
    color: '#566573',
    lineHeight: 1.55,
  },
  list: { display: 'flex', flexDirection: 'column', gap: 12 },
  card: {
    background: '#FAFBFC',
    border: '1px solid #E8EDF0',
    borderRadius: 6,
    padding: 16,
  },
  cardTop: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 16,
    flexWrap: 'wrap',
  },
  cardIdent: { flex: 1, minWidth: 220 },
  nodeId: {
    fontSize: 15,
    fontWeight: 600,
    color: '#1C2833',
    fontFamily: "'SFMono-Regular', Consolas, monospace",
    marginBottom: 6,
  },
  meta: { display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' },
  tag: {
    fontSize: 11,
    fontWeight: 600,
    padding: '3px 8px',
    borderRadius: 10,
    letterSpacing: '0.3px',
    textTransform: 'uppercase',
  },
  metaItem: { fontSize: 12, color: '#7B8A8B' },
  cardRight: { textAlign: 'right' },
  savings: {
    fontSize: 20,
    fontWeight: 700,
    color: '#16A085',
    letterSpacing: '-0.3px',
  },
  savingsHint: { fontSize: 11, color: '#7B8A8B', marginTop: 2 },
  blockedTag: {
    fontSize: 11,
    fontWeight: 600,
    padding: '4px 10px',
    background: '#FDEBD0',
    color: '#7D6608',
    borderRadius: 10,
    letterSpacing: '0.3px',
    textTransform: 'uppercase',
  },
  cardBody: {
    margin: '12px 0 0 0',
    fontSize: 13,
    color: '#566573',
    lineHeight: 1.5,
  },
  blockedReason: {
    marginTop: 10,
    padding: '8px 12px',
    background: '#FEF9E7',
    borderLeft: '3px solid #B7950B',
    borderRadius: 3,
    fontSize: 12,
    color: '#7D6608',
    lineHeight: 1.5,
  },
  cardFooter: {
    marginTop: 14,
    paddingTop: 12,
    borderTop: '1px solid #EAEDED',
    display: 'flex',
    justifyContent: 'flex-end',
  },
  simulateBtn: {
    background: '#1B4F72',
    color: 'white',
    border: 'none',
    padding: '7px 14px',
    borderRadius: 4,
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    letterSpacing: '0.3px',
  },
  disclosure: {
    fontSize: 12,
    color: '#7B8A8B',
    lineHeight: 1.55,
    padding: '12px 16px',
    borderLeft: '3px solid #AED6F1',
    background: '#F4F8FB',
    borderRadius: 3,
  },
  empty: {
    background: 'white',
    borderRadius: 8,
    padding: 48,
    boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
    textAlign: 'center',
  },
  emptyTitle: {
    margin: '0 0 12px 0',
    color: '#1B4F72',
    fontSize: 22,
    fontWeight: 700,
  },
  emptyText: { margin: 0, color: '#7B8A8B', fontSize: 14, lineHeight: 1.6 },

  /* ---------- modal ---------- */
  backdrop: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(28,40,51,0.45)',
    zIndex: 1000,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 20,
    overflowY: 'auto',
  },
  modal: {
    background: 'white',
    borderRadius: 10,
    maxWidth: 620,
    width: '100%',
    padding: 24,
    boxShadow: '0 20px 50px rgba(0,0,0,0.25)',
  },
  modalHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 16,
    marginBottom: 16,
  },
  modalEyebrow: {
    fontSize: 10,
    color: '#2874A6',
    fontWeight: 600,
    letterSpacing: '1.2px',
    textTransform: 'uppercase',
    marginBottom: 6,
  },
  modalTitle: {
    margin: 0,
    fontSize: 18,
    fontWeight: 600,
    color: '#1B4F72',
    lineHeight: 1.3,
  },
  code: {
    fontFamily: "'SFMono-Regular', Consolas, monospace",
    fontSize: 15,
    padding: '1px 6px',
    background: '#F4F6F6',
    borderRadius: 3,
  },
  closeBtn: {
    background: 'transparent',
    border: 'none',
    fontSize: 24,
    cursor: 'pointer',
    color: '#7B8A8B',
    lineHeight: 1,
  },
  verdict: {
    padding: '12px 16px',
    borderRadius: 4,
    fontWeight: 600,
    fontSize: 14,
    marginBottom: 18,
  },
  verdictReason: {
    fontSize: 12,
    fontWeight: 400,
    marginTop: 4,
    opacity: 0.85,
  },
  kvGrid: { margin: '0 0 18px 0', padding: 0 },
  kvRow: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 16,
    padding: '10px 0',
    borderBottom: '1px solid #EAEDED',
    alignItems: 'flex-start',
  },
  kvLabel: {
    margin: 0,
    fontSize: 12,
    color: '#566573',
    flexShrink: 0,
    fontWeight: 500,
  },
  kvValueBlock: { margin: 0, textAlign: 'right', flex: 1 },
  kvValue: {
    fontSize: 13,
    fontWeight: 600,
    color: '#1C2833',
    fontFamily: "'SFMono-Regular', Consolas, monospace",
  },
  barWrap: {
    marginTop: 4,
    height: 4,
    background: '#EAEDED',
    borderRadius: 2,
    width: '100%',
    overflow: 'hidden',
  },
  barFill: { height: '100%', borderRadius: 2, transition: 'width 0.2s' },
  modalFooter: {
    paddingTop: 16,
    borderTop: '1px solid #EAEDED',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 16,
  },
  footerNote: {
    fontSize: 11,
    color: '#7B8A8B',
    lineHeight: 1.5,
    flex: 1,
  },
  closeBtnPrimary: {
    background: '#1B4F72',
    color: 'white',
    border: 'none',
    padding: '8px 18px',
    borderRadius: 4,
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
  },
};
