/**
 * Client for the /v1 product API.
 *
 * Separate from the demo/scenario fetches elsewhere in the app: those are
 * unauthenticated and hit the backend proxy, these carry a session and hit
 * the core engine directly.
 */

const API_BASE =
  process.env.NEXT_PUBLIC_CORE_ENGINE_URL || 'http://localhost:8000';

const TOKEN_KEY = 'cloudoptimizer.session';

export function getToken() {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (typeof window === 'undefined') return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export function clearToken() {
  setToken(null);
}

async function request(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth) {
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  let payload = null;
  try {
    payload = await res.json();
  } catch {
    /* empty or non-JSON body */
  }

  if (!res.ok) {
    // FastAPI puts the message in `detail`. Surfacing it verbatim matters:
    // these errors are the ones that tell an operator their key is bound to
    // another cluster, or that auth is not configured on the deployment.
    const message =
      payload?.detail || payload?.error || `Request failed (HTTP ${res.status})`;
    const error = new Error(message);
    error.status = res.status;
    throw error;
  }
  return payload;
}

// The sandbox mirrors the real cluster endpoints on a public, database-free
// path. Routing by id rather than adding a parallel UI means the demo
// exercises the same components a customer sees -- and cannot drift from them.
const SANDBOX_ID = 'sandbox';
const isSandbox = (id) => id === SANDBOX_ID;

export const api = {
  signup: (body) =>
    request('/v1/auth/signup', { method: 'POST', body, auth: false }),
  login: (body) =>
    request('/v1/auth/login', { method: 'POST', body, auth: false }),
  me: () => request('/v1/auth/me'),
  listClusters: () => request('/v1/clusters'),
  createCluster: (name) =>
    request('/v1/clusters', { method: 'POST', body: { name } }),
  topology: (id) =>
    isSandbox(id)
      ? request('/v1/sandbox/cluster', { auth: false })
      : request(`/v1/clusters/${id}/topology`),
  history: (id) =>
    isSandbox(id)
      ? request('/v1/sandbox/history', { auth: false })
      : request(`/v1/clusters/${id}/history`),
  cei: (id, mode = 'blast_radius') =>
    isSandbox(id)
      ? request(`/v1/sandbox/cei?mode=${encodeURIComponent(mode)}`, { auth: false })
      : request(`/v1/clusters/${id}/cei?mode=${encodeURIComponent(mode)}`),
  cost: (id) =>
    isSandbox(id)
      ? request('/v1/sandbox/cost', { auth: false })
      : request(`/v1/clusters/${id}/cost`),
  health: (id) =>
    isSandbox(id)
      ? request('/v1/sandbox/health', { auth: false })
      : request(`/v1/clusters/${id}/health`),
  vulnerabilities: (id) =>
    isSandbox(id)
      ? request('/v1/sandbox/vulnerabilities', { auth: false })
      : request(`/v1/clusters/${id}/vulnerabilities`),
};

export { API_BASE, SANDBOX_ID, isSandbox };
