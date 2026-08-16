const express = require('express');
const jwt = require('jsonwebtoken');
const bcrypt = require('bcryptjs');
const { JWT_SECRET } = require('../middleware/auth');
const router = express.Router();

// In-memory user store (production would use database)
const users = new Map();

/**
 * Without a configured secret, jwt.sign throws and the caller sees an opaque
 * 500. Fail closed with the same 503 the middleware returns so the cause is
 * obvious from the response.
 */
function requireSecret(res) {
  if (!JWT_SECRET) {
    res.status(503).json({
      error: 'Authentication is not configured on this deployment',
    });
    return false;
  }
  return true;
}

router.post('/register', async (req, res) => {
  if (!requireSecret(res)) return;
  try {
    const { email, password, name, organization } = req.body;
    if (!email || !password) return res.status(400).json({ error: 'Email and password required' });
    if (users.has(email)) return res.status(409).json({ error: 'User already exists' });

    const hashedPassword = await bcrypt.hash(password, 10);
    users.set(email, { email, password: hashedPassword, name, organization, createdAt: new Date().toISOString() });

    const token = jwt.sign({ email, name, organization }, JWT_SECRET, { expiresIn: '24h' });
    res.status(201).json({ token, user: { email, name, organization } });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/login', async (req, res) => {
  if (!requireSecret(res)) return;
  try {
    const { email, password } = req.body;
    const user = users.get(email);
    if (!user) return res.status(401).json({ error: 'Invalid credentials' });

    const valid = await bcrypt.compare(password, user.password);
    if (!valid) return res.status(401).json({ error: 'Invalid credentials' });

    const token = jwt.sign({ email: user.email, name: user.name, organization: user.organization }, JWT_SECRET, { expiresIn: '24h' });
    res.json({ token, user: { email: user.email, name: user.name, organization: user.organization } });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
