const express = require('express');
const cors = require('cors');
const dotenv = require('dotenv');
const authRoutes = require('./routes/auth');
const analysisRoutes = require('./routes/analysis');
const providerRoutes = require('./routes/providers');
const snapshotRoutes = require('./routes/snapshots');
const demoRoutes = require('./routes/demo');
const cloudRoutes = require('./routes/cloud');

dotenv.config();
const app = express();
const PORT = process.env.PORT || 3001;
const CORE_ENGINE_URL = process.env.CORE_ENGINE_URL || 'http://localhost:8000';

const allowedOrigins = [
  'http://localhost:3000',
  process.env.FRONTEND_URL,
].filter(Boolean);
app.use(cors({ origin: allowedOrigins, credentials: true }));
app.use(express.json({ limit: '10mb' }));
app.set('coreEngineUrl', CORE_ENGINE_URL);

app.use('/api/auth', authRoutes);
app.use('/api/analysis', analysisRoutes);
app.use('/api/providers', providerRoutes);
app.use('/api/snapshots', snapshotRoutes);
app.use('/api/demo', demoRoutes);
app.use('/api/cloud', cloudRoutes);

app.get('/api/health', (req, res) => {
  res.json({ status: 'healthy', service: 'CloudOptimizer Backend', version: '1.0.0', coreEngine: CORE_ENGINE_URL });
});

// --------------- API Documentation ---------------
app.get('/docs', (req, res) => {
  res.send(buildApiDocsHtml());
});
app.get('/api/docs', (req, res) => {
  res.redirect('/docs');
});

