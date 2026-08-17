import { useCallback, useEffect, useState } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import { API_BASE, api, clearToken, getToken, setToken } from '../../lib/appApi';

/**
 * /app — product dashboard.
 *
 * Deliberately separate from the marketing and scenario pages, which back
 * the USPTO/NIW evidence and are frozen.
 *
 * The whole flow this page exists to serve: sign up, name a cluster, copy
 * one helm command, see the cluster appear. Anything that does not move a
 * user through those four steps in under ten minutes does not belong here.
 */
export default function AppDashboard() {
  const [authed, setAuthed] = useState(false);
  const [checking, setChecking] = useState(true);
  const [clusters, setClusters] = useState([]);
  const [error, setError] = useState(null);
  const [newKey, setNewKey] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.listClusters();
      setClusters(data.clusters || []);
      setError(null);
    } catch (e) {
      if (e.status === 401) {
        clearToken();
        setAuthed(false);
      } else {
        setError(e.message);
      }
    }
  }, []);

  useEffect(() => {
    if (!getToken()) {
      setChecking(false);
      return;
    }
    api
      .me()
      .then(() => {
        setAuthed(true);
        return refresh();
      })
      .catch(() => clearToken())
      .finally(() => setChecking(false));
  }, [refresh]);

  // Clusters go from "created" to "connected" only once the agent reports,
  // so the list is polled while the user is watching the install happen.
  useEffect(() => {
    if (!authed) return undefined;
    const timer = setInterval(refresh, 10000);
    return () => clearInterval(timer);
  }, [authed, refresh]);

  if (checking) return <Shell><p style={s.muted}>Loading…</p></Shell>;

  if (!authed) {
    return (
      <Shell>
        <AuthPanel
          onAuthed={() => {
            setAuthed(true);
            refresh();
          }}
        />
      </Shell>
    );
  }

  return (
    <Shell
      onSignOut={() => {
        clearToken();
        setAuthed(false);
        setClusters([]);
      }}
    >
      <div style={s.headerRow}>
        <h2 style={s.h2}>Clusters</h2>
        <AddCluster
          onCreated={(result) => {
            setNewKey(result);
            refresh();
          }}
        />
      </div>

      {error && <div style={s.error}>{error}</div>}
      {newKey && <KeyReveal result={newKey} onDismiss={() => setNewKey(null)} />}

      {clusters.length === 0 && !newKey && (
        <div style={s.empty}>
          <div style={s.stepsTitle}>Connect your first cluster — three steps</div>
          <ol style={s.steps}>
            <li>
              <strong>Add a cluster</strong> above. You get an API key,
              shown exactly once, and the exact install command.
            </li>
            <li>
              <strong>Run that command in your Kubernetes cluster.</strong>{' '}
              It installs a small read-only agent pod (Helm chart; no write
              permissions, no access to secrets) that reports your cluster's
              topology here every minute.
            </li>
            <li>
              <strong>Refresh this page.</strong> Within ~60 seconds the
              cluster shows connected, and the dashboard fills with its
              dependency map, criticality ranking, health, and cost.
            </li>
          </ol>
          <p style={s.muted}>
            Want to look around first?{' '}
            <Link href="/app/sandbox" style={s.inlineLink}>
              Explore the sandbox
            </Link>{' '}
            — same dashboard, sample cluster.
          </p>
        </div>
      )}

      <div style={s.grid}>
        {clusters.map((c) => (
          <Link key={c.id} href={`/app/${c.id}`} style={s.cardLink}>
            <div style={s.card}>
              <div style={s.cardTop}>
                <span style={s.cardName}>{c.name}</span>
                <span
                  style={{
                    ...s.badge,
                    background: c.connected ? '#EAFAF1' : '#FEF9E7',
                    color: c.connected ? '#196F3D' : '#7D6608',
                  }}
                >
                  {c.connected ? 'connected' : 'waiting for agent'}
                </span>
              </div>
              <div style={s.cardMeta}>
                {c.provider !== 'unknown' && <span>{c.provider.toUpperCase()}</span>}
                {c.k8s_version && <span>k8s {c.k8s_version}</span>}
                {c.agent_version && <span>agent {c.agent_version}</span>}
              </div>
              <div style={s.stats}>
                <Stat label="workloads" value={c.workload_count} />
                <Stat label="pods" value={c.pod_count} />
                <Stat label="nodes" value={c.node_count} />
              </div>
              {c.connected && !c.metrics_available && (
                <div style={s.warn}>
                  metrics-server not detected — usage data unavailable
                </div>
              )}
              {!c.connected && (
                <div style={s.installHint}>
                  Waiting for the agent. Install it with the command shown
                  when this cluster was created (key was shown once):
                  <code style={s.inlineCode}>
                    helm install cloudoptimizer … --set apiKey=&lt;key&gt; --set endpoint={API_BASE}
                  </code>
                  Running the local dev stack? <code style={s.inlineCode}>./deploy.sh agent &lt;key&gt;</code>{' '}
                  Lost the key? Delete this cluster and add it again.
                </div>
              )}
            </div>
          </Link>
        ))}
      </div>
    </Shell>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div style={s.statValue}>{value ?? 0}</div>
      <div style={s.statLabel}>{label}</div>
    </div>
  );
}

