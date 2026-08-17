import Head from 'next/head';
import Link from 'next/link';

/**
 * The landing page: what CloudOptimizer is, for two audiences at once.
 *
 * Every section carries a business line and a technical line, in that
 * order — a manager reads the bold text and understands the value, an
 * engineer reads the line underneath and understands the mechanism. The
 * graphics are inline SVG: no chart library, nothing external, CSP-safe.
 *
 * The NIW demonstration surface — previously this route — lives intact at
 * /niw, reached by the "For NIW Reviewers" button. Nothing there changed.
 */

const C = {
  ink: '#1B2631',
  deep: '#1B4F72',
  blue: '#2874A6',
  accent: '#F39C12',
  green: '#1E8449',
  red: '#C0392B',
  paper: '#FDFEFE',
  mist: '#F4F6F7',
  line: '#D5DBDB',
  sub: '#5D6D7E',
};

export default function Landing() {
  return (
    <>
      <Head>
        <title>CloudOptimizer — Know what breaks before it breaks</title>
        <meta
          name="description"
          content="CloudOptimizer maps what your cloud services actually depend on, finds the failures hiding in the architecture, and tells you the cheapest fix — validated against real failure."
        />
      </Head>

      <div style={s.page}>
        {/* ── header ─────────────────────────────────────────────────── */}
        <header style={s.header}>
          <div style={s.headerInner}>
            <div style={s.brand}>CloudOptimizer</div>
            <nav style={s.nav}>
              <Link href="/demo" style={s.navLink}>Live scenarios</Link>
              <Link href="/niw" style={s.niwButton}>For NIW Reviewers →</Link>
              <Link href="/app?mode=login" style={s.loginBtn}>Log in</Link>
              <Link href="/app" style={s.signupBtn}>Sign up</Link>
            </nav>
          </div>
        </header>

        {/* ── hero ───────────────────────────────────────────────────── */}
        <section style={s.hero}>
          <h1 style={s.h1}>
            Every dashboard was green.
            <br />
            Then one service took down forty.
          </h1>
          <p style={s.lead}>
            CloudOptimizer maps what your cloud services <em>actually</em>{' '}
            depend on, finds the outage hiding in the architecture while
            every metric still looks fine, and tells you the cheapest way to
            remove it — with predictions validated against real, controlled
            failure.
          </p>
          <div style={s.ctaRow}>
            <div style={s.ctaCol}>
              <Link href="/app" style={s.ctaPrimary}>Sign up — it&apos;s three steps</Link>
              <span style={s.ctaHint}>
                Create an account → add a cluster → run one install command
                in your Kubernetes cluster. Data appears in about a minute.
              </span>
            </div>
            <div style={s.ctaCol}>
              <Link href="/app?mode=login" style={s.ctaSecondary}>Log in</Link>
              <span style={s.ctaHint}>
                Already installed the agent? Your clusters and their live
                dependency maps are waiting.
              </span>
            </div>
          </div>
        </section>

        {/* ── the problem, drawn ─────────────────────────────────────── */}
        <section style={s.section}>
          <h2 style={s.h2}>The problem your monitoring can&apos;t see</h2>
          <p style={s.para}>
            <strong>Monitoring watches components. Outages come from the
            relationships between them.</strong>{' '}
            <span style={s.tech}>
              A system can be one small disturbance away from a very large
              failure — with CPU at 38%, latency at 72ms, and every pod
              healthy — because months of normal shipping quietly made one
              service the thing everything else stands on.
            </span>
          </p>
          <ProblemDiagram />
        </section>

        {/* ── how it works, drawn ────────────────────────────────────── */}
        <section style={{ ...s.section, background: C.mist }}>
          <h2 style={s.h2}>How it works</h2>
          <FlowDiagram />
          <div style={s.stepsRow}>
            <Step
              n="1"
              title="A small read-only agent"
              body="One Helm command installs a pod that observes your cluster. It has no write access and cannot read secrets — enforced by Kubernetes permissions, not promises."
            />
            <Step
              n="2"
              title="The live dependency graph"
              body="It maps what actually talks to what — including managed databases, identity providers, and payment APIs outside the cluster — refreshed every minute."
            />
            <Step
              n="3"
              title="Answers, not dashboards"
              body="Blast radius per service, root-cause diagnosis during incidents, cost savings checked for safety, and ranked fixes priced in dollars. Fixes ship as pull requests you review."
            />
          </div>
        </section>

        {/* ── benefits grid ──────────────────────────────────────────── */}
        <section style={s.section}>
          <h2 style={s.h2}>What you get</h2>
          <div style={s.grid}>
            <Card
              biz="Know what fails together"
              tech="Fourteen services with no connection to each other can all depend on one auth endpoint. The graph shows it; nothing per-service ever will."
            />
            <Card
              biz="Cut the bill without cutting resilience"
              tech='Every savings recommendation is priced against the downtime risk it creates: "saves $23k/yr, adds $4k of risk — take it" vs "this $31k saving costs $210k — don&apos;t."'
            />
            <Card
              biz="3am incidents, triaged in one call"
              tech="Root causes separated from collateral on the dependency graph, what changed on the root named, who to page listed — as a paste-ready incident brief."
            />
            <Card
              biz="Catch risky changes before merge"
              tech="Every pull request gets a blast-radius comment: what the change reaches, which affected services have no failover, and whether it concentrates the architecture."
            />
            <Card
              biz="Alerts ranked by what's at risk"
              tech="A critical CVE in an isolated batch job is not a high CVE in the service half the estate depends on. Findings are ranked by blast radius, not raw severity."
            />
            <Card
              biz="Told what to fix first — and if it's worth it"
              tech="Concrete interventions simulated against your actual architecture and ranked by risk reduced per dollar. When the honest answer is 'do nothing', it says so."
            />
          </div>
        </section>

        {/* ── proof strip ────────────────────────────────────────────── */}
        <section style={s.proof}>
          <div style={s.proofItem}>
            <div style={s.proofBig}>0.98</div>
            <div style={s.proofSmall}>
              correlation between predicted and measured failure impact,
              under controlled fault injection on a live cluster
            </div>
          </div>
          <div style={s.proofItem}>
            <div style={s.proofBig}>100%</div>
            <div style={s.proofSmall}>
              recall — zero real dependencies missed. The validation harness
              ships with the product; run it on your own staging cluster.
            </div>
          </div>
          <div style={s.proofItem}>
            <div style={s.proofBig}>Read-only</div>
            <div style={s.proofSmall}>
              the agent cannot modify your cluster or read your secrets.
              Fixes arrive as pull requests your team reviews.
            </div>
          </div>
        </section>

        {/* ── footer ─────────────────────────────────────────────────── */}
        <footer style={s.footer}>
          <div style={s.footerInner}>
            <div>
              <div style={s.footerBrand}>CloudOptimizer</div>
              <div style={s.footerSub}>
                Patent-pending dependency-graph analysis · USPTO App. No.
                19/641,446
              </div>
            </div>
            <div style={s.footerLinks}>
              <Link href="/app" style={s.footerCta}>Sign up</Link>
              <Link href="/app?mode=login" style={s.footerLink}>Log in</Link>
              <Link href="/demo" style={s.footerLink}>Live scenarios</Link>
              <Link href="/niw" style={s.footerNiw}>
                For NIW Reviewers → the research &amp; demonstration site
              </Link>
            </div>
          </div>
        </footer>
      </div>
    </>
  );
}

