import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import Head from 'next/head';
import SCENARIO_SOURCES from '../../lib/scenarioSources';
import CostSavingsPanel from '../../components/dashboard/CostSavingsPanel';
import HpaVsCeiBenchmark from '../../components/dashboard/HpaVsCeiBenchmark';
import D3DependencyGraph from '../../components/visualization/D3DependencyGraph';
import CEIBreakdownChart from '../../components/dashboard/CEIBreakdownChart';
import OscillationTimeline from '../../components/dashboard/OscillationTimeline';
import FaultPropagationView from '../../components/dashboard/FaultPropagationView';
import ComplianceHeatmap from '../../components/dashboard/ComplianceHeatmap';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001';

const TOUR_STEPS = [
  {
    title: 'Scenario loaded',
    body: 'Topology, governance policies, and 180-point telemetry time-series loaded from /scenarios/. This exercises Patent Module 101 (Data Collection).',
  },
  {
    title: 'Dependency graph constructed',
    body: 'Nodes and edges form the dependency graph G=(V,E). This is Patent Module 103 (Graph Constructor).',
  },
  {
    title: 'CEI scores computed',
    body: 'For each node: Centrality (PageRank) · Entropy (Shannon over utilization) · Governance risk (tier + policy derived). Combined: CEI = α·C + β·H + γ·R. This is Patent Module 106.',
  },
  {
    title: 'Adaptive weights applied',
    body: 'Weights α, β, γ adjusted based on observed stability and dependency change. Patent Module 107 — closed-loop recalibration.',
  },
  {
    title: 'Oscillation analyzed',
    body: 'Per-node scaling-event history inspected for flip-flop patterns. Suppression windows enforced where reversal count exceeds threshold. Patent Module 108.',
  },
  {
    title: 'Fault propagation simulated',
    body: 'k-hop cascade risk computed for each candidate modification. Modifications exceeding Ψ_max are aborted. Patent Modules 109 + 110.',
  },
  {
    title: 'HPA comparison rendered',
    body: 'Side-by-side view of threshold-based autoscaler decisions versus CEI decisions on the same workload. Demonstrates the limitations of prior art described in the patent Background section.',
  },
];

