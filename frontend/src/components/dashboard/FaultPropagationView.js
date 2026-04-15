import { useMemo, useState } from 'react';

/**
 * Fault Propagation Visualizer (Patent Modules 109 & 110)
 *
 * Lets the user pick a "failure source" node and computes the k-hop
 * cascade neighborhood that would be impacted. The cascade risk Ψ_i
 * is approximated client-side as a weighted sum of the affected nodes'
 * centrality and risk_factor scores, normalized by the total system
 * weight. This mirrors the formulation in the patent specification
 * Section V.
 *
 * Props:
 *   topology   – { nodes: [{id, tier, ...}], edges: [...] }
 *   analysis   – analysis response (with per-node centrality, risk_factor)
 *
 * Pre-modification validator (Module 110): if Ψ_i exceeds Ψ_max
 * (default 0.55) we surface an "ABORT" recommendation.
 */

const PSI_MAX = 0.55;

function buildAdjacency(topology) {
  const nodes = topology?.nodes || [];
  const edges = topology?.edges || [];
  const adj = {};
  nodes.forEach((n) => {
    adj[n.id] = new Set();
  });
  edges.forEach((e) => {
    const src = Array.isArray(e) ? e[0] : e.source;
    const dst = Array.isArray(e) ? e[1] : e.target;
    if (adj[src]) adj[src].add(dst);
    if (adj[dst]) adj[dst].add(src); // treat as undirected for blast radius
  });
  return adj;
}

function bfsKHop(adj, source, k) {
  const distances = { [source]: 0 };
  const queue = [source];
  while (queue.length) {
    const cur = queue.shift();
    if (distances[cur] >= k) continue;
    (adj[cur] ? Array.from(adj[cur]) : []).forEach((next) => {
      if (!(next in distances)) {
        distances[next] = distances[cur] + 1;
        queue.push(next);
      }
    });
  }
  return distances;
}