/* ── the green-metrics-fragile-architecture diagram ──────────────────── */
function ProblemDiagram() {
  return (
    <svg viewBox="0 0 860 330" style={s.svg} role="img"
      aria-label="Metrics look healthy while the architecture concentrates on one service">
      {/* left: the metrics panel */}
      <rect x="20" y="30" width="250" height="250" rx="10" fill="#FFFFFF" stroke={C.line} />
      <text x="145" y="60" textAnchor="middle" fontSize="14" fontWeight="700" fill={C.ink}>
        What monitoring shows
      </text>
      {[
        ['CPU', '38%'], ['Memory', '61%'], ['Latency', '72 ms'],
        ['Error rate', '0.3%'], ['Pods', 'healthy'],
      ].map(([k, v], i) => (
        <g key={k}>
          <text x="45" y={95 + i * 32} fontSize="13" fill={C.sub}>{k}</text>
          <text x="195" y={95 + i * 32} fontSize="13" fontWeight="700" fill={C.green}>{v} ✓</text>
        </g>
      ))}
      <text x="145" y="265" textAnchor="middle" fontSize="12" fill={C.green} fontWeight="700">
        Everything looks fine
      </text>

      {/* middle: the reveal arrow */}
      <text x="318" y="150" fontSize="26" fill={C.sub}>→</text>
      <text x="305" y="175" fontSize="11" fill={C.sub}>meanwhile</text>

      {/* right: the actual architecture */}
      <rect x="370" y="30" width="470" height="250" rx="10" fill="#FFFFFF" stroke={C.line} />
      <text x="605" y="60" textAnchor="middle" fontSize="14" fontWeight="700" fill={C.ink}>
        What the architecture became
      </text>
      {/* IAM at top */}
      <rect x="560" y="75" width="90" height="26" rx="6" fill="#FDEDEC" stroke={C.red} />
      <text x="605" y="93" textAnchor="middle" fontSize="12" fontWeight="700" fill={C.red}>IAM ×1</text>
      {/* two APIs */}
      <rect x="475" y="122" width="70" height="24" rx="6" fill={C.mist} stroke={C.line} />
      <text x="510" y="139" textAnchor="middle" fontSize="12" fill={C.ink}>API</text>
      <rect x="665" y="122" width="70" height="24" rx="6" fill={C.mist} stroke={C.line} />
      <text x="700" y="139" textAnchor="middle" fontSize="12" fill={C.ink}>API</text>
      {/* service X */}
      <rect x="540" y="170" width="130" height="28" rx="6" fill="#FEF9E7" stroke={C.accent} strokeWidth="2" />
      <text x="605" y="189" textAnchor="middle" fontSize="12.5" fontWeight="700" fill={C.ink}>
        Service X
      </text>
      {/* the 47 */}
      <rect x="455" y="228" width="300" height="30" rx="6" fill={C.mist} stroke={C.line} />
      <text x="605" y="248" textAnchor="middle" fontSize="12" fill={C.sub}>
        47 services quietly depend on this path
      </text>
      {/* edges */}
      <g stroke={C.sub} strokeWidth="1.4" fill="none">
        <path d="M585 101 L520 122" />
        <path d="M625 101 L690 122" />
        <path d="M515 146 L580 170" />
        <path d="M695 146 L630 170" />
        <path d="M605 198 L605 228" />
      </g>
      <text x="605" y="300" textAnchor="middle" fontSize="12.5" fontWeight="700" fill={C.red}>
        One small disturbance here = a very large outage
      </text>
      <text x="145" y="305" textAnchor="middle" fontSize="12" fill={C.sub}>
        …and none of these numbers will warn you
      </text>
    </svg>
  );
}

