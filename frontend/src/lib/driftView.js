/**
 * Pure view logic for the drift panel, kept out of the component so it is
 * unit-testable without a DOM.
 *
 * The concentration trend is drawn as an inline SVG polyline rather than
 * pulled from a chart library: it is one line over at most 200 points, and a
 * charting dependency for that would be the heaviest module in the bundle.
 */

/**
 * Map a concentration trend to an SVG polyline `points` string.
 *
 * The y-domain is padded 10% beyond the observed range rather than fixed to
 * [0,1]: concentration moves in hundredths, and on a fixed domain every real
 * cluster's trend renders as a flat line at the bottom -- visually "nothing
 * ever changes", which is precisely the wrong message for a drift panel.
 * The absolute scale is shown separately as first/last labels.
 */
export function trendPoints(trend, width, height, pad = 4) {
  const values = (trend || [])
    .map((t) => t.concentration)
    .filter((v) => typeof v === 'number' && Number.isFinite(v));
  if (values.length < 2) return null;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 1e-6);
  const lo = Math.max(0, min - span * 0.1);
  const hi = Math.min(1, max + span * 0.1);
  const domain = Math.max(hi - lo, 1e-6);

  const innerW = width - pad * 2;
  const innerH = height - pad * 2;
  const step = values.length > 1 ? innerW / (values.length - 1) : 0;

  return values
    .map((v, i) => {
      const x = pad + i * step;
      const y = pad + innerH * (1 - (v - lo) / domain);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

/**
 * Summarise a trend for the panel header: direction and relative change.
 *
 * Change below 2% relative is reported as "stable" -- concentration wobbles
 * with ordinary churn, and a header that says "rising" on every refresh
 * trains the reader to ignore it.
 */
export function trendSummary(trend) {
  const values = (trend || [])
    .map((t) => t.concentration)
    .filter((v) => typeof v === 'number' && Number.isFinite(v));
  if (values.length < 2) return null;

  const first = values[0];
  const last = values[values.length - 1];
  const relative = first > 1e-9 ? (last - first) / first : last > 0.05 ? 1 : 0;

  let direction = 'stable';
  if (relative >= 0.02) direction = 'rising';
  else if (relative <= -0.02) direction = 'falling';

  return {
    first,
    last,
    direction,
    relativePct:
      first > 1e-9 ? Math.round(relative * 100) : null,
  };
}

/** Severity chip colours, matching the health panel's palette. */
export function severityStyle(severity) {
  if (severity === 'critical') {
    return { background: '#FDEDEC', color: '#922B21' };
  }
  if (severity === 'warning') {
    return { background: '#FEF9E7', color: '#7D6608' };
  }
  return { background: '#EAF2F8', color: '#1F618D' };
}

/**
 * "why was nobody paged" in one readable clause. The API stores the reason on
 * the row; the panel's job is to render it without making the reader decode
 * an enum.
 */
export function notificationLabel(event) {
  if (event.notified) return 'notified';
  switch (event.notify_skip_reason) {
    case 'debounced':
      return 'not re-notified (already paged within 24h)';
    case 'below_threshold':
      return null; // informational events are silent by design; say nothing
    case 'no_webhook_configured':
      return 'no Slack webhook configured';
    case 'delivery_failed':
      return 'notification delivery failed';
    default:
      return null;
  }
}
