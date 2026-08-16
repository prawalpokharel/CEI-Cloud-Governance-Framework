/**
 * API client routing.
 *
 * The sandbox branch is the part worth testing: it silently changes both the
 * URL and whether a session is attached, and getting it wrong means either a
 * public demo that demands a login or a real cluster served as sample data.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let api;
let setToken;
let isSandbox;
let SANDBOX_ID;

beforeEach(async () => {
  vi.resetModules();

  const store = new Map();
  vi.stubGlobal('window', {
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, v),
      removeItem: (k) => store.delete(k),
    },
  });

  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    }))
  );

  ({ api, setToken, isSandbox, SANDBOX_ID } = await import('../appApi.js'));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const lastCall = () => fetch.mock.calls.at(-1);

describe('sandbox routing', () => {
  it('sends sandbox reads to the public path', async () => {
    await api.cost(SANDBOX_ID);
    expect(lastCall()[0]).toContain('/v1/sandbox/cost');
  });

  it('does not attach a session to sandbox reads', async () => {
    setToken('a-real-session-token');
    await api.topology(SANDBOX_ID);
    expect(lastCall()[1].headers.Authorization).toBeUndefined();
  });

  it('sends real cluster reads to the tenant path with the session', async () => {
    setToken('a-real-session-token');
    await api.cost('9f1c-real-cluster');
    const [url, options] = lastCall();
    expect(url).toContain('/v1/clusters/9f1c-real-cluster/cost');
    expect(url).not.toContain('sandbox');
    expect(options.headers.Authorization).toBe('Bearer a-real-session-token');
  });

  it('recognises only the exact sandbox id', () => {
    expect(isSandbox('sandbox')).toBe(true);
    expect(isSandbox('sandbox-2')).toBe(false);
    expect(isSandbox('my-sandbox')).toBe(false);
  });

  it('passes the centrality mode through on both paths', async () => {
    await api.cei(SANDBOX_ID, 'structural');
    expect(lastCall()[0]).toContain('mode=structural');
    await api.cei('real-id', 'structural');
    expect(lastCall()[0]).toContain('mode=structural');
  });
});

describe('error handling', () => {
  it('surfaces the FastAPI detail message verbatim', async () => {
    // These messages are how an operator learns their key is bound to another
    // cluster, so they must not be replaced with a generic string.
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'This cluster is already registered as "prod".' }),
    });
    await expect(api.listClusters()).rejects.toThrow(
      'This cluster is already registered as "prod".'
    );
  });

  it('attaches the status code so callers can branch on it', async () => {
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Not authenticated' }),
    });
    await expect(api.listClusters()).rejects.toMatchObject({ status: 401 });
  });

  it('falls back to a readable message when the body is not JSON', async () => {
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error('not json');
      },
    });
    await expect(api.listClusters()).rejects.toThrow('HTTP 502');
  });
});