/* ── the agent → graph → answers flow ────────────────────────────────── */
function FlowDiagram() {
  const box = (x, y, w, h, fill, stroke) => ({
    x, y, width: w, height: h, rx: 10, fill, stroke, strokeWidth: 1.5,
  });
  return (
    <svg viewBox="0 0 860 210" style={s.svg} role="img"
      aria-label="A read-only agent builds a live dependency graph that powers every answer">
      {/* your cluster */}
      <rect {...box(20, 40, 200, 130, '#FFFFFF', C.line)} />
      <text x="120" y="68" textAnchor="middle" fontSize="13.5" fontWeight="700" fill={C.ink}>
        Your Kubernetes cluster
      </text>
      {[0, 1, 2].map((i) => (
        <rect key={i} x={45 + i * 52} y="85" width="44" height="22" rx="5" fill={C.mist} stroke={C.line} />
      ))}
      <rect x="45" y="120" width="150" height="26" rx="6" fill="#EAF2F8" stroke={C.blue} />
      <text x="120" y="138" textAnchor="middle" fontSize="11.5" fontWeight="700" fill={C.deep}>
        agent · read-only
      </text>

      <text x="248" y="110" fontSize="24" fill={C.sub}>→</text>

      {/* the graph */}
      <rect {...box(290, 30, 240, 150, '#FFFFFF', C.blue)} />
      <text x="410" y="56" textAnchor="middle" fontSize="13.5" fontWeight="700" fill={C.deep}>
        Live dependency graph
      </text>
      <g fill={C.blue}>
        <circle cx="360" cy="95" r="9" />
        <circle cx="460" cy="90" r="7" />
        <circle cx="410" cy="130" r="12" fill={C.accent} />
        <circle cx="340" cy="145" r="6" />
        <circle cx="480" cy="140" r="7" />
      </g>
      <g stroke={C.sub} strokeWidth="1.3">
        <path d="M366 101 L402 124" /><path d="M455 96 L419 124" />
        <path d="M346 141 L399 133" /><path d="M473 137 L422 132" />
      </g>
      <text x="410" y="168" textAnchor="middle" fontSize="11" fill={C.sub}>
        incl. databases, auth, and SaaS outside the cluster
      </text>

      <text x="556" y="110" fontSize="24" fill={C.sub}>→</text>

      {/* the answers */}
      {[
        ['Blast radius', 'what fails together', 30],
        ['Cost × risk', 'savings that are safe', 76],
        ['Diagnosis', 'root cause in one call', 122],
      ].map(([t, d, y]) => (
        <g key={t}>
          <rect {...box(600, y, 240, 38, '#FFFFFF', C.line)} />
          <text x="615" y={y + 17} fontSize="12.5" fontWeight="700" fill={C.ink}>{t}</text>
          <text x="615" y={y + 31} fontSize="10.5" fill={C.sub}>{d}</text>
        </g>
      ))}
    </svg>
  );
}