function AuthPanel({ onAuthed }) {
  // The landing page's two buttons differ only by this: Sign up lands on
  // the default, Log in arrives as /app?mode=login.
  const initialMode =
    typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('mode') === 'login'
      ? 'login'
      : 'signup';
  const [mode, setMode] = useState(initialMode);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [org, setOrg] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const fn = mode === 'signup' ? api.signup : api.login;
      const result = await fn(
        mode === 'signup'
          ? { email, password, organization: org || undefined }
          : { email, password }
      );
      setToken(result.token);
      onAuthed();
    } catch (e2) {
      setError(e2.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={s.authCard}>
      <h2 style={s.h2}>{mode === 'signup' ? 'Create an account' : 'Sign in'}</h2>
      <p style={s.muted}>
        Connect a Kubernetes cluster and see its dependency topology, ranked
        by CEI.
      </p>
      <form onSubmit={submit} style={s.form}>
        <input
          style={s.input}
          type="email"
          placeholder="you@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <input
          style={s.input}
          type="password"
          placeholder="Password (12+ characters)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {mode === 'signup' && (
          <input
            style={s.input}
            placeholder="Organization (optional)"
            value={org}
            onChange={(e) => setOrg(e.target.value)}
          />
        )}
        {error && <div style={s.error}>{error}</div>}
        <button style={s.primary} disabled={busy} type="submit">
          {busy ? 'Working…' : mode === 'signup' ? 'Create account' : 'Sign in'}
        </button>
      </form>
      <button
        style={s.linkBtn}
        onClick={() => {
          setMode(mode === 'signup' ? 'login' : 'signup');
          setError(null);
        }}
      >
        {mode === 'signup'
          ? 'Already have an account? Sign in'
          : 'Need an account? Sign up'}
      </button>
    </div>
  );
}

function AddCluster({ onCreated }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const result = await api.createCluster(name.trim());
      onCreated(result);
      setName('');
    } catch (e2) {
      alert(e2.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} style={s.addRow}>
      <input
        style={{ ...s.input, marginBottom: 0, width: 220 }}
        placeholder="Cluster name"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <button style={s.primary} disabled={busy} type="submit">
        {busy ? 'Adding…' : 'Add cluster'}
      </button>
    </form>
  );
}

function KeyReveal({ result, onDismiss }) {
  // The endpoint is whatever API origin THIS dashboard is talking to, so
  // the copied command points the agent at the same deployment the key came
  // from. Omitting it made every self-hosted install silently target the
  // hosted production URL, where the key does not exist.
  const command = [
    'helm install cloudoptimizer \\',
    '  oci://ghcr.io/prawalpokharel/charts/cloudoptimizer-agent \\',
    '  --namespace cloudoptimizer --create-namespace \\',
    `  --set apiKey=${result.api_key} \\`,
    `  --set endpoint=${API_BASE}`,
  ].join('\n');
  const localCommand = `./deploy.sh agent ${result.api_key}`;

  return (
    <div style={s.keyPanel}>
      <div style={s.keyTitle}>Install the agent on “{result.cluster.name}”</div>
      <p style={s.keyWarn}>
        This key is shown once and cannot be retrieved later. Copy it now — if
        you lose it, delete the cluster and add it again.
      </p>
      <pre style={s.code}>{command}</pre>
      <p style={s.keyLocalNote}>
        Running the local dev stack on this machine? The one-liner instead:
      </p>
      <pre style={s.code}>{localCommand}</pre>
      <p style={s.keyAfter}>
        Within about a minute of the agent starting, this cluster shows
        connected and the dashboard fills with its topology, criticality
        ranking, health, and cost — refresh to see it arrive. The agent is
        read-only: it can never modify your cluster.
      </p>
      <div style={s.keyActions}>
        <button
          style={s.primary}
          onClick={() => navigator.clipboard?.writeText(command)}
        >
          Copy command
        </button>
        <button style={s.linkBtn} onClick={onDismiss}>
          Done
        </button>
      </div>
    </div>
  );
}

