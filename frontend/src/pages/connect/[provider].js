import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import Head from 'next/head';
import DetailedAnalysisView from '../../components/connect/DetailedAnalysisView';

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001';

const PROVIDER_META = {
  aws: { color: '#FF9900', label: 'AWS', subtitle: 'IAM Identity Center' },
  azure: { color: '#0078D4', label: 'Azure', subtitle: 'Microsoft Entra ID' },
  gcp: { color: '#4285F4', label: 'Google Cloud', subtitle: 'Google OAuth' },
};

/**
 * Detailed analysis page per provider (PR 8 follow-up).
 *
 * Renders the full DetailedAnalysisView for a provider that the user
 * has already connected and analyzed on /connect. Reads the cached
 * topology + analysis from sessionStorage if available; otherwise
 * refetches them from the backend (POST /api/cloud/analyze/<provider>
 * which returns both blocks in a single call).
 *
 * The user lands here by clicking 'View detailed analysis' on the
 * /connect page. A 'Back' link returns them to the provider grid.
 */
export default function ProviderDetailPage() {
  const router = useRouter();
  const { provider } = router.query;

  const [topology, setTopology] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!provider) return;
    // Try cache first (set by /connect when 'View detailed analysis' is clicked)
    try {
      const raw = sessionStorage.getItem(`cei:connect:${provider}`);
      if (raw) {
        const cached = JSON.parse(raw);
        if (cached?.analysis && cached?.topology) {
          setTopology(cached.topology);
          setAnalysis(cached.analysis);
          return;
        }
      }
    } catch {
      /* ignore */
    }
    // Fallback: refetch via backend
    refetch(provider);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const refetch = async (p) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/cloud/analyze/${p}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
      }
      const json = await res.json();
      setTopology(json.topology);
      setAnalysis(json.analysis);
      // Refresh the cache so subsequent navigations are instant.
      try {
        sessionStorage.setItem(
          `cei:connect:${p}`,
          JSON.stringify({ topology: json.topology, analysis: json.analysis })
        );
      } catch {
        /* ignore quota errors */
      }
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  const meta = PROVIDER_META[provider] || {
    color: '#7B8A8B',
    label: provider || 'Provider',
    subtitle: '',
  };

  return (
    <>
      <Head>
        <title>{meta.label} CEI Analysis — CloudOptimizer</title>
      </Head>

      <div style={s.page}>
        <header style={{ ...s.header, background: gradient(meta.color) }}>
          <div style={s.headerInner}>
            <Link href="/connect" style={s.backLink}>
              ← Back to Connect Cloud
            </Link>
            <div style={s.headerTitleRow}>
              <div>
                <h1 style={s.title}>{meta.label} — CEI Analysis</h1>
                <p style={s.subtitle}>{meta.subtitle}</p>
              </div>
              <button onClick={() => refetch(provider)} style={s.refreshBtn}>
                {loading ? 'Re-running…' : 'Re-run analysis ↻'}
              </button>
            </div>
          </div>
        </header>

        <main style={s.main}>
          {loading && !analysis && (
            <div style={s.loadingBox}>Running CEI pipeline against {meta.label}…</div>
          )}

          {error && (
            <div style={s.errorBox}>
              <strong>Failed to load analysis:</strong> {error}
              <div style={{ marginTop: 8 }}>
                You may need to{' '}
                <Link href="/connect" style={s.errorLink}>
                  reconnect the provider
                </Link>{' '}
                first.
              </div>
            </div>
          )}

          {analysis && topology && (
            <DetailedAnalysisView topology={topology} analysis={analysis} />
          )}
        </main>
      </div>
    </>
  );
}

function gradient(color) {
  // Blend the provider brand color with the CloudOptimizer dark blue
  // for the page header so each provider's page has a recognizable hue.
  return `linear-gradient(135deg, #1B4F72 0%, ${color} 110%)`;
}

const s = {
  page: {
    minHeight: '100vh',
    background: '#F8F9FA',
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    color: '#1C2833',
  },
  header: { color: 'white', padding: '28px 0' },
  headerInner: { maxWidth: 1200, margin: '0 auto', padding: '0 32px' },
  backLink: {
    color: 'rgba(255,255,255,0.85)',
    fontSize: 13,
    textDecoration: 'none',
  },
  headerTitleRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    flexWrap: 'wrap',
    gap: 16,
    marginTop: 12,
  },
  title: {
    margin: 0,
    fontSize: 26,
    fontWeight: 700,
    letterSpacing: '-0.5px',
  },
  subtitle: { margin: '4px 0 0 0', fontSize: 13, opacity: 0.9 },
  refreshBtn: {
    background: 'rgba(255,255,255,0.95)',
    color: '#1B4F72',
    border: 'none',
    borderRadius: 6,
    padding: '8px 14px',
    fontSize: 12,
    fontWeight: 700,
    cursor: 'pointer',
    boxShadow: '0 1px 3px rgba(0,0,0,0.15)',
    whiteSpace: 'nowrap',
  },
  main: { maxWidth: 1200, margin: '0 auto', padding: '24px 32px 48px' },
  loadingBox: {
    background: 'white',
    border: '1px solid #E5E8EB',
    borderRadius: 8,
    padding: 24,
    textAlign: 'center',
    color: '#566573',
    fontSize: 14,
  },
  errorBox: {
    background: '#FDEDEC',
    border: '1px solid #E74C3C',
    color: '#922B21',
    borderRadius: 6,
    padding: '14px 18px',
    fontSize: 13,
  },
  errorLink: { color: '#1B4F72', fontWeight: 600 },
};