function Step({ n, title, body }) {
  return (
    <div style={s.step}>
      <div style={s.stepN}>{n}</div>
      <div>
        <div style={s.stepTitle}>{title}</div>
        <div style={s.stepBody}>{body}</div>
      </div>
    </div>
  );
}

function Card({ biz, tech }) {
  return (
    <div style={s.card}>
      <div style={s.cardBiz}>{biz}</div>
      <div style={s.cardTech}>{tech}</div>
    </div>
  );
}

const s = {
  page: {
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    background: C.paper,
    color: C.ink,
    minHeight: '100vh',
  },
  header: {
    background: C.deep,
    padding: '0 20px',
    position: 'sticky',
    top: 0,
    zIndex: 10,
  },
  headerInner: {
    maxWidth: 1080,
    margin: '0 auto',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    height: 60,
    gap: 12,
    flexWrap: 'wrap',
  },
  brand: { color: '#FFF', fontSize: 19, fontWeight: 800, letterSpacing: 0.2 },
  nav: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  navLink: {
    color: 'rgba(255,255,255,0.85)', fontSize: 13, textDecoration: 'none',
    padding: '6px 8px',
  },
  niwButton: {
    color: '#FFF', fontSize: 12.5, fontWeight: 700, textDecoration: 'none',
    border: '1px solid rgba(255,255,255,0.5)', borderRadius: 6,
    padding: '7px 11px', whiteSpace: 'nowrap',
  },
  loginBtn: {
    color: '#FFF', fontSize: 13, fontWeight: 700, textDecoration: 'none',
    padding: '7px 12px', borderRadius: 6, background: 'rgba(255,255,255,0.15)',
  },
  signupBtn: {
    color: C.deep, fontSize: 13, fontWeight: 800, textDecoration: 'none',
    padding: '7px 14px', borderRadius: 6, background: C.accent,
    boxShadow: '0 1px 3px rgba(0,0,0,0.25)',
  },
  hero: {
    maxWidth: 900, margin: '0 auto', padding: '64px 20px 40px',
    textAlign: 'center',
  },
  h1: { fontSize: 40, lineHeight: 1.15, margin: '0 0 18px', fontWeight: 800 },
  lead: {
    fontSize: 17.5, lineHeight: 1.6, color: C.sub, maxWidth: 720,
    margin: '0 auto 30px',
  },
  ctaRow: {
    display: 'flex', gap: 28, justifyContent: 'center', flexWrap: 'wrap',
    alignItems: 'flex-start',
  },
  ctaCol: { display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 300 },
  ctaPrimary: {
    background: C.accent, color: '#FFF', fontWeight: 800, fontSize: 15.5,
    padding: '13px 26px', borderRadius: 8, textDecoration: 'none',
    boxShadow: '0 2px 6px rgba(243,156,18,0.4)',
  },
  ctaSecondary: {
    background: '#FFF', color: C.deep, fontWeight: 700, fontSize: 15.5,
    padding: '12px 26px', borderRadius: 8, textDecoration: 'none',
    border: `2px solid ${C.deep}`,
  },
  ctaHint: { fontSize: 12.5, color: C.sub, lineHeight: 1.5 },
  section: { maxWidth: 1000, margin: '0 auto', padding: '46px 20px' },
  h2: { fontSize: 26, margin: '0 0 10px', fontWeight: 800 },
  para: { fontSize: 15, lineHeight: 1.65, maxWidth: 820, margin: '0 0 20px' },
  tech: { color: C.sub },
  svg: { width: '100%', height: 'auto', display: 'block', margin: '10px 0' },
  stepsRow: {
    display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
    gap: 20, marginTop: 22,
  },
  step: { display: 'flex', gap: 12, alignItems: 'flex-start' },
  stepN: {
    background: C.deep, color: '#FFF', borderRadius: '50%', width: 30,
    height: 30, display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontWeight: 800, flexShrink: 0, fontSize: 14,
  },
  stepTitle: { fontWeight: 700, fontSize: 14.5, marginBottom: 4 },
  stepBody: { fontSize: 13, color: C.sub, lineHeight: 1.55 },
  grid: {
    display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(290px, 1fr))',
    gap: 16, marginTop: 18,
  },
  card: {
    background: '#FFF', border: `1px solid ${C.line}`, borderRadius: 10,
    padding: '16px 18px',
  },
  cardBiz: { fontWeight: 800, fontSize: 15, marginBottom: 6 },
  cardTech: { fontSize: 12.5, color: C.sub, lineHeight: 1.55 },
  proof: {
    background: C.deep, display: 'flex', justifyContent: 'center',
    gap: 50, padding: '38px 20px', flexWrap: 'wrap',
  },
  proofItem: { maxWidth: 260, textAlign: 'center' },
  proofBig: { color: C.accent, fontSize: 34, fontWeight: 800 },
  proofSmall: { color: 'rgba(255,255,255,0.85)', fontSize: 12.5, lineHeight: 1.55 },
  footer: { background: C.ink, padding: '30px 20px' },
  footerInner: {
    maxWidth: 1080, margin: '0 auto', display: 'flex',
    justifyContent: 'space-between', gap: 20, flexWrap: 'wrap',
    alignItems: 'center',
  },
  footerBrand: { color: '#FFF', fontWeight: 800, fontSize: 16 },
  footerSub: { color: 'rgba(255,255,255,0.55)', fontSize: 11.5, marginTop: 4 },
  footerLinks: { display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' },
  footerCta: {
    background: C.accent, color: '#FFF', fontWeight: 700, fontSize: 12.5,
    padding: '7px 14px', borderRadius: 6, textDecoration: 'none',
  },
  footerLink: { color: 'rgba(255,255,255,0.8)', fontSize: 12.5, textDecoration: 'none' },
  footerNiw: {
    color: '#FFF', fontSize: 12.5, textDecoration: 'none', fontWeight: 700,
    border: '1px solid rgba(255,255,255,0.4)', borderRadius: 6, padding: '6px 10px',
  },
};
