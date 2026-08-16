const jwt = require('jsonwebtoken');

/**
 * JWT secret resolution.
 *
 * Previously this fell back to a hardcoded literal when JWT_SECRET was
 * unset, which meant tokens in any unconfigured deployment were signed with
 * a value published in this repository -- anyone could mint a valid token.
 *
 * The fallback is removed. It is deliberately NOT replaced with a
 * crash-on-boot: the public demo routes under /api/demo/* back the NIW /
 * USPTO evidence pages and do not authenticate at all, so refusing to start
 * would take those offline to fix a problem they do not have. Instead the
 * process warns loudly at startup and the authenticated routes fail closed
 * with a 503, which is both safe and diagnosable.
 */
const JWT_SECRET = process.env.JWT_SECRET || null;

if (!JWT_SECRET) {
  console.warn(
    '[auth] JWT_SECRET is not set. Authenticated routes (/api/analysis/*, ' +
      '/api/providers/*, /api/snapshots/*) will return 503 until it is ' +
      'configured. Public demo routes are unaffected.'
  );
}

function authenticateToken(req, res, next) {
  if (!JWT_SECRET) {
    return res.status(503).json({
      error: 'Authentication is not configured on this deployment',
    });
  }

  const authHeader = req.headers['authorization'];
  const token = authHeader && authHeader.split(' ')[1];
  if (!token) return res.status(401).json({ error: 'Access token required' });

  jwt.verify(token, JWT_SECRET, (err, user) => {
    if (err) return res.status(403).json({ error: 'Invalid or expired token' });
    req.user = user;
    next();
  });
}

module.exports = { authenticateToken, JWT_SECRET };
