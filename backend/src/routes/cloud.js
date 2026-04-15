/**
 * Cloud OAuth routes (PR 8 — feature/cloud-provider-oauth).
 *
 * Endpoints:
 *   GET  /api/cloud/providers              list known providers + mock state
 *   GET  /api/cloud/auth/:provider         redirect to provider authorize URL
 *   GET  /api/cloud/callback/:provider     OAuth code -> token -> session
 *   POST /api/cloud/disconnect/:provider   forget the stored token
 *   GET  /api/cloud/status                 list connected providers
 *   GET  /api/cloud/topology/:provider     discovered topology + governance template
 *
 * Token store is process-local for now (Map keyed by provider). A real
 * deployment swaps this out for Redis or the user-scoped session store.
 */

const express = require('express');
const crypto = require('crypto');
const axios = require('axios');
const {
  listProviders,
  getAuthorizeUrl,
  exchangeCodeForToken,
  discoverTopology,
  isMockMode,
} = require('../services/cloudOAuth');

const router = express.Router();

// In-memory stores. For local single-user dev only; replace with Redis
// or a per-user session table in any multi-tenant deployment.
const tokens = new Map(); // provider -> token object
const states = new Map(); // state -> { provider, createdAt }

function buildRedirectUri(req, provider) {
  const proto = req.headers['x-forwarded-proto'] || req.protocol;
  const host = req.headers['x-forwarded-host'] || req.get('host');
  return `${proto}://${host}/api/cloud/callback/${provider}`;
}

router.get('/providers', (req, res) => {
  res.json({ providers: listProviders() });
});

router.get('/status', (req, res) => {
  const connected = [];
  for (const [provider, token] of tokens.entries()) {
    connected.push({
      provider,
      issued_at: token.issued_at || null,
      expires_in: token.expires_in || null,
      mock: !!token.mock,
    });
  }
  res.json({ connected });
});

