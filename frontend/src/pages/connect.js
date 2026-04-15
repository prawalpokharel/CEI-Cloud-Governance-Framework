import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import Head from 'next/head';

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001';
const CORE_BASE =
  process.env.NEXT_PUBLIC_CORE_ENGINE_URL || 'http://localhost:8000';

/**
 * Connect Cloud Provider page (PR 8).
 *
 * Three login buttons (AWS / Azure / GCP) that initiate the OAuth flow
 * via /api/cloud/auth/:provider on the backend. After the round trip
 * the page lands on /connect?connected=<provider> and a status badge
 * lights up. Once any provider is connected we offer "Discover topology"
 * which calls /api/cloud/topology/:provider and pipes the result
 * straight into the core engine's /analyze endpoint.
 *
 * In dev / no-credentials deployments every provider runs in MOCK MODE
 * (callback short-circuits to a synthetic token) so the full flow can
 * be exercised without real OAuth app credentials.
 */

const PROVIDER_META = {
  aws: { color: '#FF9900', label: 'AWS', subtitle: 'IAM Identity Center' },
  azure: { color: '#0078D4', label: 'Azure', subtitle: 'Microsoft Entra ID' },
  gcp: { color: '#4285F4', label: 'Google Cloud', subtitle: 'Google OAuth' },
};

