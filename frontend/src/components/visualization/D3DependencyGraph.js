import { useEffect, useRef, useMemo } from 'react';
import * as d3 from 'd3';

/**
 * D3 force-directed dependency graph (Patent Module 103: Graph Constructor)
 *
 * Visual encoding (per patent specification):
 *   - Node color   ← CEI classification (critical/elevated/moderate/low)
 *   - Node radius  ← Centrality score (PageRank)
 *   - Edge width   ← Edge weight from topology
 *   - Hover        ← Shows full per-node CEI breakdown (α·C, β·H, γ·R)
 *   - Drag         ← Repositions a node; releases pin on dblclick
 *
 * Interaction:
 *   - Pinch / scroll-wheel to zoom (d3-zoom, scale 0.3 .. 4)
 *   - Click-drag empty space to pan
 *   - Toolbar provides + / − / Reset / Fit-to-screen buttons
 *
 * Inputs:
 *   topology  – { nodes: [{id, ...}], edges: [[src,dst,weight] | {source,target,weight}] }
 *   analysis  – { nodes: [{node_id, cei_score, centrality, entropy, risk_factor,
 *                          classification, recommendation}], weights: {alpha, beta, gamma} }
 *   width/height – SVG viewport (defaults provided)
 */

const CLASSIFICATION_COLORS = {
  critical: '#E74C3C',
  elevated: '#F39C12',
  moderate: '#3498DB',
  low: '#27AE60',
};

const CLASSIFICATION_BORDER = {
  critical: '#922B21',
  elevated: '#7D6608',
  moderate: '#1B4F72',
  low: '#196F3D',
};

