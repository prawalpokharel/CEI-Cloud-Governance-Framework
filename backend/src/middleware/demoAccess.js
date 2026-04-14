/**
 * Demo access middleware.
 *
 * Permits unauthenticated access to routes under /api/demo/* by injecting
 * a synthetic "demo" user context into the request. This allows public
 * evaluation of the CEI framework without requiring cloud provider
 * credentials or user accounts.
 *
 * Demo routes serve pre-built scenario datasets and synthetic analysis
 * results. No real infrastructure data is exposed or modified.
 */
function demoAccess(req, res, next) {
  req.user = {
    id: 'demo-user',
    role: 'demo',
    authenticated: false,
    source: 'demo-access',
  };
  next();
}

module.exports = { demoAccess };