function Shell({ children, onSignOut }) {
  return (
    <>
      <Head>
        <title>CloudOptimizer — Dashboard</title>
      </Head>
      <div style={s.page}>
        <header style={s.header}>
          <div style={s.headerInner}>
            <Link href="/app" style={s.brand}>
              CloudOptimizer
            </Link>
            <div style={s.headerRight}>
              <Link href="/demo" style={s.headerLink}>
                Scenarios
              </Link>
              {onSignOut && (
                <button style={s.headerLink} onClick={onSignOut}>
                  Sign out
                </button>
              )}
            </div>
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
  headerInner: {
    maxWidth: 1100,
    margin: '0 auto',
    padding: '16px 20px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  brand: {
    color: 'white',
    fontWeight: 700,
    fontSize: 18,
    textDecoration: 'none',
  },
  headerRight: { display: 'flex', gap: 16, alignItems: 'center' },
  headerLink: {
    color: 'rgba(255,255,255,0.9)',
    fontSize: 13,
    textDecoration: 'none',
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    padding: 0,
    fontFamily: 'inherit',
  },
  main: { maxWidth: 1100, margin: '0 auto', padding: '28px 20px' },
  h2: { fontSize: 20, margin: '0 0 6px 0', color: '#1B4F72' },
  muted: { color: '#7B8A8B', fontSize: 14, margin: '0 0 16px 0' },
  headerRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    flexWrap: 'wrap',
    gap: 12,
    marginBottom: 18,
  },
  addRow: { display: 'flex', gap: 8 },
  authCard: {
    background: 'white',
    borderRadius: 8,
    padding: 28,
    maxWidth: 420,
    margin: '40px auto',
    boxShadow: '0 2px 10px rgba(0,0,0,0.06)',
  },
  form: { display: 'flex', flexDirection: 'column' },
  input: {
    padding: '10px 12px',
    border: '1px solid #D5DBDB',
    borderRadius: 6,
    fontSize: 14,
    marginBottom: 10,
    fontFamily: 'inherit',
  },
  primary: {
    background: '#1B4F72',
    color: 'white',
    border: 'none',
    borderRadius: 6,
    padding: '10px 16px',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
  },
  linkBtn: {
    background: 'none',
    border: 'none',
    color: '#2874A6',
    fontSize: 13,
    cursor: 'pointer',
    marginTop: 12,
    padding: 0,
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
    gap: 16,
  },
  cardLink: { textDecoration: 'none', color: 'inherit' },
  card: {
    background: 'white',
    borderRadius: 8,
    padding: 18,
    boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
    border: '1px solid #E8EDF0',
  },
  cardTop: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  cardName: { fontWeight: 600, fontSize: 15, color: '#1B4F72' },
  badge: {
    fontSize: 11,
    fontWeight: 600,
    padding: '3px 9px',
    borderRadius: 10,
  },
  cardMeta: {
    display: 'flex',
    gap: 10,
    fontSize: 11,
    color: '#7B8A8B',
    marginBottom: 12,
    flexWrap: 'wrap',
  },
  stats: { display: 'flex', gap: 24 },
  statValue: { fontSize: 20, fontWeight: 700, color: '#1C2833' },
  statLabel: { fontSize: 11, color: '#7B8A8B', textTransform: 'uppercase' },
  stepsTitle: { fontSize: 15, fontWeight: 700, color: '#1B2631', marginBottom: 8 },
  steps: { margin: '0 0 12px 18px', padding: 0, fontSize: 13.5, color: '#2C3E50', lineHeight: 1.7 },
  installHint: {
    marginTop: 10, fontSize: 12, color: '#7D6608', background: '#FEF9E7',
    borderRadius: 6, padding: '8px 10px', lineHeight: 1.6,
  },
  inlineCode: {
    display: 'block', fontFamily: 'ui-monospace, monospace', fontSize: 11,
    background: '#FDF6E3', borderRadius: 4, padding: '3px 6px', margin: '4px 0',
    overflowX: 'auto', whiteSpace: 'nowrap',
  },
  warn: {
    marginTop: 12,
    fontSize: 11,
    color: '#7D6608',
    background: '#FEF9E7',
    padding: '6px 8px',
    borderRadius: 4,
  },
  empty: { padding: '30px 0' },
  inlineLink: { color: '#2874A6', textDecoration: 'underline' },
  error: {
    background: '#FDEDEC',
    color: '#922B21',
    padding: '10px 12px',
    borderRadius: 6,
    fontSize: 13,
    marginBottom: 12,
  },
  keyPanel: {
    background: 'white',
    border: '1px solid #AED6F1',
    borderLeft: '4px solid #2874A6',
    borderRadius: 8,
    padding: 20,
    marginBottom: 20,
  },
  keyTitle: { fontWeight: 700, color: '#1B4F72', marginBottom: 6 },
  keyWarn: { fontSize: 13, color: '#922B21', margin: '0 0 12px 0' },
  code: {
    background: '#1C2833',
    color: '#EAF4FB',
    padding: 14,
    borderRadius: 6,
    fontSize: 12,
    overflowX: 'auto',
    lineHeight: 1.6,
  },
  keyLocalNote: { fontSize: 12.5, color: '#7F8C8D', margin: '10px 0 4px' },
  keyAfter: { fontSize: 12.5, color: '#5D6D7E', margin: '10px 0 2px', lineHeight: 1.5 },
  keyActions: { display: 'flex', gap: 12, alignItems: 'center', marginTop: 12 },
};
