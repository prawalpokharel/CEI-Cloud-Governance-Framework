import { useEffect, useMemo, useState } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import ClusterTopologyMap from '../../components/app/ClusterTopologyMap';
import { api, getToken } from '../../lib/appApi';

/**
 * Live cluster view: dependency map plus the workload table behind it.
 *
 * CEI is not computed on live clusters yet -- that is Week 3 -- so the map
 * renders structure without scores. The table already shows requested-vs-used
 * headroom, which is the Phase 2 waste signal and is available as soon as
 * metrics-server is present.
 */
export default function ClusterView() {
  const router = useRouter();
  const { clusterId } = router.query;
  const [data, setData] = useState(null);
  const [history, setHistory] = useState(null);
  const [error, setError] = useState(null);
  const [sortBy, setSortBy] = useState('headroom');

  useEffect(() => {
    if (!clusterId) return undefined;
    if (!getToken()) {
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
    };
    load();
    const timer = setInterval(load, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [clusterId, router]);

  // The map component expects {nodes:[{id}], edges:[{source,target,weight}]}.
  const topology = useMemo(() => {
    if (!data?.workloads) return null;
    return {
      nodes: data.workloads.map((w) => ({
        id: w.key,
        label: w.name,
        tier: w.namespace,
      })),
      edges: (data.edges || []).map((e) => ({
        source: e.source,
        target: e.target,
        weight: e.confidence ?? 1,
      })),
    };
  }, [data]);

  const rows = useMemo(() => {
    const list = (data?.workloads || []).map((w) => {
      const req = w.cpu_cores_requested;
      const used = w.cpu_cores_used;
      const headroom =
        req && used !== null && used !== undefined ? 1 - used / req : null;
      return { ...w, headroom };
    });
    if (sortBy === 'headroom') {
      list.sort((a, b) => (b.headroom ?? -1) - (a.headroom ?? -1));
    } else {
      list.sort((a, b) => a.key.localeCompare(b.key));
    }
    return list;
  }, [data, sortBy]);

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

      <div style={s.summaryRow}>
        <Metric label="workloads" value={data.summary?.workloads} />
        <Metric label="services" value={data.summary?.services} />
        <Metric label="pods" value={data.summary?.pods} />
        <Metric label="dependencies" value={data.summary?.edges?.total} />
      </div>

      <div style={s.panel}>
        <div style={s.panelTitle}>Dependency topology</div>
        {topology?.edges?.length ? (
          <ClusterTopologyMap topology={topology} analysis={null} height={520} />
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
  error: {
    background: '#FDEDEC',
    color: '#922B21',
    padding: '12px 14px',
    borderRadius: 6,
    fontSize: 13,
    marginBottom: 12,
  },
};
