import { describe, expect, it } from 'vitest';
import {
  notificationLabel,
  severityStyle,
  trendPoints,
  trendSummary,
} from '../driftView';

const trend = (values) => values.map((v, i) => ({
  captured_at: `2026-08-17T00:0${i}:00Z`,
  concentration: v,
}));

describe('trendPoints', () => {
  it('maps a rising trend into the svg viewport', () => {
    const points = trendPoints(trend([0.1, 0.2, 0.4]), 100, 50);
    const pairs = points.split(' ').map((p) => p.split(',').map(Number));

    expect(pairs).toHaveLength(3);
    // x advances monotonically; y falls as concentration rises (svg y is down)
    expect(pairs[0][0]).toBeLessThan(pairs[2][0]);
    expect(pairs[0][1]).toBeGreaterThan(pairs[2][1]);
  });

  it('pads the domain so a flat-ish real trend is not a flat line at zero', () => {
    // Concentration moves in hundredths; on a fixed [0,1] domain this would
    // render as a flat line at the bottom of the chart.
    const points = trendPoints(trend([0.03, 0.05, 0.04]), 100, 50);
    const ys = points.split(' ').map((p) => Number(p.split(',')[1]));

    expect(Math.max(...ys) - Math.min(...ys)).toBeGreaterThan(10);
  });

  it('returns null when there are not two finite points', () => {
    expect(trendPoints(trend([0.5]), 100, 50)).toBeNull();
    expect(trendPoints([], 100, 50)).toBeNull();
    expect(trendPoints([{ concentration: null }, { concentration: null }], 100, 50)).toBeNull();
  });
});

describe('trendSummary', () => {
  it('reports direction with relative change', () => {
    expect(trendSummary(trend([0.2, 0.3])).direction).toBe('rising');
    expect(trendSummary(trend([0.3, 0.2])).direction).toBe('falling');
  });

  it('treats sub-2% wobble as stable', () => {
    // Ordinary churn must not read as an event on every refresh.
    expect(trendSummary(trend([0.300, 0.303])).direction).toBe('stable');
  });

  it('handles a zero baseline without dividing by it', () => {
    const summary = trendSummary(trend([0, 0.2]));

    expect(summary.direction).toBe('rising');
    expect(summary.relativePct).toBeNull();
  });
});

describe('notificationLabel', () => {
  it('explains why nobody was paged, in words', () => {
    expect(notificationLabel({ notified: true })).toBe('notified');
    expect(notificationLabel({ notified: false, notify_skip_reason: 'debounced' }))
      .toContain('within 24h');
    expect(notificationLabel({ notified: false, notify_skip_reason: 'no_webhook_configured' }))
      .toContain('webhook');
  });

  it('says nothing for informational events — silent by design', () => {
    expect(notificationLabel({ notified: false, notify_skip_reason: 'below_threshold' }))
      .toBeNull();
  });
});

describe('severityStyle', () => {
  it('matches the health panel palette for critical', () => {
    expect(severityStyle('critical').color).toBe('#922B21');
    expect(severityStyle('info').color).toBe('#1F618D');
  });
});