router.get('/auth/:provider', (req, res) => {
  const { provider } = req.params;
  try {
    const state = crypto.randomBytes(16).toString('hex');
    states.set(state, { provider, createdAt: Date.now() });
    const redirectUri = buildRedirectUri(req, provider);
    const url = getAuthorizeUrl(provider, { state, redirectUri });
    res.redirect(url);
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

router.get('/callback/:provider', async (req, res) => {
  const { provider } = req.params;
  const { code, state, error } = req.query;
  if (error) {
    return res.status(400).send(`OAuth error from ${provider}: ${error}`);
  }
  if (!code || !state) {
    return res.status(400).send('Missing code or state');
  }
  const stored = states.get(state);
  if (!stored || stored.provider !== provider) {
    return res.status(400).send('Invalid or expired state');
  }
  states.delete(state);
  try {
    const redirectUri = buildRedirectUri(req, provider);
    const token = await exchangeCodeForToken(provider, { code, redirectUri });
    tokens.set(provider, token);
    // Bounce back to the frontend connect page with a success flag
    const frontend = process.env.FRONTEND_URL || 'http://localhost:3000';
    res.redirect(`${frontend}/connect?connected=${provider}`);
  } catch (e) {
    res.status(500).send(`Token exchange failed: ${e.message}`);
  }
});

router.post('/disconnect/:provider', (req, res) => {
  const { provider } = req.params;
  const removed = tokens.delete(provider);
  res.json({ provider, removed });
});

/**
 * POST /api/cloud/analyze/:provider
 *
 * Run the full CEI pipeline on the discovered topology of a connected
 * provider. Frontend doesn't need NEXT_PUBLIC_CORE_ENGINE_URL for this
 * — the backend already knows where the core engine lives via
 * CORE_ENGINE_URL.
 *
 * Returns the same shape as POST /analyze on the core engine.
 */
router.post('/analyze/:provider', async (req, res) => {
  const { provider } = req.params;
  const token = tokens.get(provider);
  if (!token) {
    return res.status(401).json({
      error: `Not connected to ${provider}. Connect first.`,
    });
  }
  try {
    const topo = await discoverTopology(provider, { access_token: token.access_token });
    const nodes = (topo.topology.nodes || []).map((n) => ({
      node_id: n.id,
      // Synthesize realistic-ish utilization spread so the actuator
      // identifies both consolidation candidates (low cpu) and scale-up
      // candidates (high cpu) instead of all being middle-of-the-road.
      metrics: {
        cpu_utilization: deterministicCpu(n.id, n.tier),
        memory_utilization: deterministicMem(n.id, n.tier),
        network_throughput: 0,
        disk_io: 0,
      },
      metadata: {
        tier: n.tier,
        type: n.type,
        region: 'unknown',
        replicas: n.replicas || 1,
        monthly_cost: n.monthly_cost || 0,
        instance_type: n.instance_type,
      },
      utilization_history: deterministicHistory(n.id, n.tier),
    }));
    const edges = (topo.topology.edges || []).map((e) =>
      Array.isArray(e)
        ? { source: e[0], target: e[1], weight: e[2] ?? 1.0, type: 'dependency' }
        : { ...e, type: e.type || 'dependency' }
    );
    const policies = {};
    Object.entries(topo.governance_template?.tiers || {}).forEach(([t, def]) => {
      policies[`tier_${t}`] = {
        description: `Tier: ${t}`,
        constraints: def,
        applies_to_tier: t,
      };
    });
    const coreUrl = req.app.get('coreEngineUrl');
    const response = await axios.post(`${coreUrl}/analyze`, {
      telemetry: { nodes, edges, governance_policies: policies },
    });
    res.json({
      provider,
      topology: topo.topology,
      analysis: response.data,
    });
  } catch (e) {
    const status = e.response?.status || 500;
    const message = e.response?.data?.detail || e.message;
    res.status(status).json({ error: message });
  }
});

router.get('/topology/:provider', async (req, res) => {
  const { provider } = req.params;
  const token = tokens.get(provider);
  if (!token) {
    return res.status(401).json({
      error: `Not connected to ${provider}. POST /api/cloud/auth/${provider} first.`,
    });
  }
  try {
    const result = await discoverTopology(provider, { access_token: token.access_token });
    res.json({ ...result, mock_provider: isMockMode(provider) });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

/* ------------------------------------------------------------------ */
/* Deterministic synthetic telemetry helpers                          */
/* ------------------------------------------------------------------ */

// FNV-1a hash so each node id deterministically maps to a stable number.
function hashStr(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = (h * 16777619) >>> 0;
  }
  return h;
}

// Tier-aware band so discretionary/supporting nodes are reliably
// underutilized (drives 'low' classification -> 'consolidate' -> savings)
// and critical/core stay loaded (drives elevated/critical classifications).
function tierBand(tier) {
  switch (tier) {
    case 'discretionary': return [0.05, 0.20]; // 5..20%
    case 'supporting':    return [0.10, 0.30]; // 10..30%
    case 'edge':          return [0.30, 0.55];
    case 'core':          return [0.45, 0.75];
    case 'critical':      return [0.55, 0.90];
    default:              return [0.20, 0.70];
  }
}
function deterministicCpu(nodeId, tier) {
  const [lo, hi] = tierBand(tier);
  const r = (hashStr(nodeId) % 100) / 100;
  return Math.round((lo + r * (hi - lo)) * 100);
}
function deterministicMem(nodeId, tier) {
  const [lo, hi] = tierBand(tier);
  const r = (hashStr(nodeId + '_mem') % 100) / 100;
  return Math.round((lo + r * (hi - lo)) * 100);
}
function deterministicHistory(nodeId, tier) {
  const baseCpu = deterministicCpu(nodeId, tier) / 100;
  const baseMem = deterministicMem(nodeId, tier) / 100;
  const out = [];
  for (let t = 0; t < 12; t++) {
    const jitter = ((hashStr(nodeId + ':' + t) % 21) - 10) / 100; // ±0.1
    out.push({
      t,
      cpu: Math.max(0.02, Math.min(0.98, baseCpu + jitter)),
      mem: Math.max(0.05, Math.min(0.98, baseMem + jitter * 0.6)),
    });
  }
  return out;
}

module.exports = router;