export default function FaultPropagationView({ topology, analysis }) {
  const nodes = topology?.nodes || [];
  const [sourceId, setSourceId] = useState(nodes[0]?.id || '');
  const [k, setK] = useState(2);

  const adj = useMemo(() => buildAdjacency(topology), [topology]);

  const analysisByNode = useMemo(() => {
    const m = {};
    (analysis?.nodes || []).forEach((n) => {
      m[n.node_id] = n;
    });
    return m;
  }, [analysis]);

  const distances = useMemo(
    () => (sourceId ? bfsKHop(adj, sourceId, k) : {}),
    [adj, sourceId, k]
  );

  const affected = useMemo(() => {
    return Object.entries(distances)
      .map(([id, dist]) => {
        const a = analysisByNode[id] || {};
        const n = nodes.find((x) => x.id === id);
        const impact =
          (a.centrality ?? 0) * 0.6 + (a.risk_factor ?? 0) * 0.4;
        return {
          id,
          tier: n?.tier || 'supporting',
          dist,
          centrality: a.centrality ?? 0,
          riskFactor: a.risk_factor ?? 0,
          classification: a.classification || 'unknown',
          impact,
        };
      })
      .sort((x, y) => x.dist - y.dist || y.impact - x.impact);
  }, [distances, analysisByNode, nodes]);

  // Cascade risk Ψ_i: weighted sum of affected impacts, normalized by total
  const totalSystemWeight = useMemo(() => {
    return (analysis?.nodes || []).reduce(
      (sum, n) => sum + (n.centrality ?? 0) * 0.6 + (n.risk_factor ?? 0) * 0.4,
      0
    );
  }, [analysis]);
  const cascadeWeight = affected.reduce((sum, a) => sum + a.impact, 0);
  const psi = totalSystemWeight > 0 ? cascadeWeight / totalSystemWeight : 0;
  const overLimit = psi > PSI_MAX;

  const distColor = (d) =>
    d === 0 ? '#E74C3C' : d === 1 ? '#F39C12' : d === 2 ? '#3498DB' : '#85C1E2';

  return (
    <div style={styles.wrap}>
      <div style={styles.headerRow}>
        <div>
          <h3 style={styles.title}>Fault Propagation &amp; Cascade Risk</h3>
          <p style={styles.subtitle}>
            Patent Modules 109 (Propagation) &amp; 110 (Pre-Modification
            Validator). Pick a failure source and observe the k-hop blast
            radius before allowing any modification.
          </p>
        </div>
      </div>

      <div style={styles.controls}>
        <label style={styles.label}>
          Failure source
          <select
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            style={styles.select}
          >
            {nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.id}
              </option>
            ))}
          </select>
        </label>
        <label style={styles.label}>
          k-hop depth: <strong>{k}</strong>
          <input
            type="range"
            min={1}
            max={4}
            value={k}
            onChange={(e) => setK(parseInt(e.target.value, 10))}
            style={styles.slider}
          />
        </label>

        <div
          style={{
            ...styles.psiGauge,
            background: overLimit ? '#FDEDEC' : '#EAFAF1',
            borderColor: overLimit ? '#E74C3C' : '#27AE60',
            color: overLimit ? '#922B21' : '#196F3D',
          }}
        >
          <div style={styles.psiLabel}>Ψ (cascade risk)</div>
          <div style={styles.psiValue}>{psi.toFixed(3)}</div>
          <div style={styles.psiDetail}>
            limit Ψ_max = {PSI_MAX}
            <br />
            {overLimit ? 'ABORT recommended' : 'within tolerance'}
          </div>
        </div>
      </div>

      <div style={styles.affectedHeader}>
        Affected nodes ({affected.length}/{nodes.length})
      </div>

      <div style={styles.tableHead}>
        <span style={{ ...styles.th, flex: '0 0 60px' }}>Hop</span>
        <span style={{ ...styles.th, flex: '0 0 160px' }}>Node</span>
        <span style={{ ...styles.th, flex: '0 0 110px' }}>Tier</span>
        <span style={{ ...styles.th, flex: '0 0 100px' }}>Centrality</span>
        <span style={{ ...styles.th, flex: '0 0 100px' }}>Risk R_i</span>
        <span style={{ ...styles.th, flex: 1 }}>Impact</span>
      </div>
      <div style={styles.tableBody}>
        {affected.map((n) => (
          <div key={n.id} style={styles.row}>
            <span style={{ ...styles.cell, flex: '0 0 60px' }}>
              <span
                style={{
                  ...styles.hopBadge,
                  background: distColor(n.dist),
                }}
              >
                {n.dist}
              </span>
            </span>
            <span style={{ ...styles.cell, flex: '0 0 160px' }}>
              <strong>{n.id}</strong>
            </span>
            <span
              style={{ ...styles.cell, flex: '0 0 110px', textTransform: 'capitalize' }}
            >
              {n.tier}
            </span>
            <span style={{ ...styles.cell, flex: '0 0 100px' }}>
              {n.centrality.toFixed(3)}
            </span>
            <span style={{ ...styles.cell, flex: '0 0 100px' }}>
              {n.riskFactor.toFixed(3)}
            </span>
            <span style={{ ...styles.cell, flex: 1 }}>
              <div style={styles.impactBarBg}>
                <div
                  style={{
                    ...styles.impactBarFill,
                    width: `${Math.min(100, n.impact * 200).toFixed(0)}%`,
                  }}
                />
              </div>
            </span>
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
  headerRow: { marginBottom: 14 },
  title: { margin: 0, fontSize: 16, fontWeight: 600, color: '#1B4F72' },
  subtitle: { margin: '4px 0 0 0', fontSize: 12, color: '#7B8A8B', lineHeight: 1.5 },
  controls: {
    display: 'flex',
    gap: 20,
    alignItems: 'center',
    flexWrap: 'wrap',
    marginBottom: 16,
    padding: '12px 16px',
    background: '#F8F9FA',
    borderRadius: 6,
  },
  label: {
    display: 'flex',
    flexDirection: 'column',
    fontSize: 12,
    color: '#34495E',
    minWidth: 180,
    gap: 6,
  },
  select: {
    padding: '6px 8px',
    fontSize: 13,
    border: '1px solid #CFD8DC',
    borderRadius: 4,
    background: 'white',
  },
  slider: { width: '100%' },
  psiGauge: {
    border: '2px solid',
    borderRadius: 6,
    padding: '8px 14px',
    minWidth: 180,
    textAlign: 'center',
    marginLeft: 'auto',
  },
  psiLabel: { fontSize: 10, letterSpacing: '1.5px', opacity: 0.8 },
  psiValue: { fontSize: 22, fontWeight: 700, marginTop: 2 },
  psiDetail: { fontSize: 11, marginTop: 4, opacity: 0.85, lineHeight: 1.4 },
  affectedHeader: {
    fontSize: 12,
    color: '#7B8A8B',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    marginBottom: 6,
  },
  tableHead: {
    display: 'flex',
    padding: '8px 0',
    borderBottom: '1px solid #EAEDED',
    fontSize: 11,
    color: '#7B8A8B',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  th: { padding: '0 8px' },
  tableBody: { maxHeight: 320, overflowY: 'auto' },
  row: {
    display: 'flex',
    alignItems: 'center',
    padding: '6px 0',
    borderBottom: '1px solid #F4F6F7',
    fontSize: 13,
    color: '#1C2833',
  },
  cell: { padding: '0 8px' },
  hopBadge: {
    display: 'inline-block',
    width: 26,
    height: 22,
    lineHeight: '22px',
    textAlign: 'center',
    color: 'white',
    fontWeight: 700,
    borderRadius: 4,
    fontSize: 12,
  },
  impactBarBg: {
    background: '#EAEDED',
    height: 8,
    borderRadius: 4,
    width: '90%',
    overflow: 'hidden',
  },
  impactBarFill: {
    height: '100%',
    background: 'linear-gradient(90deg, #3498DB, #E67E22 60%, #E74C3C)',
  },
};
