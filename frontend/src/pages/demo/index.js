import { useState, useEffect } from 'react';
import Link from 'next/link';
import Head from 'next/head';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001';

const SCENARIO_ICONS = {
  cloud: '☁',
  chip: '▣',
  drone: '✈',
  waves: '≈',
  shield: '⬢',
};

const DOMAIN_COLORS = {
  'Commercial Cloud': { bg: '#EBF5FB', border: '#3498DB', text: '#1B4F72' },
  'GPU Computing': { bg: '#FDEDEC', border: '#E74C3C', text: '#922B21' },
  'Edge / Tactical': { bg: '#E8F8F5', border: '#16A085', text: '#0E6655' },
  'Subsurface / GPS-Denied': { bg: '#EAEDED', border: '#34495E', text: '#1B2631' },
  'Strategic Communications': { bg: '#FEF9E7', border: '#B7950B', text: '#7D6608' },
};

export default function DemoIndex() {
  const [scenarios, setScenarios] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/demo/scenarios`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        setScenarios(data.scenarios || []);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, []);

  return (
    <>
      <Head>
        <title>CloudOptimizer — CEI Framework Live Demo</title>
        <meta
          name="description"
          content="Interactive demonstration of the Centrality-Entropy Index framework across five application domains"
        />
      </Head>

      <div style={styles.page}>
        <header style={styles.header}>
          <div style={styles.headerInner}>
            <div>
              <h1 style={styles.title}>CloudOptimizer</h1>
              <p style={styles.subtitle}>
                Centrality-Entropy Index Framework — Live Demonstration
              </p>
            </div>
            <div style={styles.patentBadge}>
              <div style={styles.patentLabel}>USPTO</div>
              <div style={styles.patentNumber}>App. No. 19/641,446</div>
            </div>
          </div>
        </header>

        <section style={styles.intro}>
          <div style={styles.introInner}>
            <h2 style={styles.introTitle}>
              Five scenarios. One decision framework.
            </h2>
            <p style={styles.introText}>
              Each scenario below exercises the CEI framework on a structurally
              distinct class of resource-allocation problem — from commercial
              cloud microservices to tactical drone swarms, GPU training
              clusters, underwater positioning networks, and strategic
              communications infrastructure. The framework produces sensible
              decisions across all five, demonstrating operation at the level
              of system structure rather than application domain.
            </p>
            <p style={styles.introText}>
              Select any scenario to see live CEI analysis: dependency graph,
              per-node centrality/entropy/governance scores, oscillation
              suppression, fault propagation analysis, and HPA-vs-CEI
              comparison. No login required.
            </p>

            <div style={styles.provenance}>
              <div style={styles.provenanceTitle}>Data provenance</div>
              <p style={styles.provenanceText}>
                Each scenario is a <strong>synthetic dataset</strong> whose
                topology, tier assignments, governance constraints, and
                telemetry envelopes are derived from <strong>publicly
                published reference architectures and operational research</strong>{' '}
                in that domain. Sources include the AWS Well-Architected
                Framework (commercial cloud), NVIDIA DGX SuperPOD reference
                architecture and MLCommons MLPerf (GPU clusters), DARPA
                OFFSET / CODE and USAF Skyborg (drone swarm), NATO CMRE and
                US Navy ONR (underwater acoustic positioning), and DoD
                NC3 / RAND modernization studies (strategic communications).
                Per-scenario citations appear on each detail page.
              </p>
              <p style={{ ...styles.provenanceText, marginTop: 8 }}>
                The same datasets are reproduced in{' '}
                <a
                  href="https://github.com/prawalpokharel/CEI-Cloud-Governance-Framework/tree/main/core-engine/scenarios"
                  style={styles.provenanceLink}
                  target="_blank"
                  rel="noreferrer"
                >
                  /core-engine/scenarios
                </a>{' '}
                and referenced as worked examples in USPTO App. 19/641,446.
                Numbers are constructed to exercise the CEI pipeline across
                structurally distinct workloads — they are not measurements
                of any single production system.
              </p>
            </div>
          </div>
        </section>

        <section style={styles.grid}>
          {loading && <div style={styles.loading}>Loading scenarios…</div>}
          {error && (
            <div style={styles.error}>
              Unable to load scenarios: {error}
              <div style={styles.errorHint}>
                Is the backend running at {API_BASE}?
              </div>
            </div>
          )}
          {!loading &&
            !error &&
            scenarios.map((s) => {
              const colors =
                DOMAIN_COLORS[s.domain] || DOMAIN_COLORS['Commercial Cloud'];
              return (
                <Link
                  key={s.scenario_id}
                  href={`/demo/${s.scenario_id}`}
                  style={{ textDecoration: 'none' }}
                >
                  <article
                    style={{
                      ...styles.card,
                      borderTop: `4px solid ${colors.border}`,
                    }}
                  >
                    <div style={styles.cardHeader}>
                      <span
                        style={{ ...styles.cardIcon, color: colors.border }}
                      >
                        {SCENARIO_ICONS[s.icon] || '●'}
                      </span>
                      <span
                        style={{
                          ...styles.domainTag,
                          background: colors.bg,
                          color: colors.text,
                        }}
                      >
                        {s.domain}
                      </span>
                    </div>
                    <h3 style={styles.cardTitle}>{s.display_name}</h3>
                    <p style={styles.cardDesc}>{s.description}</p>
                    <div style={styles.cardFooter}>
                      <span style={styles.cardStat}>
                        <strong>{s.node_count}</strong> nodes
                      </span>
                      <span style={styles.cardStat}>
                        <strong>{s.edge_count}</strong> edges
                      </span>
                      <span style={styles.cardStat}>
                        Patent §
                        {(s.patent_sections || []).join(', ')}
                      </span>
                    </div>
                    <div style={styles.cardAction}>
                      Run Analysis →
                    </div>
                  </article>
                </Link>
              );
            })}
        </section>

        <footer style={styles.footer}>
          <div>
            CloudOptimizer · Patent-pending CEI framework · Prawal Pokharel
          </div>
          <div style={styles.footerLinks}>
            <Link href="/" style={styles.footerLink}>
              Home
            </Link>
            <a
              href="https://github.com/prawalpokharel/CEI-Cloud-Governance-Framework"
              style={styles.footerLink}
            >
              GitHub
            </a>
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
  header: {
    background: 'linear-gradient(135deg, #1B4F72 0%, #2874A6 100%)',
    color: 'white',
    padding: '32px 0',
  },
  headerInner: {
    maxWidth: 1200,
    margin: '0 auto',
    padding: '0 32px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 16,
  },
  title: { margin: 0, fontSize: 36, fontWeight: 700, letterSpacing: '-0.5px' },
  subtitle: { margin: '6px 0 0 0', fontSize: 16, opacity: 0.9 },
  patentBadge: {
    background: 'rgba(255,255,255,0.15)',
    padding: '10px 16px',
    borderRadius: 6,
    border: '1px solid rgba(255,255,255,0.25)',
  },
  patentLabel: { fontSize: 11, opacity: 0.8, letterSpacing: '1px' },
  patentNumber: { fontSize: 14, fontWeight: 600, marginTop: 2 },
  intro: { background: 'white', padding: '48px 0', borderBottom: '1px solid #E8EDF0' },
  introInner: { maxWidth: 900, margin: '0 auto', padding: '0 32px' },
  provenance: {
    background: '#FEF9E7',
    border: '1px solid #B7950B',
    borderLeft: '4px solid #B7950B',
    borderRadius: 6,
    padding: '14px 18px',
    marginTop: 20,
  },
  provenanceTitle: {
    fontSize: 11,
    fontWeight: 700,
    color: '#7D6608',
    textTransform: 'uppercase',
    letterSpacing: '1px',
    marginBottom: 6,
  },
  provenanceText: {
    margin: 0,
    fontSize: 13,
    lineHeight: 1.6,
    color: '#5D4E0A',
  },
  provenanceLink: {
    color: '#1B4F72',
    fontWeight: 600,
    textDecoration: 'underline',
  },
  introTitle: {
    fontSize: 24,
    fontWeight: 600,
    color: '#1B4F72',
    margin: '0 0 16px 0',
  },
  introText: {
    fontSize: 15,
    lineHeight: 1.7,
    color: '#34495E',
    margin: '0 0 12px 0',
  },
  grid: {
    maxWidth: 1200,
    margin: '0 auto',
    padding: '48px 32px',
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
    gap: 20,
  },
  card: {
    background: 'white',
    borderRadius: 8,
    padding: 24,
    boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
    cursor: 'pointer',
    transition: 'transform 0.15s, box-shadow 0.15s',
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
  },
  cardHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 14,
  },
  cardIcon: { fontSize: 32, fontWeight: 700 },
  domainTag: {
    fontSize: 11,
    fontWeight: 600,
    padding: '4px 10px',
    borderRadius: 12,
    letterSpacing: '0.3px',
  },
  cardTitle: {
    fontSize: 18,
    fontWeight: 600,
    color: '#1B4F72',
    margin: '0 0 10px 0',
    lineHeight: 1.3,
  },
  cardDesc: {
    fontSize: 13,
    lineHeight: 1.55,
    color: '#566573',
    margin: '0 0 16px 0',
    flex: 1,
  },
  cardFooter: {
    display: 'flex',
    gap: 14,
    flexWrap: 'wrap',
    paddingTop: 12,
    borderTop: '1px solid #EAEDED',
    marginBottom: 12,
  },
  cardStat: { fontSize: 12, color: '#7B8A8B' },
  cardAction: {
    fontSize: 13,
    fontWeight: 600,
    color: '#2874A6',
    marginTop: 'auto',
  },
  loading: { textAlign: 'center', padding: 40, color: '#7B8A8B' },
  error: {
    textAlign: 'center',
    padding: 40,
    color: '#922B21',
    background: '#FDEDEC',
    borderRadius: 6,
    gridColumn: '1 / -1',
  },
  errorHint: { fontSize: 12, marginTop: 8, color: '#7B8A8B' },
  footer: {
    background: '#1C2833',
    color: '#BDC3C7',
    padding: '24px 32px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: 13,
    flexWrap: 'wrap',
    gap: 12,
  },
  footerLinks: { display: 'flex', gap: 20 },
  footerLink: { color: '#85C1E2', textDecoration: 'none' },
};
