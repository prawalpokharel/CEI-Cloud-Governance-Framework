const express = require('express');
const axios = require('axios');
const { authenticateToken } = require('../middleware/auth');
const router = express.Router();

// Full CEI analysis pipeline
router.post('/run', authenticateToken, async (req, res) => {
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

// CEI-only computation
router.post('/cei', authenticateToken, async (req, res) => {
  try {
    const coreUrl = req.app.get('coreEngineUrl');
    const response = await axios.post(`${coreUrl}/cei/compute`, req.body);
    res.json(response.data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Oscillation detection
router.post('/oscillation', authenticateToken, async (req, res) => {
  try {
    const coreUrl = req.app.get('coreEngineUrl');
    const response = await axios.post(`${coreUrl}/oscillation/detect`, req.body);
    res.json(response.data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Governance validation
router.post('/governance', authenticateToken, async (req, res) => {
  try {
    const coreUrl = req.app.get('coreEngineUrl');
    const response = await axios.post(`${coreUrl}/governance/validate`, req.body);
    res.json(response.data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
