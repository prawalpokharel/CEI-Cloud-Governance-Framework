import { useState, useRef } from 'react';
import Link from 'next/link';
import Head from 'next/head';

const CORE_BASE =
  process.env.NEXT_PUBLIC_CORE_ENGINE_URL || 'http://localhost:8000';

/**
 * Manual Data Upload (PR 6)
 *
 * Drag-and-drop or paste CEI input data (JSON or CSV bundle) and run
 * the full Patent-Module-101..112 pipeline against it without needing
 * cloud credentials. Schema validation surfaces problems before we hit
 * the network.
 *
 * Accepted shapes:
 *   - Single JSON file matching the scenario template (see /cei-template.json)
 *   - Two CSV files: nodes.csv (id,tier,type) + edges.csv (source,target,weight)
 */

const REQUIRED_TOPOLOGY_KEYS = ['nodes', 'edges'];
const REQUIRED_NODE_KEYS = ['id'];

function validatePayload(payload) {
  const errors = [];
  if (!payload || typeof payload !== 'object') {
    errors.push('Payload must be a JSON object.');
    return errors;
  }
  if (!payload.topology || typeof payload.topology !== 'object') {
    errors.push("Missing 'topology' object.");
    return errors;
  }
  REQUIRED_TOPOLOGY_KEYS.forEach((k) => {
    if (!Array.isArray(payload.topology[k])) {
      errors.push(`'topology.${k}' must be an array.`);
    }
  });
  if (Array.isArray(payload.topology.nodes)) {
    payload.topology.nodes.forEach((n, i) => {
      REQUIRED_NODE_KEYS.forEach((k) => {
        if (!n[k]) errors.push(`Node #${i}: missing '${k}'.`);
      });
    });
  }
  if (payload.governance && typeof payload.governance !== 'object') {
    errors.push("'governance' must be an object if provided.");
  }
  return errors;
}

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length < 2) return [];
  const headers = lines[0].split(',').map((h) => h.trim());
  return lines.slice(1).map((line) => {
    const parts = line.split(',').map((p) => p.trim());
    const row = {};
    headers.forEach((h, i) => {
      row[h] = parts[i];
    });
    return row;
  });
}

function csvBundleToPayload({ nodesCsv, edgesCsv }) {
  const nodes = parseCsv(nodesCsv).map((r) => ({
    id: r.id,
    tier: r.tier || 'supporting',
    type: r.type || 'service',
    region: r.region || 'unknown',
    replicas: r.replicas ? Number(r.replicas) : 1,
  }));
  const edges = parseCsv(edgesCsv).map((r) => [
    r.source,
    r.target,
    r.weight ? Number(r.weight) : 1.0,
  ]);
  // Synthesize minimal telemetry: single point with cpu = mem = 0.5 each
  const telemetry = {};
  nodes.forEach((n) => {
    telemetry[n.id] = [
      { t: 0, cpu: 0.5, mem: 0.5 },
      { t: 1, cpu: 0.52, mem: 0.51 },
      { t: 2, cpu: 0.48, mem: 0.5 },
    ];
  });
  return {
    topology: { nodes, edges },
    governance: { tiers: {}, policies: [] },
    telemetry,
  };
}

function payloadToAnalyzeBody(payload) {
  // Mirror the loader's to_core_engine_format() shape so /analyze accepts it.
  const tel = payload.telemetry || {};
  const nodes = (payload.topology.nodes || []).map((n) => {
    const series = tel[n.id] || [];
    const latest = series[series.length - 1] || { cpu: 0.5, mem: 0.5 };
    return {
      node_id: n.id,
      metrics: {
        cpu_utilization: (latest.cpu || 0) * 100,
        memory_utilization: (latest.mem || 0) * 100,
        network_throughput: 0,
        disk_io: 0,
      },
      metadata: {
        tier: n.tier || 'supporting',
        type: n.type || 'service',
        region: n.region || 'unknown',
        replicas: n.replicas || 1,
      },
      utilization_history: series,
    };
  });
  const edges = (payload.topology.edges || []).map((e) =>
    Array.isArray(e)
      ? { source: e[0], target: e[1], weight: e[2] ?? 1.0, type: 'dependency' }
      : { ...e, type: e.type || 'dependency' }
  );
  const governance_policies = {};
  const gov = payload.governance || {};
  Object.entries(gov.tiers || {}).forEach(([tier, def]) => {
    governance_policies[`tier_${tier}`] = {
      description: `Tier: ${tier}`,
      constraints: def,
      applies_to_tier: tier,
    };
  });
  (gov.policies || []).forEach((p, i) => {
    governance_policies[p.id || `policy_${i}`] = p;
  });
  return {
    telemetry: { nodes, edges, governance_policies },
  };
}