function buildApiDocsHtml() {
  var endpoints = [
    { method: 'GET', path: '/api/health', auth: false, desc: 'Health check -- returns service status and version.' },
    { method: 'POST', path: '/api/demo/analyze', auth: false, desc: 'Run CEI analysis on a custom topology payload. Body: { telemetry: { nodes, edges, governance_policies }, ...thresholds }.' },
    { method: 'GET', path: '/api/demo/scenarios', auth: false, desc: 'List all available demonstration scenarios with metadata.' },
    { method: 'GET', path: '/api/demo/scenarios/:id', auth: false, desc: 'Retrieve full dataset for a single scenario (topology, governance, telemetry).' },
    { method: 'POST', path: '/api/demo/scenarios/:id/analyze', auth: false, desc: 'Execute the full CEI pipeline on a named scenario and return analysis results.' },
    { method: 'POST', path: '/api/analysis/run', auth: true, desc: 'Full CEI analysis pipeline (authenticated). Same body shape as demo/analyze.' },
    { method: 'POST', path: '/api/analysis/cei', auth: true, desc: 'CEI-only computation (no governance / oscillation checks).' },
    { method: 'POST', path: '/api/analysis/oscillation', auth: true, desc: 'Run oscillation detection on the provided telemetry.' },
    { method: 'POST', path: '/api/analysis/governance', auth: true, desc: 'Validate a topology against governance policy constraints.' },
    { method: 'GET', path: '/api/cloud/providers', auth: false, desc: 'List supported cloud providers (AWS, Azure, GCP) and connection state.' },
    { method: 'GET', path: '/api/cloud/status', auth: false, desc: 'List currently connected providers with token metadata.' },
    { method: 'GET', path: '/api/cloud/auth/:provider', auth: false, desc: 'Initiate OAuth flow -- redirects to the provider authorization page.' },
    { method: 'GET', path: '/api/cloud/callback/:provider', auth: false, desc: 'OAuth callback -- exchanges authorization code for access token.' },
    { method: 'POST', path: '/api/cloud/disconnect/:provider', auth: false, desc: 'Disconnect a cloud provider (revoke stored token).' },
    { method: 'GET', path: '/api/cloud/topology/:provider', auth: false, desc: 'Discover infrastructure topology from a connected provider.' },
    { method: 'POST', path: '/api/cloud/analyze/:provider', auth: false, desc: 'Run CEI pipeline on live topology from a connected cloud provider.' },
  ];

  var rows = endpoints.map(function(e) {
    var mc = e.method === 'GET' ? '#2874A6' : '#196F3D';
    var authBadge = e.auth
      ? '<span style="background:#FEF9E7;color:#7D6608;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;">Auth</span>'
      : '<span style="background:#EAFAF1;color:#196F3D;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;">Public</span>';
    return '<tr style="border-bottom:1px solid #E8EDF0;">' +
      '<td style="padding:12px 14px;"><span style="display:inline-block;width:54px;text-align:center;background:' + mc + ';color:white;padding:3px 0;border-radius:4px;font-size:11px;font-weight:700;font-family:monospace;">' + e.method + '</span></td>' +
      '<td style="padding:12px 14px;font-family:monospace;font-size:13px;color:#1C2833;">' + e.path + '</td>' +
      '<td style="padding:12px 14px;text-align:center;">' + authBadge + '</td>' +
      '<td style="padding:12px 14px;font-size:13px;color:#566573;line-height:1.5;">' + e.desc + '</td>' +
      '</tr>';
  }).join('');

  return '<!DOCTYPE html>' +
    '<html lang="en"><head>' +
    '<meta charset="utf-8"/>' +
    '<meta name="viewport" content="width=device-width, initial-scale=1"/>' +
    '<title>CloudOptimizer API Documentation</title>' +
    '<style>' +
    '*{box-sizing:border-box;margin:0;padding:0}' +
    "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#F8F9FA;color:#1C2833}" +
    '.hdr{background:linear-gradient(135deg,#1B4F72,#2874A6);color:#fff;padding:32px 24px}' +
    '.hdr h1{font-size:28px;font-weight:700;letter-spacing:-0.5px}' +
    '.hdr p{margin-top:8px;font-size:14px;opacity:.88}' +
    '.wrap{max-width:1100px;margin:0 auto;padding:24px 16px}' +
    '.card{background:#fff;border:1px solid #E8EDF0;border-radius:8px;overflow-x:auto;box-shadow:0 1px 3px rgba(0,0,0,.04)}' +
    'table{width:100%;border-collapse:collapse;font-size:14px}' +
    'th{text-align:left;padding:12px 14px;background:#F4F6F7;color:#566573;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #E8EDF0}' +
    '.info{margin-top:24px;padding:18px;background:#EAF4FB;border-radius:8px;font-size:13px;color:#1B4F72;line-height:1.6}' +
    '.info a{color:#2874A6;font-weight:600}' +
    '.badge{display:inline-block;background:#1B4F72;color:white;padding:4px 10px;border-radius:4px;font-size:11px;font-weight:600;margin-left:10px}' +
    '</style></head><body>' +
    '<div class="hdr">' +
    '<h1>CloudOptimizer API<span class="badge">v1.0.0</span></h1>' +
    '<p>Governance-Aware Dynamic Resource Allocation &middot; CEI Framework &middot; USPTO App. No. 19/641,446</p>' +
    '</div>' +
    '<div class="wrap">' +
    '<div class="card"><table>' +
    '<thead><tr><th>Method</th><th>Endpoint</th><th>Auth</th><th>Description</th></tr></thead>' +
    '<tbody>' + rows + '</tbody>' +
    '</table></div>' +
    '<div class="info">' +
    '<strong>Base URL:</strong> <code>https://api.cloudoptimizer.app</code><br/><br/>' +
    '<strong>Demo endpoints</strong> (<code>/api/demo/*</code>) require no authentication and are intended for evaluation by USCIS reviewers, attorneys, and technical evaluators.<br/><br/>' +
    '<strong>Analysis endpoints</strong> (<code>/api/analysis/*</code>) require a Bearer token via the <code>Authorization</code> header.<br/><br/>' +
    '<strong>Cloud endpoints</strong> (<code>/api/cloud/*</code>) handle OAuth flows for AWS, Azure, and GCP provider connections.<br/><br/>' +
    'Source: <a href="https://github.com/prawalpokharel/CEI-Cloud-Governance-Framework" target="_blank">GitHub Repository</a> &middot; ' +
    'Frontend: <a href="https://cloudoptimizer.app" target="_blank">cloudoptimizer.app</a>' +
    '</div></div></body></html>';
}

app.listen(PORT, () => {
  console.log('CloudOptimizer Backend running on port ' + PORT);
});

module.exports = app;
