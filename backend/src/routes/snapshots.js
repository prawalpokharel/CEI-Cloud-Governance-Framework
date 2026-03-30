const express = require('express');
const axios = require('axios');
const { authenticateToken } = require('../middleware/auth');
const router = express.Router();

router.post('/create', authenticateToken, async (req, res) => {
  try {
    const coreUrl = req.app.get('coreEngineUrl');
    const response = await axios.post(`${coreUrl}/rollback/snapshot`, req.body);
    res.json(response.data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/revert/:snapshotId', authenticateToken, async (req, res) => {
  try {
    const coreUrl = req.app.get('coreEngineUrl');
    const response = await axios.post(`${coreUrl}/rollback/revert/${req.params.snapshotId}`);
    res.json(response.data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
