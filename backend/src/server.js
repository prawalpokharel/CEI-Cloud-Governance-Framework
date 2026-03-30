const express = require('express');
const cors = require('cors');
const dotenv = require('dotenv');
const authRoutes = require('./routes/auth');
const analysisRoutes = require('./routes/analysis');
const providerRoutes = require('./routes/providers');
const snapshotRoutes = require('./routes/snapshots');

dotenv.config();
const app = express();
const PORT = process.env.PORT || 3001;
const CORE_ENGINE_URL = process.env.CORE_ENGINE_URL || 'http://localhost:8000';

app.use(cors({ origin: ['http://localhost:3000'], credentials: true }));
app.use(express.json({ limit: '10mb' }));
app.set('coreEngineUrl', CORE_ENGINE_URL);

app.use('/api/auth', authRoutes);
app.use('/api/analysis', analysisRoutes);
app.use('/api/providers', providerRoutes);
app.use('/api/snapshots', snapshotRoutes);

app.get('/api/health', (req, res) => {
  res.json({ status: 'healthy', service: 'CloudOptimizer Backend', version: '1.0.0', coreEngine: CORE_ENGINE_URL });
});

app.listen(PORT, () => {
  console.log(`CloudOptimizer Backend running on port ${PORT}`);
});

module.exports = app;