export default function ScenarioDetail() {
  const router = useRouter();
  const { scenario: scenarioId } = router.query;

  const [scenario, setScenario] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [tourStep, setTourStep] = useState(0);
  const [tourActive, setTourActive] = useState(false);
  const [savings, setSavings] = useState(null);
  const [benchmark, setBenchmark] = useState(null);

  // Core engine base URL — used for direct calls to /pricing/* and
  // /benchmark/* endpoints which don't need the auth-bearing backend.
  const CORE_BASE =
    process.env.NEXT_PUBLIC_CORE_ENGINE_URL || 'http://localhost:8000';

  useEffect(() => {
    if (!scenarioId) return;
    fetch(`${API_BASE}/api/demo/scenarios/${scenarioId}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setScenario)
      .catch((e) => setError(e.message));
  }, [scenarioId]);

  const runAnalysis = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/demo/scenarios/${scenarioId}/analyze`,
        { method: 'POST' }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setAnalysis(data.analysis);
      setTourActive(true);
      setTourStep(0);

      // Fire-and-forget the savings + benchmark calls once analysis lands.
      // Failures here don't break the analysis view — the UI just hides
      // the panels until they succeed.
      runSavingsAndBenchmark(data.analysis);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  const runSavingsAndBenchmark = async (analysisData) => {
    if (!scenario || !analysisData) return;
    const topoNodes = (scenario.topology?.nodes || []).map((n) => ({
      id: n.id,
      provider: n.provider || 'aws',
      instance_type: n.instance_type,
      replicas: n.replicas || 1,
      tier: n.tier || 'supporting',
    }));
    const analysisNodes = analysisData.nodes || [];
    try {
      const savRes = await fetch(`${CORE_BASE}/pricing/savings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          nodes: topoNodes,
          analysis_nodes: analysisNodes,
          governance: scenario.governance,
        }),
      });
      if (savRes.ok) setSavings(await savRes.json());
    } catch {
      /* leave savings null */
    }
    try {
      const benchRes = await fetch(`${CORE_BASE}/benchmark/hpa-vs-cei`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          nodes: topoNodes,
          edges: scenario.topology?.edges || [],
          analysis_nodes: analysisNodes,
          oscillation_status: analysisData.oscillation_status,
          governance: scenario.governance,
        }),
      });
      if (benchRes.ok) setBenchmark(await benchRes.json());
    } catch {
      /* leave benchmark null */
    }
  };

  if (!scenario && !error) {
    return <div style={styles.loading}>Loading scenario…</div>;
  }

  if (error && !scenario) {
    return (
      <div style={styles.loading}>
        Error: {error}
        <div>
          <Link href="/demo">← Back to scenarios</Link>
        </div>
      </div>
    );
  }

  const { metadata, topology, governance } = scenario;

  return (
    <>
      <Head>
        <title>{metadata.display_name} — CloudOptimizer Demo</title>
      </Head>
      <div style={styles.page}>
        <header style={styles.header}>
          <div style={styles.headerInner}>
            <div style={styles.backLinks}>
              <Link href="/" style={styles.backLink}>
                ← Home
              </Link>
              <Link href="/demo" style={styles.backLink}>
                ← All scenarios
              </Link>
            </div>
            <h1 style={styles.title}>{metadata.display_name}</h1>
            <div style={styles.meta}>
              <span>Domain: {metadata.domain}</span>
              <span>
                Patent §{(metadata.patent_sections || []).join(', ')}
              </span>
              <span>
                {topology.nodes.length} nodes · {topology.edges.length} edges
              </span>
            </div>
          </div>
        </header>

        <div style={styles.body}>
          <aside style={styles.sidebar}>
            <h3 style={styles.sidebarTitle}>About this scenario</h3>
            <p style={styles.sidebarText}>{metadata.description}</p>

            <h4 style={styles.sidebarSubtitle}>Governance tiers</h4>
            <ul style={styles.tierList}>
              {Object.keys(governance.tiers || {}).map((t) => (
                <li key={t} style={styles.tierItem}>
                  <strong>{t}</strong>
                </li>
              ))}
            </ul>

            <h4 style={styles.sidebarSubtitle}>Governance policies</h4>
            <div style={styles.policyCount}>
              {(governance.policies || []).length} policies loaded
            </div>

            <h4 style={styles.sidebarSubtitle}>Data source</h4>
            <div style={styles.sourceBlock}>
              {SCENARIO_SOURCES[scenarioId] ? (
                <>
                  <p style={styles.sourceLine}>
                    {SCENARIO_SOURCES[scenarioId].summary}
                  </p>
                  <p style={{ ...styles.sourceLine, marginTop: 8, fontWeight: 600, color: '#5D4E0A' }}>
                    Primary references:
                  </p>
                  <ul style={styles.sourceList}>
                    {SCENARIO_SOURCES[scenarioId].sources.map((s) => (
                      <li key={s.url} style={styles.sourceListItem}>
                        <a
                          href={s.url}
                          style={styles.sourceLink}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {s.label}
                        </a>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p style={styles.sourceLine}>
                  Synthetic / illustrative dataset.
                </p>
              )}
              <p
                style={{
                  ...styles.sourceLine,
                  marginTop: 10,
                  paddingTop: 10,
                  borderTop: '1px solid rgba(183,149,11,0.4)',
                  color: '#7B8A8B',
                  marginBottom: 0,
                }}
              >
                Reproduced in{' '}
                <a
                  href={`https://github.com/prawalpokharel/CEI-Cloud-Governance-Framework/tree/main/core-engine/scenarios/${scenarioId}`}
                  style={styles.sourceLink}
                  target="_blank"
                  rel="noreferrer"
                >
                  /core-engine/scenarios/{scenarioId}
                </a>{' '}
                and referenced in USPTO App. 19/641,446 §
                {(metadata.patent_sections || []).join(', §')} as a worked
                example. Numbers exercise the CEI pipeline; not measurements
                of any single production system.
              </p>
            </div>

            {!analysis && (
              <button
                onClick={runAnalysis}
                disabled={loading}
                style={{
                  ...styles.runButton,
                  opacity: loading ? 0.6 : 1,
                  cursor: loading ? 'wait' : 'pointer',
                }}
              >
                {loading ? 'Running CEI Analysis…' : 'Run CEI Analysis →'}
              </button>
            )}
          </aside>

          <main style={styles.main}>
            {!analysis && !loading && (
              <div style={styles.placeholder}>
                <div style={styles.placeholderIcon}>⚡</div>
                <h2 style={styles.placeholderTitle}>
                  Ready to analyze
                </h2>
                <p style={styles.placeholderText}>
                  Click "Run CEI Analysis" to execute the full pipeline
                  (Patent Modules 101-112) on this scenario's dataset. Results
                  will include per-node CEI scores, oscillation analysis,
                  cascade risk assessment, and HPA comparison.
                </p>
              </div>
            )}

            {loading && (
              <div style={styles.placeholder}>
                <div style={styles.placeholderIcon}>⏳</div>
                <h2 style={styles.placeholderTitle}>Running analysis…</h2>
                <p style={styles.placeholderText}>
                  Computing centrality, entropy, governance risk, oscillation,
                  and fault propagation across {topology.nodes.length} nodes.
                </p>
              </div>
            )}

            {error && analysis === null && !loading && (
              <div style={styles.errorBlock}>Analysis error: {error}</div>
            )}

            {analysis && (
              <>
                {tourActive && (
                  <div style={styles.tourCard}>
                    <div style={styles.tourHeader}>
                      <span style={styles.tourStep}>
                        Step {tourStep + 1} of {TOUR_STEPS.length}
                      </span>
                      <button
                        style={styles.tourClose}
                        onClick={() => setTourActive(false)}
                      >
                        ×
                      </button>
                    </div>
                    <h3 style={styles.tourTitle}>
                      {TOUR_STEPS[tourStep].title}
                    </h3>
                    <p style={styles.tourBody}>{TOUR_STEPS[tourStep].body}</p>
                    <div style={styles.tourNav}>
                      <button
                        onClick={() => setTourStep((s) => Math.max(0, s - 1))}
                        disabled={tourStep === 0}
                        style={styles.tourBtnSecondary}
                      >
                        ← Previous
                      </button>
                      {tourStep < TOUR_STEPS.length - 1 ? (
                        <button
                          onClick={() => setTourStep((s) => s + 1)}
                          style={styles.tourBtn}
                        >
                          Next →
                        </button>
                      ) : (
                        <button
                          onClick={() => setTourActive(false)}
                          style={styles.tourBtn}
                        >
                          Finish tour
                        </button>
                      )}
                    </div>
                  </div>
                )}

                <section style={styles.section}>
                  <h2 style={styles.sectionTitle}>Analysis Results</h2>
                  <div style={styles.metricsRow}>
                    <div style={styles.metricCard}>
                      <div style={styles.metricLabel}>Nodes Analyzed</div>
                      <div style={styles.metricValue}>
                        {analysis.nodes?.length || topology.nodes.length}
                      </div>
                    </div>
                    <div style={styles.metricCard}>
                      <div style={styles.metricLabel}>α Weight</div>
                      <div style={styles.metricValue}>
                        {analysis.weights?.alpha?.toFixed(2) || '—'}
                      </div>
                    </div>
                    <div style={styles.metricCard}>
                      <div style={styles.metricLabel}>β Weight</div>
                      <div style={styles.metricValue}>
                        {analysis.weights?.beta?.toFixed(2) || '—'}
                      </div>
                    </div>
                    <div style={styles.metricCard}>
                      <div style={styles.metricLabel}>γ Weight</div>
                      <div style={styles.metricValue}>
                        {analysis.weights?.gamma?.toFixed(2) || '—'}
                      </div>
                    </div>
                    <div style={styles.metricCard}>
                      <div style={styles.metricLabel}>Suppression</div>
                      <div style={styles.metricValue}>
                        {analysis.oscillation_status?.suppression_active
                          ? 'Active'
                          : 'Inactive'}
                      </div>
                    </div>
                  </div>
                </section>

                {savings && (
                  <section style={styles.section}>
                    <CostSavingsPanel savings={savings} />
                  </section>
                )}

                {benchmark && (
                  <section style={styles.section}>
                    <HpaVsCeiBenchmark benchmark={benchmark} />
                  </section>
                )}

                <section style={styles.section}>
                  <h2 style={styles.sectionTitle}>
                    Dependency Graph (Patent Module 103)
                  </h2>
                  <p style={styles.sectionSub}>
                    Force-directed view of the topology with CEI-classified
                    nodes. Node color = classification; node radius = centrality;
                    edge width = dependency weight. Hover for breakdown.
                  </p>
                  <D3DependencyGraph
                    topology={topology}
                    analysis={analysis}
                  />
                </section>

                <section style={styles.section}>
                  <CEIBreakdownChart
                    nodes={analysis.nodes || []}
                    weights={
                      analysis.weights || { alpha: 0.4, beta: 0.35, gamma: 0.25 }
                    }
                  />
                </section>

                <section style={styles.section}>
                  <OscillationTimeline
                    oscillationStatus={analysis.oscillation_status}
                    telemetry={scenario.telemetry}
                  />
                </section>

                <section style={styles.section}>
                  <FaultPropagationView
                    topology={topology}
                    analysis={analysis}
                  />
                </section>

                <section style={styles.section}>
                  <ComplianceHeatmap
                    topology={topology}
                    analysis={analysis}
                    governance={governance}
                  />
                </section>

                <section style={styles.section}>
                  <h2 style={styles.sectionTitle}>Per-Node CEI Scores</h2>
                  <div style={styles.tableWrap}>
                    <table style={styles.table}>
                      <thead>
                        <tr>
                          <th style={styles.th}>Node</th>
                          <th style={styles.th}>C</th>
                          <th style={styles.th}>H</th>
                          <th style={styles.th}>R</th>
                          <th style={styles.th}>CEI</th>
                          <th style={styles.th}>Classification</th>
                          <th style={styles.th}>Recommendation</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(analysis.nodes || []).map((n) => (
                          <tr key={n.node_id}>
                            <td style={styles.td}>
                              <strong>{n.node_id}</strong>
                            </td>
                            <td style={styles.td}>
                              {n.centrality?.toFixed(3) || '—'}
                            </td>
                            <td style={styles.td}>
                              {n.entropy?.toFixed(3) || '—'}
                            </td>
                            <td style={styles.td}>
                              {n.risk_factor?.toFixed(3) || '—'}
                            </td>
                            <td style={{ ...styles.td, fontWeight: 600 }}>
                              {n.cei_score?.toFixed(3) || '—'}
                            </td>
                            <td style={styles.td}>
                              <span
                                style={{
                                  ...styles.classificationBadge,
                                  background:
                                    n.classification === 'critical'
                                      ? '#FDEDEC'
                                      : n.classification === 'elevated'
                                      ? '#FEF9E7'
                                      : n.classification === 'moderate'
                                      ? '#EBF5FB'
                                      : '#EAFAF1',
                                  color:
                                    n.classification === 'critical'
                                      ? '#922B21'
                                      : n.classification === 'elevated'
                                      ? '#7D6608'
                                      : n.classification === 'moderate'
                                      ? '#1B4F72'
                                      : '#186A3B',
                                }}
                              >
                                {n.classification}
                              </span>
                            </td>
                            <td style={styles.td}>
                              <small>{n.recommendation}</small>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              </>
            )}
          </main>
        </div>
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
  loading: { padding: 40, textAlign: 'center' },
  header: {
    background: 'linear-gradient(135deg, #1B4F72 0%, #2874A6 100%)',
    color: 'white',
    padding: '24px 0',
  },
  headerInner: { maxWidth: 1300, margin: '0 auto', padding: '0 32px' },
  backLinks: {
    display: 'flex',
    gap: 16,
    marginBottom: 8,
  },
  backLink: {
    color: '#AED6F1',
    textDecoration: 'none',
    fontSize: 13,
    display: 'inline-block',
  },
  title: {
    margin: '6px 0 10px 0',
    fontSize: 28,
    fontWeight: 700,
  },
  meta: { fontSize: 13, opacity: 0.85, display: 'flex', gap: 20, flexWrap: 'wrap' },
  body: {
    maxWidth: 1300,
    margin: '0 auto',
    padding: '24px 32px',
    display: 'grid',
    gridTemplateColumns: '320px 1fr',
    gap: 24,
  },
  sidebar: {
    background: 'white',
    borderRadius: 8,
    padding: 20,
    boxShadow: '0 1px 4px rgba(0,0,0,0.05)',
    alignSelf: 'start',
    position: 'sticky',
    top: 20,
  },
  sidebarTitle: {
    margin: '0 0 8px 0',
    fontSize: 14,
    fontWeight: 600,
    color: '#1B4F72',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  sidebarText: {
    fontSize: 13,
    lineHeight: 1.55,
    color: '#566573',
    margin: '0 0 16px 0',
  },
  sidebarSubtitle: {
    fontSize: 12,
    fontWeight: 600,
    color: '#1B4F72',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    margin: '16px 0 8px 0',
  },
  tierList: { listStyle: 'none', padding: 0, margin: '0 0 10px 0' },
  tierItem: { fontSize: 13, color: '#34495E', padding: '3px 0' },
  policyCount: { fontSize: 13, color: '#34495E', marginBottom: 20 },
  sourceBlock: {
    background: '#FEF9E7',
    border: '1px solid #B7950B',
    borderLeft: '4px solid #B7950B',
    borderRadius: 6,
    padding: '12px 14px',
    marginBottom: 20,
  },
  sourceLine: {
    margin: '0 0 8px 0',
    fontSize: 12,
    lineHeight: 1.5,
    color: '#5D4E0A',
  },
  sourceLink: {
    color: '#1B4F72',
    fontWeight: 600,
    textDecoration: 'underline',
  },
  sourceList: {
    margin: '4px 0 0 0',
    paddingLeft: 18,
    fontSize: 12,
    color: '#5D4E0A',
  },
  sourceListItem: {
    marginBottom: 4,
    lineHeight: 1.5,
  },
  runButton: {
    width: '100%',
    padding: '12px 16px',
    background: '#148F77',
    color: 'white',
    border: 'none',
    borderRadius: 6,
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
  },
  main: { minHeight: 400 },
  placeholder: {
    background: 'white',
    borderRadius: 8,
    padding: 48,
    textAlign: 'center',
    boxShadow: '0 1px 4px rgba(0,0,0,0.05)',
  },
  placeholderIcon: { fontSize: 48, marginBottom: 12 },
  placeholderTitle: {
    fontSize: 20,
    fontWeight: 600,
    color: '#1B4F72',
    margin: '0 0 10px 0',
  },
  placeholderText: {
    fontSize: 14,
    lineHeight: 1.6,
    color: '#566573',
    margin: 0,
  },
  section: {
    background: 'white',
    borderRadius: 8,
    padding: 24,
    marginBottom: 20,
    boxShadow: '0 1px 4px rgba(0,0,0,0.05)',
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: 600,
    color: '#1B4F72',
    margin: '0 0 16px 0',
  },
  sectionSub: {
    fontSize: 13,
    color: '#566573',
    margin: '-8px 0 16px 0',
    lineHeight: 1.5,
  },
  metricsRow: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
    gap: 12,
  },
  metricCard: {
    background: '#F4F6F7',
    padding: '14px 16px',
    borderRadius: 6,
    borderLeft: '3px solid #2874A6',
  },
  metricLabel: { fontSize: 11, color: '#7B8A8B', textTransform: 'uppercase', letterSpacing: '0.5px' },
  metricValue: { fontSize: 22, fontWeight: 700, color: '#1B4F72', marginTop: 4 },
  tableWrap: { overflowX: 'auto' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: {
    background: '#F4F6F7',
    padding: '10px 12px',
    textAlign: 'left',
    fontWeight: 600,
    borderBottom: '2px solid #BDC3C7',
    fontSize: 12,
    color: '#1B4F72',
  },
  td: {
    padding: '10px 12px',
    borderBottom: '1px solid #EAEDED',
    color: '#2C3E50',
  },
  classificationBadge: {
    padding: '3px 10px',
    borderRadius: 10,
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.3px',
  },
  errorBlock: {
    background: '#FDEDEC',
    color: '#922B21',
    padding: 20,
    borderRadius: 6,
  },
  tourCard: {
    background: '#FEF9E7',
    border: '1px solid #F4D03F',
    borderRadius: 8,
    padding: 20,
    marginBottom: 20,
  },
  tourHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  tourStep: { fontSize: 11, fontWeight: 600, color: '#7D6608', letterSpacing: '0.5px', textTransform: 'uppercase' },
  tourClose: { background: 'transparent', border: 'none', fontSize: 20, cursor: 'pointer', color: '#7D6608' },
  tourTitle: { fontSize: 16, fontWeight: 600, color: '#7D6608', margin: '0 0 8px 0' },
  tourBody: { fontSize: 14, lineHeight: 1.55, color: '#34495E', margin: '0 0 14px 0' },
  tourNav: { display: 'flex', justifyContent: 'space-between', gap: 8 },
  tourBtn: {
    padding: '8px 16px',
    background: '#1B4F72',
    color: 'white',
    border: 'none',
    borderRadius: 4,
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
  },
  tourBtnSecondary: {
    padding: '8px 16px',
    background: 'transparent',
    color: '#1B4F72',
    border: '1px solid #BDC3C7',
    borderRadius: 4,
    fontSize: 13,
    cursor: 'pointer',
  },
};
