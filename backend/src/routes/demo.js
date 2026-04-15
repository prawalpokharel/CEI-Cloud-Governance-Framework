const express = require('express');
const axios = require('axios');
const { demoAccess } = require('../middleware/demoAccess');

const router = express.Router();

/**
 * Demo routes: public, unauthenticated endpoints that proxy to the core
 * engine's scenario endpoints. These allow evaluation of the CEI framework
 * without requiring user accounts or cloud credentials.
 */

router.use(demoAccess);

/**
 * POST /api/demo/analyze
 *
 * No-auth proxy to the core engine's /analyze endpoint. The frontend's
 * AnalysisPanel uses this so it doesn't need to be configured with a
 * direct NEXT_PUBLIC_CORE_ENGINE_URL.
 *
 * Body shape mirrors POST /analyze on the core engine:
 *   { telemetry: {nodes, edges, governance_policies}, ...thresholds }
 */
router.post('/analyze', async (req, res) => {
  try {
    const coreUrl = req.app.get('coreEngineUrl');
    const response = await axios.post(`${coreUrl}/analyze`, req.body);
    res.json(response.data);
  } catch (err) {
    const status = err.response?.status || 500;
    const message = err.response?.data?.detail || err.message;
    res.status(status).json({ error: message });
  }
});

/**
 * GET /api/demo/scenarios
 * List all available demonstration scenarios with metadata.
 */
router.get('/scenarios', async (req, res) => {
  try {
    const coreUrl = req.app.get('coreEngineUrl');
    const response = await axios.get(`${coreUrl}/scenarios/list`);
    res.json(response.data);
  } catch (err) {
    const status = err.response?.status || 500;
    const message = err.response?.data?.detail || err.message;
    res.status(status).json({ error: message });
  }
});

/**
 * GET /api/demo/scenarios/:id
 * Retrieve full dataset for a single scenario (topology, governance,
 * telemetry, writeup).
 */
router.get('/scenarios/:id', async (req, res) => {
  try {
    const coreUrl = req.app.get('coreEngineUrl');
    const response = await axios.get(
      `${coreUrl}/scenarios/${req.params.id}`
    );
    res.json(response.data);
  } catch (err) {
    const status = err.response?.status || 500;
    const message = err.response?.data?.detail || err.message;
    res.status(status).json({ error: message });
  }
});

/**
 * POST /api/demo/scenarios/:id/analyze
 * Execute the full CEI pipeline on a scenario and return analysis results.
 */
router.post('/scenarios/:id/analyze', async (req, res) => {
  try {
    const coreUrl = req.app.get('coreEngineUrl');
    const response = await axios.post(
      `${coreUrl}/scenarios/${req.params.id}/analyze`
    );
    res.json(response.data);
  } catch (err) {
    const status = err.response?.status || 500;
    const message = err.response?.data?.detail || err.message;
    res.status(status).json({ error: message });
  }
});

module.exports = router;