export default function D3DependencyGraph({
  topology,
  analysis,
  width = 900,
  height = 560,
}) {
  const svgRef = useRef(null);
  const tooltipRef = useRef(null);
  const zoomBehaviorRef = useRef(null);
  const innerGroupRef = useRef(null);

  // Index analysis by node id for O(1) lookup during render.
  const analysisByNode = useMemo(() => {
    const map = {};
    (analysis?.nodes || []).forEach((n) => {
      map[n.node_id] = n;
    });
    return map;
  }, [analysis]);

  // Normalize edges: accept either [source, target, weight] tuples
  // (loader output) or {source, target, weight} dicts.
  const normalizedLinks = useMemo(() => {
    const edges = topology?.edges || [];
    return edges.map((e) => {
      if (Array.isArray(e)) {
        return { source: e[0], target: e[1], weight: e[2] ?? 1.0 };
      }
      return {
        source: e.source ?? e[0],
        target: e.target ?? e[1],
        weight: e.weight ?? 1.0,
      };
    });
  }, [topology]);

  const normalizedNodes = useMemo(() => {
    return (topology?.nodes || []).map((n) => ({
      id: n.id,
      tier: n.tier || 'supporting',
      type: n.type || 'service',
    }));
  }, [topology]);

  useEffect(() => {
    if (!normalizedNodes.length) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    svg
      .attr('viewBox', `0 0 ${width} ${height}`)
      .attr('preserveAspectRatio', 'xMidYMid meet')
      .style('background', '#FAFBFC')
      .style('border', '1px solid #E5E8EB')
      .style('border-radius', '6px')
      .style('cursor', 'grab');

    // Arrow marker for directed edges
    svg
      .append('defs')
      .append('marker')
      .attr('id', 'arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 18)
      .attr('refY', 0)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', '#7B8A8B');

    // Centrality range for radius scale
    const centralityValues = normalizedNodes.map(
      (n) => analysisByNode[n.id]?.centrality ?? 0
    );
    const radiusScale = d3
      .scaleLinear()
      .domain([
        Math.min(0, d3.min(centralityValues) || 0),
        Math.max(0.1, d3.max(centralityValues) || 0.1),
      ])
      .range([10, 26]);

    // Edge weight range for stroke width
    const weights = normalizedLinks.map((l) => l.weight || 1);
    const strokeScale = d3
      .scaleLinear()
      .domain([d3.min(weights) || 0.1, d3.max(weights) || 1])
      .range([0.8, 3]);

    // Zoomable group — all subsequent content goes inside this <g>
    const root = svg.append('g').attr('class', 'zoom-root');
    innerGroupRef.current = root;

    // Approximate label half-width so the collide force keeps labels apart too.
    const labelHalfWidth = (id) => Math.max(28, id.length * 3.2);

    // Force simulation
    // - link distance scaled up so labels fit between nodes
    // - collision radius includes node radius + label half-width
    const simulation = d3
      .forceSimulation(normalizedNodes)
      .force(
        'link',
        d3
          .forceLink(normalizedLinks)
          .id((d) => d.id)
          .distance(130)
          .strength(0.35)
      )
      .force('charge', d3.forceManyBody().strength(-360))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force(
        'collide',
        d3.forceCollide().radius((d) => {
          const r = radiusScale(analysisByNode[d.id]?.centrality ?? 0);
          return r + labelHalfWidth(d.id) + 6;
        })
      )
      .force('x', d3.forceX(width / 2).strength(0.04))
      .force('y', d3.forceY(height / 2).strength(0.04));

    // Edges
    const link = root
      .append('g')
      .attr('stroke', '#B0BEC5')
      .attr('stroke-opacity', 0.6)
      .selectAll('line')
      .data(normalizedLinks)
      .join('line')
      .attr('stroke-width', (d) => strokeScale(d.weight))
      .attr('marker-end', 'url(#arrow)');

    // Nodes (group containing circle + label)
    const nodeGroup = root
      .append('g')
      .selectAll('g')
      .data(normalizedNodes)
      .join('g')
      .style('cursor', 'grab')
      .call(
        d3
          .drag()
          .on('start', dragStarted)
          .on('drag', dragged)
          .on('end', dragEnded)
      );

    nodeGroup
      .append('circle')
      .attr('r', (d) => radiusScale(analysisByNode[d.id]?.centrality ?? 0))
      .attr('fill', (d) => {
        const cls = analysisByNode[d.id]?.classification;
        return CLASSIFICATION_COLORS[cls] || '#95A5A6';
      })
      .attr('stroke', (d) => {
        const cls = analysisByNode[d.id]?.classification;
        return CLASSIFICATION_BORDER[cls] || '#566573';
      })
      .attr('stroke-width', 1.5);

    // Label rendered with a white halo (paint-order: stroke) so it stays
    // readable even if it crosses an edge or overlaps a neighboring label.
    nodeGroup
      .append('text')
      .text((d) => d.id)
      .attr('font-size', 11)
      .attr(
        'font-family',
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
      )
      .attr('text-anchor', 'middle')
      .attr('dy', (d) => radiusScale(analysisByNode[d.id]?.centrality ?? 0) + 14)
      .attr('fill', '#1C2833')
      .style('paint-order', 'stroke')
      .style('stroke', 'white')
      .style('stroke-width', 3)
      .style('stroke-linejoin', 'round')
      .style('pointer-events', 'none')
      .style('user-select', 'none');

    // Tooltip on hover
    const tooltip = d3.select(tooltipRef.current);

    nodeGroup
      .on('mouseover', function (event, d) {
        const a = analysisByNode[d.id];
        const w = analysis?.weights || { alpha: 0, beta: 0, gamma: 0 };
        if (!a) {
          tooltip.style('opacity', 0);
          return;
        }
        const c = (a.centrality * w.alpha).toFixed(3);
        const h = (a.entropy * w.beta).toFixed(3);
        const r = (a.risk_factor * w.gamma).toFixed(3);
        tooltip
          .style('opacity', 1)
          .html(
            `<div style="font-weight:600;margin-bottom:4px">${d.id}</div>` +
              `<div>Tier: ${d.tier}</div>` +
              `<div>Classification: <strong>${a.classification.toUpperCase()}</strong></div>` +
              `<div style="margin-top:6px;border-top:1px solid #444;padding-top:6px">` +
              `<div>α·C = ${w.alpha.toFixed(2)} × ${a.centrality.toFixed(3)} = ${c}</div>` +
              `<div>β·H = ${w.beta.toFixed(2)} × ${a.entropy.toFixed(3)} = ${h}</div>` +
              `<div>γ·R = ${w.gamma.toFixed(2)} × ${a.risk_factor.toFixed(3)} = ${r}</div>` +
              `<div style="margin-top:4px;font-weight:600">CEI = ${a.cei_score.toFixed(3)}</div>` +
              `</div>` +
              `<div style="margin-top:6px;font-size:11px;color:#BDC3C7">${a.recommendation}</div>`
          );
      })
      .on('mousemove', function (event) {
        const [x, y] = d3.pointer(event, document.body);
        tooltip.style('left', `${x + 14}px`).style('top', `${y + 14}px`);
      })
      .on('mouseout', function () {
        tooltip.style('opacity', 0);
      })
      .on('dblclick', function (event, d) {
        // Release a pinned node
        d.fx = null;
        d.fy = null;
      });

    simulation.on('tick', () => {
      link
        .attr('x1', (d) => d.source.x)
        .attr('y1', (d) => d.source.y)
        .attr('x2', (d) => d.target.x)
        .attr('y2', (d) => d.target.y);
      nodeGroup.attr('transform', (d) => `translate(${d.x},${d.y})`);
    });

    // d3-zoom: scroll wheel zoom + drag-to-pan on background
    const zoom = d3
      .zoom()
      .scaleExtent([0.3, 4])
      .filter((event) => {
        // allow wheel always; only pan when not initiated on a node
        if (event.type === 'wheel') return true;
        return !event.target.closest('g[data-node]') && event.button === 0;
      })
      .on('zoom', (event) => {
        root.attr('transform', event.transform);
      });
    zoomBehaviorRef.current = zoom;
    svg.call(zoom);

    function dragStarted(event, d) {
      if (!event.active) simulation.alphaTarget(0.3).restart();
      d.fx = d.x;
      d.fy = d.y;
    }
    function dragged(event, d) {
      d.fx = event.x;
      d.fy = event.y;
    }
    function dragEnded(event, d) {
      if (!event.active) simulation.alphaTarget(0);
      // Keep pinned where dropped; dblclick to release.
    }

    return () => {
      simulation.stop();
    };
  }, [normalizedNodes, normalizedLinks, analysisByNode, analysis, width, height]);

  const zoomBy = (factor) => {
    if (!zoomBehaviorRef.current || !svgRef.current) return;
    d3.select(svgRef.current)
      .transition()
      .duration(200)
      .call(zoomBehaviorRef.current.scaleBy, factor);
  };
  const resetZoom = () => {
    if (!zoomBehaviorRef.current || !svgRef.current) return;
    d3.select(svgRef.current)
      .transition()
      .duration(250)
      .call(zoomBehaviorRef.current.transform, d3.zoomIdentity);
  };
  const fitToScreen = () => {
    if (!zoomBehaviorRef.current || !svgRef.current || !innerGroupRef.current) return;
    const svgEl = svgRef.current;
    const inner = innerGroupRef.current.node();
    if (!inner) return;
    const bbox = inner.getBBox();
    if (!bbox.width || !bbox.height) return;
    const margin = 40;
    const scale = Math.min(
      (width - margin) / bbox.width,
      (height - margin) / bbox.height,
      4
    );
    const tx = (width - bbox.width * scale) / 2 - bbox.x * scale;
    const ty = (height - bbox.height * scale) / 2 - bbox.y * scale;
    d3.select(svgEl)
      .transition()
      .duration(300)
      .call(
        zoomBehaviorRef.current.transform,
        d3.zoomIdentity.translate(tx, ty).scale(scale)
      );
  };

  return (
    <div style={{ position: 'relative' }}>
      <svg ref={svgRef} width="100%" style={{ display: 'block' }} />

      {/* Zoom controls */}
      <div style={controlStyles.bar}>
        <button
          onClick={() => zoomBy(1.4)}
          style={controlStyles.btn}
          title="Zoom in"
          aria-label="Zoom in"
        >
          +
        </button>
        <button
          onClick={() => zoomBy(1 / 1.4)}
          style={controlStyles.btn}
          title="Zoom out"
          aria-label="Zoom out"
        >
          −
        </button>
        <button
          onClick={fitToScreen}
          style={controlStyles.btnText}
          title="Fit graph to viewport"
        >
          Fit
        </button>
        <button
          onClick={resetZoom}
          style={controlStyles.btnText}
          title="Reset zoom and pan"
        >
          Reset
        </button>
      </div>

      {/* Legend */}
      <div style={legendStyles.legend}>
        <div style={legendStyles.legendTitle}>Classification</div>
        {Object.entries(CLASSIFICATION_COLORS).map(([cls, color]) => (
          <div key={cls} style={legendStyles.legendRow}>
            <span
              style={{
                ...legendStyles.legendDot,
                background: color,
                borderColor: CLASSIFICATION_BORDER[cls],
              }}
            />
            <span style={legendStyles.legendLabel}>{cls}</span>
          </div>
        ))}
        <div style={{ ...legendStyles.legendTitle, marginTop: 10 }}>Encoding</div>
        <div style={legendStyles.legendNote}>radius = centrality</div>
        <div style={legendStyles.legendNote}>edge width = dep. weight</div>
        <div style={{ ...legendStyles.legendNote, marginTop: 6, fontStyle: 'italic' }}>
          scroll to zoom · drag to pan
        </div>
        <div style={{ ...legendStyles.legendNote, fontStyle: 'italic' }}>
          drag node to pin · dbl-click to release
        </div>
      </div>

      <div
        ref={tooltipRef}
        style={{
          position: 'fixed',
          opacity: 0,
          background: '#1C2833',
          color: 'white',
          padding: '8px 10px',
          borderRadius: 4,
          fontSize: 12,
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
          pointerEvents: 'none',
          zIndex: 1000,
          minWidth: 200,
          boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
        }}
      />
    </div>
  );
}

const controlStyles = {
  bar: {
    position: 'absolute',
    top: 12,
    left: 12,
    background: 'rgba(255,255,255,0.95)',
    border: '1px solid #E5E8EB',
    borderRadius: 6,
    padding: 4,
    display: 'flex',
    gap: 4,
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
  },
  btn: {
    width: 28,
    height: 28,
    border: '1px solid transparent',
    background: 'white',
    borderRadius: 4,
    fontSize: 16,
    fontWeight: 700,
    color: '#1B4F72',
    cursor: 'pointer',
    lineHeight: 1,
  },
  btnText: {
    height: 28,
    padding: '0 10px',
    border: '1px solid transparent',
    background: 'white',
    borderRadius: 4,
    fontSize: 12,
    fontWeight: 600,
    color: '#1B4F72',
    cursor: 'pointer',
  },
};

const legendStyles = {
  legend: {
    position: 'absolute',
    top: 12,
    right: 12,
    background: 'rgba(255,255,255,0.95)',
    padding: '10px 12px',
    borderRadius: 6,
    border: '1px solid #E5E8EB',
    fontSize: 11,
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
  },
  legendTitle: {
    fontWeight: 600,
    color: '#1B4F72',
    fontSize: 11,
    marginBottom: 6,
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  legendRow: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 },
  legendDot: {
    display: 'inline-block',
    width: 12,
    height: 12,
    borderRadius: '50%',
    border: '1.5px solid',
  },
  legendLabel: { color: '#34495E', textTransform: 'capitalize' },
  legendNote: { color: '#7B8A8B', fontSize: 11, marginBottom: 2 },
};
