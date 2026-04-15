import { useMemo } from 'react';

/**
 * Oscillation Timeline (Patent Module 108: Oscillation Detector & Suppression)
 *
 * Visualizes per-node oscillation behavior derived from the
 * /analyze response's `oscillation_status.node_details` plus the raw
 * telemetry history. For each node we show a sparkline of utilization
 * with direction-change markers. Nodes flagged as oscillating get a
 * red pill; suppression-window overlay shows the active hysteresis.
 *
 * Inputs:
 *   oscillationStatus – analysis.oscillation_status (full block from
 *     core engine /analyze response)
 *   telemetry         – { [nodeId]: [{t, cpu, mem}, ...] } map from
 *     scenario.telemetry (scenario detail page already has this)
 */

const SPARKLINE_W = 220;
const SPARKLINE_H = 32;

function Sparkline({ values, color = '#3498DB' }) {
  if (!values || values.length === 0) {
    return <svg width={SPARKLINE_W} height={SPARKLINE_H} />;
  }

  const max = Math.max(...values, 0.01);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const stepX = SPARKLINE_W / (values.length - 1 || 1);

  const path = values
    .map((v, i) => {
      const x = i * stepX;
      const y = SPARKLINE_H - ((v - min) / range) * SPARKLINE_H;
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(' ');

  // Direction changes
  const changes = [];
  for (let i = 1; i < values.length - 1; i++) {
    const dPrev = values[i] - values[i - 1];
    const dNext = values[i + 1] - values[i];
    if (Math.sign(dPrev) !== 0 && Math.sign(dNext) !== 0 && Math.sign(dPrev) !== Math.sign(dNext)) {
      const x = i * stepX;
      const y = SPARKLINE_H - ((values[i] - min) / range) * SPARKLINE_H;
      changes.push({ x, y });
    }
  }

  return (
    <svg width={SPARKLINE_W} height={SPARKLINE_H}>
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
      {changes.map((p, i) => (
        <circle
          key={i}
          cx={p.x}
          cy={p.y}
          r={2}
          fill="#E74C3C"
          opacity={0.7}
        />
      ))}
    </svg>
  );
}

export default function OscillationTimeline({ oscillationStatus, telemetry }) {
  const rows = useMemo(() => {
    if (!oscillationStatus || !oscillationStatus.node_details) return [];
    return Object.entries(oscillationStatus.node_details)
      .map(([nodeId, det]) => {
        const series = (telemetry?.[nodeId] || []).map((p) => p.cpu ?? 0);
        return {
          nodeId,
          oscillationFrequency: det.oscillation_frequency || 0,
          directionChanges: det.direction_changes || 0,
          isOscillating: !!det.is_oscillating,
          series,
        };
      })
      .sort((a, b) => b.oscillationFrequency - a.oscillationFrequency);
  }, [oscillationStatus, telemetry]);

  if (!oscillationStatus) return null;

  const {
    suppression_active,
    hysteresis_window_minutes,
    oscillating_node_count,
    total_nodes,
    threshold,
    oscillation_ratio,
  } = oscillationStatus;

  return (
    <div style={styles.wrap}>
      <div style={styles.headerRow}>
        <div>
          <h3 style={styles.title}>Oscillation Timeline</h3>
          <p style={styles.subtitle}>
            Patent Module 108 — direction-change density per node, plus
            system-level suppression status.
          </p>
        </div>
        <div
          style={{
            ...styles.statusBadge,
            background: suppression_active ? '#FDEDEC' : '#EAFAF1',
            color: suppression_active ? '#922B21' : '#196F3D',
            borderColor: suppression_active ? '#E74C3C' : '#27AE60',
          }}
        >
          <div style={styles.statusLabel}>SUPPRESSION</div>
          <div style={styles.statusValue}>
            {suppression_active ? 'ACTIVE' : 'CLEAR'}
          </div>
          <div style={styles.statusDetail}>
            {suppression_active
              ? `Hysteresis window: ${hysteresis_window_minutes} min`
              : 'No window enforced'}
          </div>
        </div>
      </div>

      <div style={styles.summary}>
        <span>
          <strong>{oscillating_node_count}</strong> / {total_nodes} nodes
          oscillating
        </span>
        <span>
          ratio: <strong>{(oscillation_ratio * 100).toFixed(1)}%</strong>
        </span>
        <span>
          threshold θ: <strong>{threshold}</strong>
        </span>
      </div>

      <div style={styles.tableHead}>
        <span style={{ ...styles.th, flex: '0 0 160px' }}>Node</span>
        <span style={{ ...styles.th, flex: '0 0 230px' }}>CPU sparkline</span>
        <span style={{ ...styles.th, flex: '0 0 80px' }}>O(t)</span>
        <span style={{ ...styles.th, flex: '0 0 80px' }}>Δsign</span>
        <span style={{ ...styles.th, flex: 1 }}>Status</span>
      </div>
      <div style={styles.tableBody}>
        {rows.map((r) => (
          <div key={r.nodeId} style={styles.row}>
            <span style={{ ...styles.cell, flex: '0 0 160px' }}>
              <strong>{r.nodeId}</strong>
            </span>
            <span style={{ ...styles.cell, flex: '0 0 230px' }}>
              <Sparkline
                values={r.series}
                color={r.isOscillating ? '#E74C3C' : '#3498DB'}
              />
            </span>
            <span style={{ ...styles.cell, flex: '0 0 80px' }}>
              {r.oscillationFrequency.toFixed(3)}
            </span>
            <span style={{ ...styles.cell, flex: '0 0 80px' }}>
              {r.directionChanges}
            </span>
            <span style={{ ...styles.cell, flex: 1 }}>
              {r.isOscillating ? (
                <span style={styles.pillRed}>FLIP-FLOP</span>
              ) : (
                <span style={styles.pillGreen}>stable</span>
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

const styles = {
  wrap: {
    background: 'white',
    borderRadius: 8,
    padding: 20,
    border: '1px solid #E5E8EB',
    boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
  },
  headerRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    flexWrap: 'wrap',
    gap: 12,
    marginBottom: 12,
  },
  title: { margin: 0, fontSize: 16, fontWeight: 600, color: '#1B4F72' },
  subtitle: { margin: '4px 0 0 0', fontSize: 12, color: '#7B8A8B' },
  statusBadge: {
    border: '2px solid',
    borderRadius: 6,
    padding: '8px 14px',
    minWidth: 180,
    textAlign: 'center',
  },
  statusLabel: { fontSize: 10, letterSpacing: '1.5px', opacity: 0.8 },
  statusValue: { fontSize: 18, fontWeight: 700, marginTop: 2 },
  statusDetail: { fontSize: 11, marginTop: 4, opacity: 0.85 },
  summary: {
    display: 'flex',
    gap: 18,
    fontSize: 13,
    color: '#34495E',
    marginBottom: 12,
    paddingBottom: 12,
    borderBottom: '1px solid #EAEDED',
  },
  tableHead: {
    display: 'flex',
    padding: '8px 0',
    borderBottom: '1px solid #EAEDED',
    fontSize: 11,
    color: '#7B8A8B',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  th: { padding: '0 8px' },
  tableBody: { maxHeight: 380, overflowY: 'auto' },
  row: {
    display: 'flex',
    alignItems: 'center',
    padding: '6px 0',
    borderBottom: '1px solid #F4F6F7',
    fontSize: 13,
    color: '#1C2833',
  },
  cell: { padding: '0 8px' },
  pillRed: {
    background: '#FDEDEC',
    color: '#922B21',
    padding: '2px 8px',
    borderRadius: 10,
    fontSize: 11,
    fontWeight: 600,
  },
  pillGreen: {
    background: '#EAFAF1',
    color: '#196F3D',
    padding: '2px 8px',
    borderRadius: 10,
    fontSize: 11,
  },
};
