import { useEffect, useMemo, useState } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import ClusterTopologyMap from '../../components/app/ClusterTopologyMap';
import DriftPanel from '../../components/app/DriftPanel';
import { api, getToken, isSandbox } from '../../lib/appApi';

// Beyond this a force-directed layout is a hairball: nodes overlap, labels
// collide, and the simulation is slow. Measured against a synthetic
// 1000-workload cluster.
const MAX_GRAPH_NODES = 150;

/**
 * Live cluster view: dependency map plus the workload table behind it.
 *
 * CEI colours and sizes the map. The centrality mode is switchable because
 * "central" has two defensible readings that rank workloads very differently
 * -- see services/live_cei.CentralityMode.
 *
 * The table shows requested-vs-used headroom alongside the score, which is
 * the Phase 2 waste signal and is available as soon as metrics-server is.
 */
export default function ClusterView() {
  const router = useRouter();
  const { clusterId } = router.query;
  const [data, setData] = useState(null);
  const [cei, setCei] = useState(null);
  const [mode, setMode] = useState('blast_radius');
  const [history, setHistory] = useState(null);
  const [cost, setCost] = useState(null);
  const [health, setHealth] = useState(null);
  const [vulns, setVulns] = useState(null);
  const [drift, setDrift] = useState(null);
  const [error, setError] = useState(null);
  const [sortBy, setSortBy] = useState('cei');
  const [namespace, setNamespace] = useState('all');

  useEffect(() => {
    if (!clusterId) return undefined;
    if (!getToken() && !isSandbox(clusterId)) {
      router.replace('/app');
      return undefined;
    }
    let active = true;
    const load = () => {
      api
        .topology(clusterId)
        .then((d) => active && setData(d))
        .catch((e) => active && setError(e.message));
      api
        .history(clusterId)
        .then((h) => active && setHistory(h))
        .catch(() => {});
      api
        .cost(clusterId)
        .then((c) => active && setCost(c))
        .catch(() => active && setCost(null));
      api
        .health(clusterId)
        .then((h) => active && setHealth(h))
        .catch(() => active && setHealth(null));
      api
        .vulnerabilities(clusterId)
        .then((v) => active && setVulns(v))
        .catch(() => active && setVulns(null));
      api
        .drift(clusterId)
        .then((d) => active && setDrift(d))
        .catch(() => active && setDrift(null));
      api
        .cei(clusterId, mode)
        // A cluster with no snapshot yet returns 409; that is a normal
        // early state, not an error worth surfacing.
        .then((c) => active && setCei(c))
        .catch(() => active && setCei(null));
    };
    load();
    const timer = setInterval(load, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [clusterId, router, mode]);

  const ceiByWorkload = useMemo(() => {
    const map = {};
    (cei?.nodes || []).forEach((n) => {
      map[n.node_id] = n;
    });
    return map;
  }, [cei]);

  const namespaces = useMemo(() => {
    const set = new Set((data?.workloads || []).map((w) => w.namespace));
    return ['all', ...Array.from(set).sort()];
  }, [data]);

  // The map component expects {nodes:[{id}], edges:[{source,target,weight}]}.
  //
  // Capped at MAX_GRAPH_NODES. A force-directed layout past a few hundred
  // nodes is an unreadable hairball no matter how fast it renders, so the
  // highest-CEI workloads are kept and the rest dropped — with the count
  // stated, because a silently truncated graph looks like a complete one.
  const topology = useMemo(() => {
    if (!data?.workloads) return null;

    let visible = data.workloads;
    if (namespace !== 'all') {
      visible = visible.filter((w) => w.namespace === namespace);
    }

    const scored = visible
      .map((w) => ({ w, score: ceiByWorkload[w.key]?.cei_score ?? 0 }))
      .sort((a, b) => b.score - a.score);
    const kept = scored.slice(0, MAX_GRAPH_NODES).map((x) => x.w);
    const keptKeys = new Set(kept.map((w) => w.key));

    return {
      truncatedFrom: visible.length > kept.length ? visible.length : null,
      nodes: kept.map((w) => ({ id: w.key, label: w.name, tier: w.namespace })),
      edges: (data.edges || [])
        .filter((e) => keptKeys.has(e.source) && keptKeys.has(e.target))
        .map((e) => ({
          source: e.source,
          target: e.target,
          weight: e.confidence ?? 1,
        })),
    };
  }, [data, namespace, ceiByWorkload]);

  const rows = useMemo(() => {
    const list = (data?.workloads || []).map((w) => {
      const req = w.cpu_cores_requested;
      const used = w.cpu_cores_used;
      const headroom =
        req && used !== null && used !== undefined ? 1 - used / req : null;
      return { ...w, headroom, cei: ceiByWorkload[w.key] || null };
    });
    if (sortBy === 'cei') {
      list.sort((a, b) => (b.cei?.cei_score ?? -1) - (a.cei?.cei_score ?? -1));
    } else if (sortBy === 'headroom') {
      list.sort((a, b) => (b.headroom ?? -1) - (a.headroom ?? -1));
    } else {
      list.sort((a, b) => a.key.localeCompare(b.key));
    }
    return list;
  }, [data, sortBy, ceiByWorkload]);

  if (error) {
    return (
      <Shell>
        <div style={s.error}>{error}</div>
        <Link href="/app" style={s.back}>← All clusters</Link>
      </Shell>
    );
  }
  if (!data) return <Shell><p style={s.muted}>Loading cluster…</p></Shell>;

  if (!data.cluster.connected) {
    return (
      <Shell>
        <Link href="/app" style={s.back}>← All clusters</Link>
        <h2 style={s.h2}>{data.cluster.name}</h2>
        <div style={s.waiting}>
          <strong>Waiting for the agent.</strong>
          <p style={s.muted}>{data.message}</p>
        </div>
      </Shell>
    );
  }

  const c = data.cluster;

  return (
    <Shell>
      <Link href="/app" style={s.back}>← All clusters</Link>
      {isSandbox(clusterId) && (
        <div style={s.sandboxBanner}>
          <strong>Sample data.</strong> A synthetic 26-workload cluster, run
          through the same analysis a connected cluster uses — nothing here is
          a mock-up. Install the agent to see your own.
        </div>
      )}
      <div style={s.titleRow}>
        <h2 style={s.h2}>{c.name}</h2>
        <div style={s.meta}>
          {c.provider !== 'unknown' && <span>{c.provider.toUpperCase()}</span>}
          {c.k8s_version && <span>k8s {c.k8s_version}</span>}
          <span>seq {data.seq}</span>
          <span>
            updated {new Date(data.captured_at).toLocaleTimeString()}
          </span>
        </div>
      </div>

      {!c.metrics_available && (
        <div style={s.notice}>
          <strong>Usage data unavailable.</strong>{' '}
          {c.metrics_reason ||
            'metrics-server was not reachable, so only requested capacity is known.'}
        </div>
      )}

      {history && !history.entropy_ready && (
        <div style={s.notice}>
          <strong>CEI entropy is warming up.</strong> {history.snapshot_count} of{' '}
          {history.entropy_samples_required} observations collected. Workload
          variability cannot be measured until enough history exists, so
          scores are withheld rather than estimated from too little data.
        </div>
      )}

      {cost && (
        <div style={s.wastePanel}>
          <div style={s.wasteHead}>
            <div>
              <div style={s.wasteLabel}>Reserved but unused</div>
              <div style={s.wasteValue}>
                ${cost.summary.wasted_monthly_usd.toLocaleString()}
                <span style={s.wasteUnit}>/month</span>
              </div>
              <div style={s.wasteSub}>
                ${cost.summary.wasted_annual_usd.toLocaleString()}/year across{' '}
                {cost.summary.workloads_over_provisioned} workload(s)
              </div>
            </div>
            <div style={s.wasteBreakdown}>
              <Small label="cluster" value={`$${cost.summary.cluster_monthly_usd.toLocaleString()}/mo`} />
              <Small label="allocated" value={`$${cost.summary.allocated_monthly_usd.toLocaleString()}/mo`} />
              <Small
                label="unallocated"
                value={`$${cost.summary.unallocated_monthly_usd.toLocaleString()}/mo`}
                hint="Node capacity nothing reserves. Shrink the node pool, not requests."
              />
            </div>
          </div>
          <p style={s.wasteNote}>{cost.basis.note}</p>
          {cost.opportunities.length > 0 && (
            <table style={s.table}>
              <thead>
                <tr>
                  <th style={s.th}>Opportunity</th>
                  <th style={s.thNum}>CPU used</th>
                  <th style={s.thNum}>Reclaimable</th>
                </tr>
              </thead>
              <tbody>
                {cost.opportunities.slice(0, 5).map((o) => (
                  <tr key={o.workload_key}>
                    <td style={s.td}>
                      <strong>{o.name}</strong>
                      <div style={s.oppDetail}>{o.recommendation}</div>
                    </td>
                    <td style={s.tdNum}>
                      {o.cpu_utilization !== null
                        ? `${(o.cpu_utilization * 100).toFixed(0)}%`
                        : '–'}
                    </td>
                    <td style={{ ...s.tdNum, fontWeight: 700, color: '#196F3D' }}>
                      ${o.wasted_monthly_usd.toFixed(0)}/mo
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {vulns && vulns.summary.total_vulnerabilities > 0 && (
        <div style={s.panel}>
          <div style={s.panelTitle}>
            Vulnerabilities — ranked by what they put at risk
          </div>
          <p style={s.vulnHeadline}>{vulns.summary.headline}</p>
          <div style={s.vulnCounts}>
            {['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((sev) => (
              <span key={sev} style={s.vulnCount}>
                <strong>{vulns.summary.by_severity?.[sev] ?? 0}</strong> {sev.toLowerCase()}
              </span>
            ))}
            <span style={s.vulnCount}>
              {vulns.summary.images_scanned} images scanned
              {vulns.summary.images_failed > 0 &&
                ` · ${vulns.summary.images_failed} failed`}
            </span>
          </div>
          {vulns.top_risks.map((r) => (
            <div key={`${r.vulnerability_id}-${r.image_reference}`} style={s.finding}>
              <span
                style={{
                  ...s.sev,
                  background: r.severity === 'CRITICAL' ? '#FDEDEC' : '#FEF9E7',
                  color: r.severity === 'CRITICAL' ? '#922B21' : '#7D6608',
                }}
              >
                {r.severity}
              </span>
              <div style={{ flex: 1 }}>
                <div style={s.findingTitle}>
                  {r.vulnerability_id} in {r.package}
                </div>
                <div style={s.findingDetail}>{r.rationale}</div>
                <div style={s.vulnMeta}>
                  {r.image_reference}
                  {r.fixed_version && ` · fix: ${r.fixed_version}`}
                </div>
              </div>
              <span style={s.findingCei}>{r.priority_score.toFixed(1)}</span>
            </div>
          ))}
          {vulns.base_image_recommendations?.length > 0 && (
            <div style={s.rebase}>
              <strong>Base image:</strong>{' '}
              {vulns.base_image_recommendations[0].recommendation}
            </div>
          )}
        </div>
      )}

      <DriftPanel drift={drift} styles={s} />

      {health && health.summary.total > 0 && (
        <div style={s.panel}>
          <div style={s.panelTitle}>
            Health — {health.summary.critical} critical, {health.summary.warning} warning
            {health.summary.ranked_by_cei && ' · ranked by blast radius'}
          </div>
          {health.findings.slice(0, 6).map((f, i) => (
            <div key={i} style={s.finding}>
              <span
                style={{
                  ...s.sev,
                  background: f.severity === 'critical' ? '#FDEDEC' : '#FEF9E7',
                  color: f.severity === 'critical' ? '#922B21' : '#7D6608',
                }}
              >
                {f.severity}
              </span>
              <div style={{ flex: 1 }}>
                <div style={s.findingTitle}>{f.title}</div>
                <div style={s.findingDetail}>{f.detail}</div>
              </div>
              {f.cei_score !== null && f.cei_score !== undefined && (
                <span style={s.findingCei}>CEI {f.cei_score.toFixed(2)}</span>
              )}
            </div>
          ))}
        </div>
      )}

      <div style={s.summaryRow}>
        <Metric label="workloads" value={data.summary?.workloads} />
        <Metric label="services" value={data.summary?.services} />
        <Metric label="pods" value={data.summary?.pods} />
        <Metric label="dependencies" value={data.summary?.edges?.total} />
      </div>

      <div style={s.panel}>
        <div style={s.panelHeader}>
          <div style={s.panelTitle}>Dependency topology</div>
          <select
            style={{ ...s.select, marginRight: 8 }}
            value={namespace}
            onChange={(e) => setNamespace(e.target.value)}
          >
            {namespaces.map((ns) => (
              <option key={ns} value={ns}>
                {ns === 'all' ? 'All namespaces' : ns}
              </option>
            ))}
          </select>
          <select
            style={s.select}
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            title="What 'central' means. Blast radius asks what breaks if this fails; structural ranks hubs and entrypoints."
          >
            <option value="blast_radius">Rank by blast radius</option>
            <option value="structural">Rank by structural position</option>
          </select>
        </div>
        {topology?.truncatedFrom && (
          <p style={s.truncated}>
            Showing the {MAX_GRAPH_NODES} highest-CEI workloads of{' '}
            {topology.truncatedFrom}. Filter by namespace to see the rest — a
            force-directed graph is unreadable much beyond this.
          </p>
        )}
        {cei && (
          <p style={s.ceiNote}>
            α {cei.weights.alpha} · β {cei.weights.beta} · γ {cei.weights.gamma}
            {cei.entropy && !cei.entropy.ready && (
              <>
                {' '}— entropy withheld ({cei.entropy.samples}/
                {cei.entropy.samples_required} observations), its weight
                redistributed across centrality and governance risk.
              </>
            )}
          </p>
        )}
        {topology?.edges?.length ? (
          <ClusterTopologyMap
            topology={topology}
            analysis={cei ? { nodes: cei.nodes, weights: cei.weights } : null}
            height={520}
          />
        ) : (
          <p style={s.muted}>
            No dependencies inferred yet. Edges are derived from Service
            selectors, Ingress backends, and service references in container
            environment variables.
          </p>
        )}
      </div>

      <div style={s.panel}>
        <div style={s.panelHeader}>
          <div style={s.panelTitle}>Workloads</div>
          <select
            style={s.select}
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
          >
            <option value="cei">Sort by CEI</option>
            <option value="headroom">Sort by unused CPU</option>
            <option value="name">Sort by name</option>
          </select>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={s.table}>
            <thead>
              <tr>
                <th style={s.th}>Workload</th>
                <th style={s.th}>Namespace</th>
                <th style={s.thNum}>CEI</th>
                <th style={s.thNum}>Replicas</th>
                <th style={s.thNum}>CPU req</th>
                <th style={s.thNum}>CPU used</th>
                <th style={s.thNum}>Unused</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((w) => (
                <tr key={w.key}>
                  <td style={s.td}>{w.name}</td>
                  <td style={s.tdMuted}>{w.namespace}</td>
                  <td style={s.tdNum}>
                    {w.cei ? (
                      <span style={{ fontWeight: 600 }}>
                        {w.cei.cei_score.toFixed(3)}
                      </span>
                    ) : (
                      '–'
                    )}
                  </td>
                  <td style={s.tdNum}>
                    {w.replicas_ready ?? '–'}/{w.replicas_desired ?? '–'}
                  </td>
                  <td style={s.tdNum}>
                    {w.cpu_cores_requested?.toFixed(3) ?? '–'}
                  </td>
                  <td style={s.tdNum}>
                    {w.cpu_cores_used !== null && w.cpu_cores_used !== undefined
                      ? w.cpu_cores_used.toFixed(4)
                      : '–'}
                  </td>
                  <td
                    style={{
                      ...s.tdNum,
                      color:
                        w.headroom > 0.8
                          ? '#922B21'
                          : w.headroom > 0.5
                          ? '#7D6608'
                          : '#196F3D',
                      fontWeight: w.headroom > 0.8 ? 700 : 400,
                    }}
                  >
                    {w.headroom !== null
                      ? `${(w.headroom * 100).toFixed(0)}%`
                      : '–'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Shell>
  );
}

function Small({ label, value, hint }) {
  return (
    <div style={s.small} title={hint || undefined}>
      <div style={s.smallValue}>{value}</div>
      <div style={s.smallLabel}>{label}</div>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div style={s.metric}>
      <div style={s.metricValue}>{value ?? 0}</div>
      <div style={s.metricLabel}>{label}</div>
    </div>
  );
}

function Shell({ children }) {
  return (
    <>
      <Head>
        <title>CloudOptimizer — Cluster</title>
      </Head>
      <div style={s.page}>
        <header style={s.header}>
          <div style={s.headerInner}>
            <Link href="/app" style={s.brand}>
              CloudOptimizer
            </Link>
          </div>
        </header>
        <main style={s.main}>{children}</main>
      </div>
    </>
  );
}

const s = {
  page: {
    minHeight: '100vh',
    background: '#F8F9FA',
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    color: '#1C2833',
  },
  header: {
    background: 'linear-gradient(135deg, #1B4F72 0%, #2874A6 100%)',
    color: 'white',
  },
  headerInner: { maxWidth: 1100, margin: '0 auto', padding: '16px 20px' },
  brand: { color: 'white', fontWeight: 700, fontSize: 18, textDecoration: 'none' },
  main: { maxWidth: 1100, margin: '0 auto', padding: '24px 20px' },
  back: {
    fontSize: 13,
    color: '#2874A6',
    textDecoration: 'none',
    display: 'inline-block',
    marginBottom: 12,
  },
  titleRow: { display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap' },
  h2: { fontSize: 22, margin: '0 0 8px 0', color: '#1B4F72' },
  meta: { display: 'flex', gap: 12, fontSize: 12, color: '#7B8A8B' },
  muted: { color: '#7B8A8B', fontSize: 14 },
  notice: {
    background: '#FEF9E7',
    border: '1px solid #F7DC6F',
    borderRadius: 6,
    padding: '10px 14px',
    fontSize: 13,
    color: '#7D6608',
    margin: '14px 0',
    lineHeight: 1.5,
  },
  waiting: {
    background: 'white',
    borderRadius: 8,
    padding: 24,
    border: '1px solid #E8EDF0',
  },
  summaryRow: { display: 'flex', gap: 28, margin: '18px 0' },
  metric: {},
  metricValue: { fontSize: 24, fontWeight: 700, color: '#1B4F72' },
  metricLabel: { fontSize: 11, color: '#7B8A8B', textTransform: 'uppercase' },
  panel: {
    background: 'white',
    borderRadius: 8,
    padding: 18,
    marginBottom: 20,
    border: '1px solid #E8EDF0',
  },
  panelHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  panelTitle: {
    fontSize: 12,
    fontWeight: 700,
    color: '#1B4F72',
    textTransform: 'uppercase',
    letterSpacing: '0.6px',
    marginBottom: 12,
  },
  select: {
    fontSize: 12,
    padding: '5px 8px',
    borderRadius: 5,
    border: '1px solid #D5DBDB',
  },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: {
    textAlign: 'left',
    padding: '8px 10px',
    borderBottom: '2px solid #E8EDF0',
    fontSize: 11,
    color: '#7B8A8B',
    textTransform: 'uppercase',
  },
  thNum: {
    textAlign: 'right',
    padding: '8px 10px',
    borderBottom: '2px solid #E8EDF0',
    fontSize: 11,
    color: '#7B8A8B',
    textTransform: 'uppercase',
  },
  td: { padding: '8px 10px', borderBottom: '1px solid #F2F4F4' },
  tdMuted: {
    padding: '8px 10px',
    borderBottom: '1px solid #F2F4F4',
    color: '#7B8A8B',
  },
  tdNum: {
    padding: '8px 10px',
    borderBottom: '1px solid #F2F4F4',
    textAlign: 'right',
    fontVariantNumeric: 'tabular-nums',
  },
  wastePanel: {
    background: 'white',
    border: '1px solid #A9DFBF',
    borderLeft: '4px solid #196F3D',
    borderRadius: 8,
    padding: 20,
    marginBottom: 20,
  },
  wasteHead: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    flexWrap: 'wrap',
    gap: 20,
  },
  wasteLabel: {
    fontSize: 11,
    textTransform: 'uppercase',
    letterSpacing: '0.8px',
    color: '#7B8A8B',
  },
  wasteValue: { fontSize: 34, fontWeight: 700, color: '#196F3D', lineHeight: 1.1 },
  wasteUnit: { fontSize: 15, fontWeight: 500, color: '#7B8A8B', marginLeft: 4 },
  wasteSub: { fontSize: 12, color: '#566573', marginTop: 4 },
  wasteBreakdown: { display: 'flex', gap: 22 },
  wasteNote: {
    fontSize: 11,
    color: '#7B8A8B',
    lineHeight: 1.5,
    margin: '14px 0',
    paddingTop: 12,
    borderTop: '1px solid #EAEDED',
  },
  small: {},
  smallValue: { fontSize: 15, fontWeight: 600, color: '#1C2833' },
  smallLabel: { fontSize: 10, color: '#7B8A8B', textTransform: 'uppercase' },
  oppDetail: { fontSize: 11, color: '#7B8A8B', marginTop: 3, lineHeight: 1.45 },
  finding: {
    display: 'flex',
    gap: 12,
    alignItems: 'flex-start',
    padding: '10px 0',
    borderBottom: '1px solid #F2F4F4',
  },
  sev: {
    fontSize: 10,
    fontWeight: 700,
    padding: '3px 8px',
    borderRadius: 10,
    textTransform: 'uppercase',
    whiteSpace: 'nowrap',
  },
  findingTitle: { fontSize: 13, fontWeight: 600 },
  findingDetail: { fontSize: 12, color: '#7B8A8B', marginTop: 2, lineHeight: 1.45 },
  findingCei: { fontSize: 11, color: '#7B8A8B', whiteSpace: 'nowrap' },
  sandboxBanner: {
    background: '#EAF4FB',
    border: '1px solid #AED6F1',
    borderLeft: '4px solid #2874A6',
    borderRadius: 6,
    padding: '10px 14px',
    fontSize: 13,
    color: '#1B4F72',
    marginBottom: 14,
    lineHeight: 1.5,
  },
  truncated: {
    fontSize: 11,
    color: '#7D6608',
    background: '#FEF9E7',
    padding: '6px 10px',
    borderRadius: 4,
    margin: '0 0 10px 0',
    lineHeight: 1.5,
  },
  vulnHeadline: {
    fontSize: 13,
    color: '#566573',
    margin: '0 0 10px 0',
    lineHeight: 1.5,
  },
  vulnCounts: {
    display: 'flex',
    gap: 16,
    flexWrap: 'wrap',
    fontSize: 11,
    color: '#7B8A8B',
    paddingBottom: 12,
    marginBottom: 4,
    borderBottom: '1px solid #F2F4F4',
  },
  vulnCount: {},
  vulnMeta: {
    fontSize: 10,
    color: '#95A5A6',
    marginTop: 3,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  },
  rebase: {
    marginTop: 12,
    padding: '10px 12px',
    background: '#EAF4FB',
    borderRadius: 4,
    fontSize: 12,
    color: '#1B4F72',
    lineHeight: 1.5,
  },
  ceiNote: {
    fontSize: 11,
    color: '#7B8A8B',
    margin: '0 0 12px 0',
    lineHeight: 1.5,
  },
  error: {
    background: '#FDEDEC',
    color: '#922B21',
    padding: '12px 14px',
    borderRadius: 6,
    fontSize: 13,
    marginBottom: 12,
  },
};
