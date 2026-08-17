import { useMemo } from 'react';
import {
  notificationLabel,
  severityStyle,
  trendPoints,
  trendSummary,
} from '../../lib/driftView';

/**
 * The drift rail, made visible: what structurally changed between snapshots,
 * and the concentration trend those changes moved.
 *
 * Renders nothing when there is nothing to say. A cluster whose topology is
 * stable should have a quiet dashboard -- an empty "no drift" panel on every
 * screen is how the panel earns being scrolled past on the day it matters.
 */
export default function DriftPanel({ drift, styles }) {
  const s = styles;
  const events = drift?.events || [];
  const trend = drift?.concentration_trend || [];
  const summary = useMemo(() => trendSummary(trend), [trend]);
  const points = useMemo(() => trendPoints(trend, 640, 60), [trend]);

  if (!events.length && !points) return null;

  return (
    <div style={s.panel}>
      <div style={s.panelTitle}>
        Structural drift — what changed between snapshots
        {summary && (
          <span style={driftStyles.trendBadge}>
            concentration {summary.direction}
            {summary.relativePct !== null &&
              summary.direction !== 'stable' &&
              ` ${summary.relativePct > 0 ? '+' : ''}${summary.relativePct}%`}
          </span>
        )}
      </div>

      {points && (
        <div style={driftStyles.trendWrap}>
          <svg
            viewBox="0 0 640 60"
            style={driftStyles.trendSvg}
            role="img"
            aria-label="Structural concentration trend"
          >
            <polyline
              points={points}
              fill="none"
              stroke="#1F618D"
              strokeWidth="1.5"
            />
          </svg>
          <div style={driftStyles.trendLabels}>
            <span>{summary.first.toFixed(3)}</span>
            <span style={driftStyles.trendCaption}>
              structural concentration per snapshot — 0 is evenly spread, 1 is
              one workload holding everything
            </span>
            <span>{summary.last.toFixed(3)}</span>
          </div>
        </div>
      )}

      {events.slice(0, 8).map((event) => {
        const paging = notificationLabel(event);
        return (
          <div key={event.id} style={s.finding}>
            <span style={{ ...s.sev, ...severityStyle(event.severity) }}>
              {event.severity}
            </span>
            <div style={{ flex: 1 }}>
              <div style={s.findingTitle}>{event.title}</div>
              <div style={s.findingDetail}>{event.detail}</div>
              <div style={driftStyles.meta}>
                {new Date(event.detected_at).toLocaleString()}
                {paging && ` · ${paging}`}
              </div>
            </div>
          </div>
        );
      })}
      {events.length > 8 && (
        <div style={driftStyles.meta}>
          {events.length - 8} older event(s) not shown.
        </div>
      )}
    </div>
  );
}

const driftStyles = {
  trendWrap: { margin: '4px 0 14px' },
  trendSvg: { width: '100%', height: 60, display: 'block' },
  trendLabels: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 11,
    color: '#7F8C8D',
    fontVariantNumeric: 'tabular-nums',
  },
  trendCaption: { fontStyle: 'italic' },
  meta: { fontSize: 11, color: '#95A5A6', marginTop: 3 },
};