export default function ConnectPage() {
  const router = useRouter();
  const justConnected = router.query.connected;

  const [providers, setProviders] = useState([]);
  const [connected, setConnected] = useState([]);
  const [topologies, setTopologies] = useState({});
  const [analysis, setAnalysis] = useState({});
  const [savings, setSavings] = useState({});
  const [loading, setLoading] = useState({});
  const [error, setError] = useState(null);

  const refresh = async () => {
    try {
      const [pRes, sRes] = await Promise.all([
        fetch(`${API_BASE}/api/cloud/providers`),
        fetch(`${API_BASE}/api/cloud/status`),
      ]);
      if (pRes.ok) setProviders((await pRes.json()).providers || []);
      if (sRes.ok) setConnected((await sRes.json()).connected || []);
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const isConnected = (p) => connected.some((c) => c.provider === p);

  const handleConnect = (provider) => {
    // Browser navigation so the redirect chain works through to the IdP
    // and back to /api/cloud/callback/:provider.
    window.location.href = `${API_BASE}/api/cloud/auth/${provider}`;
  };

  const handleDisconnect = async (provider) => {
    try {
      await fetch(`${API_BASE}/api/cloud/disconnect/${provider}`, { method: 'POST' });
      setTopologies((t) => {
        const next = { ...t };
        delete next[provider];
        return next;
      });
      setAnalysis((a) => {
        const next = { ...a };
        delete next[provider];
        return next;
      });
      refresh();
    } catch (e) {
      setError(e.message);
    }
  };

  const handleDiscover = async (provider) => {
    setLoading((l) => ({ ...l, [provider]: true }));
    setError(null);
    try {
      const tRes = await fetch(`${API_BASE}/api/cloud/topology/${provider}`);
      if (!tRes.ok) throw new Error(`topology HTTP ${tRes.status}`);
      const topo = await tRes.json();
      setTopologies((t) => ({ ...t, [provider]: topo }));
    } catch (e) {
      setError(e.message);
    }
    setLoading((l) => ({ ...l, [provider]: false }));
  };

  const handleAnalyze = async (provider) => {
    setLoading((l) => ({ ...l, [`${provider}-analyze`]: true }));
    setError(null);
    try {
      // Route through the backend so we don't depend on
      // NEXT_PUBLIC_CORE_ENGINE_URL being set on the frontend.
      // Backend already knows the core engine URL via CORE_ENGINE_URL.
      const res = await fetch(`${API_BASE}/api/cloud/analyze/${provider}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`analyze HTTP ${res.status}: ${text.slice(0, 200)}`);
      }
      const json = await res.json();
      // Backend returns { provider, topology, analysis } — surface the
      // analysis block to the per-provider summary card.
      setAnalysis((a) => ({ ...a, [provider]: json.analysis }));
    } catch (e) {
      setError(e.message);
    }
    setLoading((l) => ({ ...l, [`${provider}-analyze`]: false }));
  };


  return (
    <>
      <Head>
        <title>Connect Cloud Provider — CloudOptimizer</title>
      </Head>

      <div style={s.page}>
        <header style={s.header}>
          <div style={s.headerInner}>
            <Link href="/" style={s.backLink}>
              ← Home
            </Link>
            <h1 style={s.title}>Connect a Cloud Provider</h1>
            <p style={s.subtitle}>
              Sign in with your AWS, Azure, or GCP account to discover live
              infrastructure and run the CEI pipeline against it. Without
              real OAuth client credentials the flow runs in{' '}
              <strong>mock mode</strong> — the round trip still completes
              and a representative topology is returned so you can preview
              the experience.
            </p>
          </div>
        </header>

        <main style={s.main}>
          {justConnected && (
            <div style={s.banner}>
              ✓ Connected to <strong>{justConnected.toUpperCase()}</strong>.
              {' '}
              You can now discover topology below.
            </div>
          )}

          {error && <div style={s.errorBox}>{error}</div>}

          <div style={s.providerGrid}>
            {providers.map((p) => {
              const meta = PROVIDER_META[p.key] || { color: '#7B8A8B', label: p.key };
              const connectedNow = isConnected(p.key);
              const topo = topologies[p.key];
              const an = analysis[p.key];
              return (
                <div key={p.key} style={s.providerCard}>
                  <div style={{ ...s.providerStripe, background: meta.color }} />
                  <div style={s.providerBody}>
                    <div style={s.providerHeader}>
                      <div>
                        <div style={s.providerLabel}>{meta.label}</div>
                        <div style={s.providerSub}>{meta.subtitle}</div>
                      </div>
                      <div style={s.statusArea}>
                        {connectedNow ? (
                          <span style={s.statusConnected}>● Connected</span>
                        ) : (
                          <span style={s.statusIdle}>○ Not connected</span>
                        )}
                        {p.mock && (
                          <span style={s.mockBadge}>MOCK</span>
                        )}
                      </div>
                    </div>

                    {!connectedNow && (
                      <button
                        onClick={() => handleConnect(p.key)}
                        style={{ ...s.primaryBtn, background: meta.color }}
                      >
                        Connect {meta.label} →
                      </button>
                    )}

                    {connectedNow && (
                      <div style={s.connectedActions}>
                        <button
                          onClick={() => handleDiscover(p.key)}
                          disabled={loading[p.key]}
                          style={s.primaryBtn}
                        >
                          {loading[p.key] ? 'Discovering…' : 'Discover topology'}
                        </button>
                        <button
                          onClick={() => handleDisconnect(p.key)}
                          style={s.secondaryBtn}
                        >
                          Disconnect
                        </button>
                      </div>
                    )}

                    {topo && (
                      <div style={s.topoBlock}>
                        <div style={s.topoHeader}>
                          Discovered: {topo.topology.nodes.length} nodes ·{' '}
                          {topo.topology.edges.length} edges
                        </div>
                        <div style={s.topoSub}>
                          source: {topo.source} · {topo.discovered_at}
                        </div>
                        <div style={s.nodeList}>
                          {topo.topology.nodes.slice(0, 8).map((n) => (
                            <span key={n.id} style={s.nodePill}>
                              <strong>{n.id}</strong>
                              <span style={s.nodeTier}> {n.tier}</span>
                            </span>
                          ))}
                          {topo.topology.nodes.length > 8 && (
                            <span style={s.nodeMore}>
                              +{topo.topology.nodes.length - 8} more
                            </span>
                          )}
                        </div>
                        <button
                          onClick={() => handleAnalyze(p.key)}
                          disabled={loading[`${p.key}-analyze`]}
                          style={{ ...s.primaryBtn, marginTop: 12 }}
                        >
                          {loading[`${p.key}-analyze`] ? 'Running CEI…' : 'Run CEI Analysis →'}
                        </button>
                      </div>
                    )}

                    {an && (
                      <div style={s.analysisBlock}>
                        <div style={s.analysisHeader}>Analysis complete</div>
                        <div style={s.analysisGrid}>
                          <Stat label="Nodes" value={an.nodes?.length} />
                          <Stat
                            label="α / β / γ"
                            value={`${an.weights?.alpha?.toFixed(2)} / ${an.weights?.beta?.toFixed(2)} / ${an.weights?.gamma?.toFixed(2)}`}
                          />
                          <Stat
                            label="Suppression"
                            value={
                              an.oscillation_status?.suppression_active
                                ? 'ACTIVE'
                                : 'CLEAR'
                            }
                          />
                          <Stat
                            label="Savings"
                            value={`$${(an.total_potential_savings || 0).toFixed(0)}/mo`}
                          />
                        </div>
                        <Link
                          href={`/connect/${p.key}`}
                          onClick={() => {
                            // Cache the analysis + topology so the detail
                            // page renders instantly without a refetch.
                            try {
                              sessionStorage.setItem(
                                `cei:connect:${p.key}`,
                                JSON.stringify({
                                  topology: topo?.topology,
                                  analysis: an,
                                })
                              );
                            } catch {
                              /* ignore quota */
                            }
                          }}
                          style={{
                            ...s.primaryBtn,
                            marginTop: 12,
                            display: 'block',
                            textAlign: 'center',
                            textDecoration: 'none',
                          }}
                        >
                          View detailed analysis →
                        </Link>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div style={s.note}>
            <strong>Production setup:</strong> Set{' '}
            <code>AWS_OAUTH_CLIENT_ID</code>,{' '}
            <code>AZURE_OAUTH_CLIENT_ID</code>, and/or{' '}
            <code>GCP_OAUTH_CLIENT_ID</code> (plus the matching{' '}
            <code>_SECRET</code> vars) on the backend Railway service to
            exit mock mode and use real OAuth flows. Topology discovery
            currently returns a stub layout per provider; live EC2/AKS/GKE
            adapters are a follow-up PR.
          </div>
        </main>
      </div>
    </>
  );
}

function Stat({ label, value }) {
  return (
    <div style={s.statCard}>
      <div style={s.statLabel}>{label}</div>
      <div style={s.statValue}>{value ?? '—'}</div>
    </div>
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
    padding: '32px 0',
  },
  headerInner: { maxWidth: 1100, margin: '0 auto', padding: '0 32px' },
  backLink: {
    color: 'rgba(255,255,255,0.85)',
    fontSize: 13,
    textDecoration: 'none',
  },
  title: {
    margin: '12px 0 6px 0',
    fontSize: 28,
    fontWeight: 700,
    letterSpacing: '-0.5px',
  },
  subtitle: { margin: 0, fontSize: 14, opacity: 0.9, lineHeight: 1.6 },
  main: { maxWidth: 1100, margin: '0 auto', padding: 32 },
  banner: {
    background: '#EAFAF1',
    color: '#196F3D',
    border: '1px solid #27AE60',
    padding: '12px 16px',
    borderRadius: 6,
    marginBottom: 16,
  },
  errorBox: {
    background: '#FDEDEC',
    color: '#922B21',
    border: '1px solid #E74C3C',
    padding: '10px 14px',
    borderRadius: 6,
    marginBottom: 16,
  },
  providerGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
    gap: 16,
  },
  providerCard: {
    background: 'white',
    border: '1px solid #E5E8EB',
    borderRadius: 8,
    overflow: 'hidden',
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
  },
  providerStripe: { height: 4 },
  providerBody: { padding: 18 },
  providerHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 14,
  },
  providerLabel: { fontSize: 18, fontWeight: 700, color: '#1B4F72' },
  providerSub: { fontSize: 12, color: '#7B8A8B', marginTop: 2 },
  statusArea: { display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 },
  statusConnected: { fontSize: 12, color: '#196F3D', fontWeight: 600 },
  statusIdle: { fontSize: 12, color: '#7B8A8B' },
  mockBadge: {
    background: '#FEF9E7',
    color: '#7D6608',
    fontSize: 10,
    padding: '2px 8px',
    borderRadius: 10,
    fontWeight: 700,
    letterSpacing: '0.5px',
  },
  primaryBtn: {
    background: '#2874A6',
    color: 'white',
    padding: '10px 18px',
    border: 'none',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    width: '100%',
  },
  secondaryBtn: {
    background: 'white',
    color: '#566573',
    padding: '10px 18px',
    border: '1px solid #CFD8DC',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
  },
  connectedActions: { display: 'flex', gap: 8 },
  topoBlock: {
    marginTop: 14,
    paddingTop: 14,
    borderTop: '1px solid #EAEDED',
  },
  topoHeader: { fontSize: 13, fontWeight: 600, color: '#1B4F72' },
  topoSub: { fontSize: 11, color: '#7B8A8B', marginTop: 2 },
  nodeList: { display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 },
  nodePill: {
    background: '#F4F6F7',
    padding: '4px 10px',
    borderRadius: 12,
    fontSize: 11,
  },
  nodeTier: { color: '#7B8A8B', textTransform: 'capitalize' },
  nodeMore: { fontSize: 11, color: '#7B8A8B', alignSelf: 'center' },
  analysisBlock: {
    marginTop: 14,
    paddingTop: 14,
    borderTop: '1px solid #EAEDED',
  },
  analysisHeader: { fontSize: 13, fontWeight: 600, color: '#196F3D', marginBottom: 8 },
  analysisGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(2, 1fr)',
    gap: 8,
  },
  statCard: { background: '#F4F6F7', borderRadius: 4, padding: '8px 10px' },
  statLabel: { fontSize: 10, color: '#7B8A8B', textTransform: 'uppercase', letterSpacing: '0.5px' },
  statValue: { fontSize: 14, fontWeight: 700, color: '#1B4F72', marginTop: 2 },
  note: {
    marginTop: 24,
    padding: 16,
    background: '#F4F6F7',
    borderRadius: 6,
    fontSize: 12,
    color: '#566573',
    lineHeight: 1.6,
  },
};