export default function UploadPage() {
  const [rawText, setRawText] = useState('');
  const [parsed, setParsed] = useState(null);
  const [errors, setErrors] = useState([]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [runError, setRunError] = useState(null);
  const [csvNodes, setCsvNodes] = useState('');
  const [csvEdges, setCsvEdges] = useState('');
  const [mode, setMode] = useState('json'); // 'json' | 'csv'
  const dropRef = useRef(null);

  const handleParse = (text, source) => {
    setRunError(null);
    setResult(null);
    try {
      const obj = JSON.parse(text);
      const errs = validatePayload(obj);
      setErrors(errs);
      setParsed(errs.length === 0 ? obj : null);
      if (source) setRawText(text);
    } catch (e) {
      setErrors([`Invalid JSON: ${e.message}`]);
      setParsed(null);
    }
  };

  const handleFile = async (file) => {
    if (!file) return;
    const text = await file.text();
    handleParse(text, 'file');
  };

  const handleDrop = (e) => {
    e.preventDefault();
    if (dropRef.current) dropRef.current.style.background = '#FAFBFC';
    const file = e.dataTransfer?.files?.[0];
    if (file) handleFile(file);
  };

  const handleCsvSubmit = () => {
    if (!csvNodes.trim() || !csvEdges.trim()) {
      setErrors(['Both nodes.csv and edges.csv are required.']);
      return;
    }
    try {
      const payload = csvBundleToPayload({ nodesCsv: csvNodes, edgesCsv: csvEdges });
      const errs = validatePayload(payload);
      setErrors(errs);
      setParsed(errs.length === 0 ? payload : null);
      setRawText(JSON.stringify(payload, null, 2));
    } catch (e) {
      setErrors([`CSV parse error: ${e.message}`]);
    }
  };

  const runAnalysis = async () => {
    if (!parsed) return;
    setRunning(true);
    setRunError(null);
    try {
      const body = payloadToAnalyzeBody(parsed);
      const res = await fetch(`${CORE_BASE}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
      }
      setResult(await res.json());
    } catch (e) {
      setRunError(e.message);
    }
    setRunning(false);
  };

  const previewSummary = parsed
    ? {
        nodes: parsed.topology.nodes.length,
        edges: parsed.topology.edges.length,
        tiers: Object.keys(parsed.governance?.tiers || {}).length,
        policies: (parsed.governance?.policies || []).length,
      }
    : null;

  return (
    <>
      <Head>
        <title>Upload Infrastructure Data — CloudOptimizer</title>
      </Head>

      <div style={styles.page}>
        <header style={styles.header}>
          <div style={styles.headerInner}>
            <Link href="/" style={styles.backLink}>
              ← Home
            </Link>
            <h1 style={styles.title}>Upload Your Infrastructure Data</h1>
            <p style={styles.subtitle}>
              Drag a JSON file or paste CSV. We&rsquo;ll validate the schema,
              show a preview, and run the full CEI pipeline (Patent Modules
              101&ndash;112) against it.
            </p>
          </div>
        </header>

        <main style={styles.main}>
          <div style={styles.toolbar}>
            <div style={styles.modeSwitch}>
              <button
                onClick={() => setMode('json')}
                style={{
                  ...styles.modeBtn,
                  ...(mode === 'json' ? styles.modeBtnActive : {}),
                }}
              >
                JSON file / paste
              </button>
              <button
                onClick={() => setMode('csv')}
                style={{
                  ...styles.modeBtn,
                  ...(mode === 'csv' ? styles.modeBtnActive : {}),
                }}
              >
                Two CSVs
              </button>
            </div>
            <a href="/cei-template.json" download style={styles.templateBtn}>
              ⬇ Download JSON template
            </a>
          </div>

          {mode === 'json' && (
            <>
              <div
                ref={dropRef}
                onDragOver={(e) => {
                  e.preventDefault();
                  dropRef.current.style.background = '#EBF5FB';
                }}
                onDragLeave={() => {
                  if (dropRef.current) dropRef.current.style.background = '#FAFBFC';
                }}
                onDrop={handleDrop}
                style={styles.dropZone}
              >
                <div style={styles.dropTitle}>
                  Drop a .json file here
                </div>
                <div style={styles.dropSub}>or paste below — both work</div>
                <input
                  type="file"
                  accept=".json,application/json"
                  onChange={(e) => handleFile(e.target.files?.[0])}
                  style={styles.fileInput}
                />
              </div>

              <textarea
                value={rawText}
                onChange={(e) => {
                  setRawText(e.target.value);
                  if (e.target.value.trim()) handleParse(e.target.value);
                  else setParsed(null);
                }}
                placeholder='{"topology": {"nodes": [...], "edges": [...]}, "governance": {...}, "telemetry": {...}}'
                style={styles.textarea}
              />
            </>
          )}

          {mode === 'csv' && (
            <div style={styles.csvGrid}>
              <div>
                <label style={styles.csvLabel}>nodes.csv</label>
                <p style={styles.csvHint}>
                  Required headers: <code>id,tier,type</code> (region, replicas
                  optional)
                </p>
                <textarea
                  value={csvNodes}
                  onChange={(e) => setCsvNodes(e.target.value)}
                  placeholder={'id,tier,type\nlb-01,edge,load_balancer\napi-gateway,core,service'}
                  style={styles.textareaSmall}
                />
              </div>
              <div>
                <label style={styles.csvLabel}>edges.csv</label>
                <p style={styles.csvHint}>
                  Required headers: <code>source,target,weight</code>
                </p>
                <textarea
                  value={csvEdges}
                  onChange={(e) => setCsvEdges(e.target.value)}
                  placeholder={'source,target,weight\nlb-01,api-gateway,1.0\napi-gateway,auth-svc,0.8'}
                  style={styles.textareaSmall}
                />
              </div>
              <button onClick={handleCsvSubmit} style={styles.parseBtn}>
                Parse CSVs →
              </button>
            </div>
          )}

          {errors.length > 0 && (
            <div style={styles.errorBox}>
              <strong>Schema validation failed</strong>
              <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                {errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
          )}

          {previewSummary && (
            <div style={styles.previewBox}>
              <h3 style={styles.previewTitle}>Schema valid — preview</h3>
              <div style={styles.previewGrid}>
                <PreviewStat label="Nodes" value={previewSummary.nodes} />
                <PreviewStat label="Edges" value={previewSummary.edges} />
                <PreviewStat label="Gov. tiers" value={previewSummary.tiers} />
                <PreviewStat label="Policies" value={previewSummary.policies} />
              </div>
              <div style={styles.actionRow}>
                <button
                  onClick={runAnalysis}
                  disabled={running}
                  style={{
                    ...styles.runBtn,
                    opacity: running ? 0.6 : 1,
                    cursor: running ? 'wait' : 'pointer',
                  }}
                >
                  {running ? 'Running CEI Analysis…' : 'Run CEI Analysis →'}
                </button>
                <span style={styles.engineNote}>
                  POST → <code>{CORE_BASE}/analyze</code>
                </span>
              </div>
            </div>
          )}

          {runError && (
            <div style={styles.errorBox}>
              <strong>Analysis failed:</strong> {runError}
            </div>
          )}

          {result && (
            <div style={styles.resultBox}>
              <h3 style={styles.previewTitle}>Analysis complete</h3>
              <div style={styles.previewGrid}>
                <PreviewStat label="Nodes analyzed" value={result.nodes?.length} />
                <PreviewStat
                  label="α / β / γ"
                  value={`${result.weights?.alpha?.toFixed(2)} / ${result.weights?.beta?.toFixed(2)} / ${result.weights?.gamma?.toFixed(2)}`}
                />
                <PreviewStat
                  label="Suppression"
                  value={
                    result.oscillation_status?.suppression_active
                      ? 'ACTIVE'
                      : 'CLEAR'
                  }
                />
                <PreviewStat
                  label="Potential savings"
                  value={`$${(result.total_potential_savings || 0).toFixed(0)}/mo`}
                />
              </div>
              <details style={{ marginTop: 16 }}>
                <summary style={styles.rawSummary}>
                  Raw analysis JSON ({JSON.stringify(result).length} chars)
                </summary>
                <pre style={styles.rawJson}>
                  {JSON.stringify(result, null, 2)}
                </pre>
              </details>
            </div>
          )}
        </main>
      </div>
    </>
  );
}

function PreviewStat({ label, value }) {
  return (
    <div style={styles.statCard}>
      <div style={styles.statLabel}>{label}</div>
      <div style={styles.statValue}>{value ?? '—'}</div>
    </div>
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
  subtitle: { margin: 0, fontSize: 14, opacity: 0.9, lineHeight: 1.5 },
  main: { maxWidth: 1100, margin: '0 auto', padding: '32px' },
  toolbar: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
    flexWrap: 'wrap',
    gap: 12,
  },
  modeSwitch: { display: 'flex', gap: 0 },
  modeBtn: {
    padding: '8px 16px',
    fontSize: 13,
    border: '1px solid #CFD8DC',
    background: 'white',
    cursor: 'pointer',
    color: '#566573',
  },
  modeBtnActive: {
    background: '#1B4F72',
    color: 'white',
    borderColor: '#1B4F72',
  },
  templateBtn: {
    background: '#27AE60',
    color: 'white',
    padding: '8px 14px',
    borderRadius: 4,
    fontSize: 13,
    fontWeight: 600,
    textDecoration: 'none',
  },
  dropZone: {
    border: '2px dashed #B0BEC5',
    borderRadius: 8,
    padding: 36,
    textAlign: 'center',
    background: '#FAFBFC',
    transition: 'background 0.15s',
    marginBottom: 16,
  },
  dropTitle: { fontSize: 16, fontWeight: 600, color: '#1B4F72' },
  dropSub: { fontSize: 13, color: '#7B8A8B', marginTop: 6 },
  fileInput: { display: 'block', margin: '14px auto 0' },
  textarea: {
    width: '100%',
    minHeight: 200,
    padding: 14,
    fontSize: 12,
    fontFamily: 'monospace',
    border: '1px solid #CFD8DC',
    borderRadius: 6,
    resize: 'vertical',
  },
  csvGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 },
  csvLabel: {
    fontSize: 13,
    fontWeight: 600,
    color: '#1B4F72',
    display: 'block',
  },
  csvHint: {
    fontSize: 12,
    color: '#7B8A8B',
    margin: '4px 0 8px 0',
  },
  textareaSmall: {
    width: '100%',
    minHeight: 160,
    padding: 12,
    fontSize: 12,
    fontFamily: 'monospace',
    border: '1px solid #CFD8DC',
    borderRadius: 6,
    resize: 'vertical',
  },
  parseBtn: {
    gridColumn: '1 / -1',
    background: '#2874A6',
    color: 'white',
    padding: '10px 20px',
    border: 'none',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    justifySelf: 'start',
  },
  errorBox: {
    background: '#FDEDEC',
    color: '#922B21',
    border: '1px solid #E74C3C',
    borderRadius: 6,
    padding: '12px 16px',
    marginTop: 16,
    fontSize: 13,
  },
  previewBox: {
    background: 'white',
    border: '1px solid #E5E8EB',
    borderRadius: 8,
    padding: 20,
    marginTop: 16,
    boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
  },
  previewTitle: { margin: '0 0 12px 0', color: '#1B4F72', fontSize: 16 },
  previewGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
    gap: 12,
  },
  statCard: {
    background: '#F4F6F7',
    padding: '14px 16px',
    borderRadius: 6,
  },
  statLabel: {
    fontSize: 11,
    color: '#7B8A8B',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  statValue: { fontSize: 20, fontWeight: 700, color: '#1B4F72', marginTop: 4 },
  actionRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
    marginTop: 16,
    flexWrap: 'wrap',
  },
  runBtn: {
    background: '#2874A6',
    color: 'white',
    padding: '12px 24px',
    border: 'none',
    borderRadius: 6,
    fontSize: 14,
    fontWeight: 600,
  },
  engineNote: { fontSize: 12, color: '#7B8A8B' },
  resultBox: {
    background: 'white',
    border: '1px solid #27AE60',
    borderRadius: 8,
    padding: 20,
    marginTop: 16,
    boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
  },
  rawSummary: {
    fontSize: 12,
    color: '#566573',
    cursor: 'pointer',
  },
  rawJson: {
    background: '#1C2833',
    color: '#85C1E2',
    padding: 14,
    borderRadius: 6,
    fontSize: 11,
    overflow: 'auto',
    maxHeight: 320,
    marginTop: 8,
  },
};
