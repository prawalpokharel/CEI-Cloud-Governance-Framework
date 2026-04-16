import Head from 'next/head';
import Link from 'next/link';
import {
  PATENT,
  REPO,
  FEDERAL_PROGRAMS,
  DEFENSE_APPLICATIONS,
  IMPACT_FIGURES,
  REPRODUCIBILITY,
  PUBLICATIONS,
} from '../lib/nationalInterestData';

/**
 * /national-interest
 *
 * A single dedicated page for USCIS officers, attorneys, or reviewers
 * evaluating the CloudOptimizer / CEI framework under the Dhanasar
 * National Interest Waiver framework.
 *
 * Sections map to the three Dhanasar prongs:
 *   1. Substantial merit + national importance  → Federal Programs, Impact
 *   2. Well positioned to advance the endeavor  → Reproducibility, Patent
 *   3. Beneficial to the U.S. to waive labor cert → Defense Applications
 */
export default function NationalInterestPage() {
  return (
    <>
      <Head>
        <title>National Interest — CloudOptimizer / CEI Framework</title>
        <meta
          name="description"
          content="How the Centrality-Entropy Index framework advances U.S. federal cloud governance, defense resilience, and national-security compute priorities."
        />
      </Head>

      <div style={styles.page}>
        <header style={styles.header}>
          <div style={styles.headerInner}>
            <div>
              <div style={styles.eyebrow}>For Reviewers · USCIS / Counsel</div>
              <h1 style={styles.title}>
                National Interest &amp; Federal Relevance
              </h1>
              <p style={styles.subtitle}>
                Centrality-Entropy Index (CEI) Framework —{' '}
                <a href={PATENT.uspto_url} style={styles.headerLink}>
                  USPTO App. No. {PATENT.application_no}
                </a>
              </p>
            </div>
            <Link href="/" style={styles.homeLink}>
              ← Home
            </Link>
          </div>
        </header>

        <section style={styles.lede}>
          <div style={styles.container}>
            <p style={styles.ledeText}>
              This page summarizes the federal programs, defense applications,
              and quantified impact advanced by the CEI framework. Each claim
              below is paired with a publicly verifiable source link so a
              reviewer can independently confirm the reference.
            </p>
            <p style={styles.ledeText}>
              Every component is reproducible: the full patent specification,
              source code, reference datasets, and a live deployed instance
              are linked in the{' '}
              <a href="#reproducibility" style={styles.inlineLink}>
                Reproducibility
              </a>{' '}
              section.
            </p>
          </div>
        </section>

        {/* ---------- Federal Programs ---------- */}
        <section style={styles.sectionLight}>
          <div style={styles.container}>
            <div style={styles.sectionHead}>
              <div style={styles.prong}>Prong 1 · Substantial Merit &amp; National Importance</div>
              <h2 style={styles.h2}>Federal programs this framework directly supports</h2>
              <p style={styles.sectionLede}>
                The CEI framework is not speculative — it maps to named,
                budgeted federal programs and standing executive orders. Each
                entry below identifies the specific governance or
                resource-allocation problem the framework addresses.
              </p>
            </div>

            <div style={styles.programsGrid}>
              {FEDERAL_PROGRAMS.map((p) => (
                <article key={p.name} style={styles.programCard}>
                  <div style={styles.programAgency}>{p.agency}</div>
                  <h3 style={styles.programName}>{p.name}</h3>
                  <p style={styles.programText}>{p.relevance}</p>
                  <a
                    href={p.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={styles.programLink}
                  >
                    View authoritative source →
                  </a>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* ---------- Defense Applications ---------- */}
        <section style={styles.sectionDark}>
          <div style={styles.container}>
            <div style={styles.sectionHead}>
              <div style={{ ...styles.prong, color: '#AED6F1' }}>
                Prong 3 · Beneficial to Waive Labor Certification
              </div>
              <h2 style={{ ...styles.h2, color: 'white' }}>
                Defense &amp; national-security applications
              </h2>
              <p style={{ ...styles.sectionLede, color: '#D5DBDB' }}>
                Three live demonstration scenarios exercise the framework on
                structurally distinct classes of national-security
                infrastructure. Each is runnable in one click.
              </p>
            </div>

            <div style={styles.defenseGrid}>
              {DEFENSE_APPLICATIONS.map((d) => (
                <article key={d.scenario_id} style={styles.defenseCard}>
                  <h3 style={styles.defenseTitle}>{d.label}</h3>
                  <p style={styles.defenseSummary}>{d.summary}</p>
                  <div style={styles.defenseMatters}>
                    <div style={styles.defenseMattersLabel}>
                      Why it matters
                    </div>
                    <p style={styles.defenseMattersText}>{d.why_it_matters}</p>
                  </div>
                  <Link
                    href={`/demo/${d.scenario_id}`}
                    style={styles.defenseCta}
                  >
                    Run live demo →
                  </Link>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* ---------- Impact Figures ---------- */}
        <section style={styles.sectionLight}>
          <div style={styles.container}>
            <div style={styles.sectionHead}>
              <div style={styles.prong}>Prong 1 · Quantified Impact</div>
              <h2 style={styles.h2}>Scale of the addressable problem</h2>
              <p style={styles.sectionLede}>
                Every figure below is paired with a publicly verifiable
                source. Ranges are conservative and exclude second-order
                effects (cascade-failure avoidance, oscillation-suppression
                downtime savings).
              </p>
            </div>

            <div style={styles.impactGrid}>
              {IMPACT_FIGURES.map((f) => (
                <div key={f.label} style={styles.impactCard}>
                  <div style={styles.impactValue}>{f.value}</div>
                  <div style={styles.impactLabel}>{f.label}</div>
                  <p style={styles.impactDetail}>{f.detail}</p>
                  <a
                    href={f.source_url}
                    target={f.source_url.startsWith('http') ? '_blank' : undefined}
                    rel="noopener noreferrer"
                    style={styles.impactSource}
                  >
                    {f.source_label} →
                  </a>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ---------- Reproducibility ---------- */}
        <section id="reproducibility" style={styles.sectionLight}>
          <div style={styles.container}>
            <div style={styles.sectionHead}>
              <div style={styles.prong}>Prong 2 · Well Positioned to Advance the Endeavor</div>
              <h2 style={styles.h2}>Reproducibility surface</h2>
              <p style={styles.sectionLede}>
                A reviewer can independently verify every claim on this page
                using the artifacts below. No login is required for the demo
                scenarios.
              </p>
            </div>

            <ul style={styles.reproList}>
              {REPRODUCIBILITY.map((r) => (
                <li key={r.label} style={styles.reproItem}>
                  <a
                    href={r.url}
                    target={r.url.startsWith('http') ? '_blank' : undefined}
                    rel="noopener noreferrer"
                    style={styles.reproLink}
                  >
                    {r.label}
                  </a>
                  <div style={styles.reproDetail}>{r.detail}</div>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* ---------- Publications (optional, renders only if URLs present) ---------- */}
        {PUBLICATIONS.some((p) => p.url) && (
          <section style={styles.sectionLight}>
            <div style={styles.container}>
              <div style={styles.sectionHead}>
                <h2 style={styles.h2}>Publications</h2>
              </div>
              <ul style={styles.reproList}>
                {PUBLICATIONS.filter((p) => p.url).map((p, i) => (
                  <li key={i} style={styles.reproItem}>
                    <a
                      href={p.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={styles.reproLink}
                    >
                      {p.citation}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          </section>
        )}

        {/* ---------- Patent Block ---------- */}
        <section style={styles.patentBlock}>
          <div style={styles.container}>
            <h2 style={{ ...styles.h2, color: 'white', marginBottom: 16 }}>
              Patent &amp; attribution
            </h2>
            <div style={styles.patentGrid}>
              <div style={styles.patentCell}>
                <div style={styles.patentLabel}>Application</div>
                <div style={styles.patentVal}>{PATENT.application_no}</div>
              </div>
              <div style={styles.patentCell}>
                <div style={styles.patentLabel}>Filed</div>
                <div style={styles.patentVal}>{PATENT.filed}</div>
              </div>
              <div style={styles.patentCell}>
                <div style={styles.patentLabel}>Priority (Provisional)</div>
                <div style={styles.patentVal}>
                  {PATENT.priority_provisional}
                </div>
              </div>
              <div style={styles.patentCell}>
                <div style={styles.patentLabel}>Priority Date</div>
                <div style={styles.patentVal}>{PATENT.priority_date}</div>
              </div>
            </div>
            <p style={styles.patentTitle}>{PATENT.title}</p>
            <div style={styles.patentLinks}>
              <a
                href={PATENT.uspto_url}
                target="_blank"
                rel="noopener noreferrer"
                style={styles.patentLinkBtn}
              >
                USPTO Patent Center →
              </a>
              <a
                href={REPO.url}
                target="_blank"
                rel="noopener noreferrer"
                style={styles.patentLinkBtn}
              >
                GitHub Repository →
              </a>
            </div>
          </div>
        </section>

        <footer style={styles.footer}>
          <div>
            CloudOptimizer · CEI Framework · Prawal Pokharel
          </div>
          <div style={styles.footerLinks}>
            <Link href="/" style={styles.footerLink}>Home</Link>
            <Link href="/demo" style={styles.footerLink}>Live Demo</Link>
            <a href={REPO.url} style={styles.footerLink}>GitHub</a>
          </div>
        </footer>
      </div>
    </>
  );
}

const styles = {
  page: {
    minHeight: '100vh',
    background: '#F8F9FA',
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    color: '#1C2833',
  },
  container: { maxWidth: 1100, margin: '0 auto', padding: '0 16px' },

  header: {
    background: 'linear-gradient(135deg, #1B4F72 0%, #2874A6 100%)',
    color: 'white',
    padding: '32px 0 24px 0',
  },
  headerInner: {
    maxWidth: 1100,
    margin: '0 auto',
    padding: '0 16px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    flexWrap: 'wrap',
    gap: 16,
  },
  eyebrow: {
    fontSize: 11,
    letterSpacing: '1.5px',
    textTransform: 'uppercase',
    opacity: 0.8,
    marginBottom: 6,
  },
  title: {
    margin: 0,
    fontSize: 'clamp(24px, 5vw, 34px)',
    fontWeight: 700,
    letterSpacing: '-0.5px',
    lineHeight: 1.2,
  },
  subtitle: { margin: '10px 0 0 0', fontSize: 'clamp(13px, 2.5vw, 15px)', opacity: 0.92 },
  headerLink: { color: '#AED6F1', textDecoration: 'underline' },
  homeLink: {
    color: 'white',
    textDecoration: 'none',
    fontSize: 13,
    opacity: 0.9,
    padding: '6px 12px',
    border: '1px solid rgba(255,255,255,0.3)',
    borderRadius: 4,
  },

  lede: { background: 'white', padding: '28px 0', borderBottom: '1px solid #E8EDF0' },
  ledeText: {
    fontSize: 15,
    lineHeight: 1.7,
    color: '#34495E',
    margin: '0 0 12px 0',
  },
  inlineLink: { color: '#2874A6', textDecoration: 'underline' },

  sectionLight: { padding: '40px 0', background: '#F8F9FA' },
  sectionDark: {
    padding: '40px 0',
    background: 'linear-gradient(180deg, #1C2833 0%, #283747 100%)',
  },
  sectionHead: { marginBottom: 32, maxWidth: 820 },
  prong: {
    fontSize: 11,
    letterSpacing: '1.5px',
    textTransform: 'uppercase',
    color: '#2874A6',
    fontWeight: 600,
    marginBottom: 8,
  },
  h2: {
    fontSize: 'clamp(20px, 4vw, 26px)',
    fontWeight: 700,
    color: '#1B4F72',
    margin: '0 0 12px 0',
    lineHeight: 1.25,
    letterSpacing: '-0.3px',
  },
  sectionLede: {
    fontSize: 15,
    lineHeight: 1.65,
    color: '#566573',
    margin: 0,
  },

  programsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
    gap: 16,
  },
  programCard: {
    background: 'white',
    borderRadius: 8,
    padding: 22,
    border: '1px solid #E8EDF0',
    boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
    display: 'flex',
    flexDirection: 'column',
  },
  programAgency: {
    fontSize: 11,
    letterSpacing: '0.8px',
    textTransform: 'uppercase',
    color: '#7B8A8B',
    marginBottom: 6,
  },
  programName: {
    fontSize: 17,
    fontWeight: 600,
    color: '#1B4F72',
    margin: '0 0 10px 0',
    lineHeight: 1.3,
  },
  programText: {
    fontSize: 14,
    lineHeight: 1.6,
    color: '#34495E',
    margin: '0 0 14px 0',
    flex: 1,
  },
  programLink: {
    fontSize: 13,
    fontWeight: 600,
    color: '#2874A6',
    textDecoration: 'none',
  },

  defenseGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
    gap: 16,
  },
  defenseCard: {
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.15)',
    borderRadius: 8,
    padding: 24,
    color: 'white',
    display: 'flex',
    flexDirection: 'column',
  },
  defenseTitle: {
    fontSize: 18,
    fontWeight: 600,
    color: '#AED6F1',
    margin: '0 0 10px 0',
  },
  defenseSummary: {
    fontSize: 14,
    lineHeight: 1.6,
    color: '#D5DBDB',
    margin: '0 0 16px 0',
  },
  defenseMatters: {
    background: 'rgba(0,0,0,0.25)',
    borderLeft: '3px solid #5499C7',
    padding: '10px 12px',
    borderRadius: 3,
    marginBottom: 16,
  },
  defenseMattersLabel: {
    fontSize: 10,
    letterSpacing: '1.2px',
    textTransform: 'uppercase',
    color: '#85C1E2',
    marginBottom: 4,
  },
  defenseMattersText: {
    fontSize: 13,
    lineHeight: 1.55,
    color: '#EAEDED',
    margin: 0,
  },
  defenseCta: {
    fontSize: 13,
    fontWeight: 600,
    color: '#AED6F1',
    textDecoration: 'none',
    marginTop: 'auto',
  },

  impactGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
    gap: 16,
  },
  impactCard: {
    background: 'white',
    border: '1px solid #E8EDF0',
    borderRadius: 8,
    padding: 22,
    borderTop: '4px solid #2874A6',
  },
  impactValue: {
    fontSize: 30,
    fontWeight: 700,
    color: '#1B4F72',
    letterSpacing: '-0.5px',
  },
  impactLabel: {
    fontSize: 13,
    fontWeight: 600,
    color: '#34495E',
    marginTop: 4,
    marginBottom: 10,
  },
  impactDetail: {
    fontSize: 12.5,
    lineHeight: 1.55,
    color: '#566573',
    margin: '0 0 12px 0',
  },
  impactSource: {
    fontSize: 12,
    fontWeight: 600,
    color: '#2874A6',
    textDecoration: 'none',
  },

  reproList: { listStyle: 'none', padding: 0, margin: 0 },
  reproItem: {
    background: 'white',
    border: '1px solid #E8EDF0',
    borderRadius: 6,
    padding: '16px 18px',
    marginBottom: 10,
  },
  reproLink: {
    fontSize: 15,
    fontWeight: 600,
    color: '#2874A6',
    textDecoration: 'none',
  },
  reproDetail: {
    fontSize: 13,
    color: '#566573',
    marginTop: 4,
    lineHeight: 1.55,
  },

  patentBlock: {
    background: '#1C2833',
    color: 'white',
    padding: '32px 0',
  },
  patentGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
    gap: 12,
    marginBottom: 20,
  },
  patentCell: {
    background: 'rgba(255,255,255,0.05)',
    padding: '12px 14px',
    borderRadius: 4,
    border: '1px solid rgba(255,255,255,0.1)',
  },
  patentLabel: {
    fontSize: 10,
    letterSpacing: '1.2px',
    textTransform: 'uppercase',
    color: '#85C1E2',
    marginBottom: 4,
  },
  patentVal: { fontSize: 14, fontWeight: 600 },
  patentTitle: {
    fontSize: 13,
    color: '#BDC3C7',
    fontStyle: 'italic',
    lineHeight: 1.5,
    margin: '0 0 20px 0',
  },
  patentLinks: { display: 'flex', gap: 12, flexWrap: 'wrap' },
  patentLinkBtn: {
    padding: '10px 18px',
    background: '#2874A6',
    color: 'white',
    textDecoration: 'none',
    borderRadius: 4,
    fontSize: 13,
    fontWeight: 600,
  },

  footer: {
    background: '#17202A',
    color: '#BDC3C7',
    padding: '20px 16px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: 13,
    flexWrap: 'wrap',
    gap: 12,
  },
  footerLinks: { display: 'flex', gap: 16, flexWrap: 'wrap' },
  footerLink: { color: '#85C1E2', textDecoration: 'none' },
};
