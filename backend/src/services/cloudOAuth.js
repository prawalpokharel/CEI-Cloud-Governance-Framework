/**
 * Cloud Provider OAuth scaffolding (PR 8).
 *
 * Implements the OAuth 2.0 authorization-code grant for AWS IAM Identity
 * Center, Microsoft Identity (Azure AD / Entra), and Google OAuth. Every
 * provider exposes the same surface:
 *
 *   getAuthorizeUrl({ state, redirect_uri })  -> string
 *   exchangeCodeForToken({ code, redirect_uri }) -> Promise<{access_token, ...}>
 *   discoverTopology({ access_token }) -> Promise<{nodes, edges}>
 *
 * This first cut runs in MOCK MODE when the corresponding *_CLIENT_ID
 * env var is not set — it returns a self-loopback URL so the
 * frontend can complete the round trip without real credentials. Once
 * a deployment supplies real OAuth app credentials, the same code path
 * issues the genuine authorize URL and exchanges the code via fetch.
 *
 * Topology discovery is currently always stubbed (returns 5–8 mock
 * nodes per provider); per-provider EC2 / Azure Resource Graph / GCE
 * adapters are roadmap PR 8.x follow-ups.
 */

const PROVIDERS = {
  aws: {
    clientIdEnv: 'AWS_OAUTH_CLIENT_ID',
    secretEnv: 'AWS_OAUTH_CLIENT_SECRET',
    authorizeUrl: 'https://signin.aws.amazon.com/oauth',
    tokenUrl: 'https://oauth.amazonaws.com/token',
    scopes: ['openid', 'profile'],
    displayName: 'AWS (IAM Identity Center)',
  },
  azure: {
    clientIdEnv: 'AZURE_OAUTH_CLIENT_ID',
    secretEnv: 'AZURE_OAUTH_CLIENT_SECRET',
    authorizeUrl:
      'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    tokenUrl: 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
    scopes: ['openid', 'profile', 'https://management.azure.com/.default'],
    displayName: 'Azure (Entra ID)',
  },
  gcp: {
    clientIdEnv: 'GCP_OAUTH_CLIENT_ID',
    secretEnv: 'GCP_OAUTH_CLIENT_SECRET',
    authorizeUrl: 'https://accounts.google.com/o/oauth2/v2/auth',
    tokenUrl: 'https://oauth2.googleapis.com/token',
    scopes: [
      'openid',
      'https://www.googleapis.com/auth/cloud-platform.read-only',
    ],
    displayName: 'Google Cloud',
  },
};

function isMockMode(provider) {
  const cfg = PROVIDERS[provider];
  if (!cfg) return true;
  return !process.env[cfg.clientIdEnv];
}

function listProviders() {
  return Object.entries(PROVIDERS).map(([key, cfg]) => ({
    key,
    displayName: cfg.displayName,
    mock: isMockMode(key),
  }));
}

function getAuthorizeUrl(provider, { state, redirectUri }) {
  const cfg = PROVIDERS[provider];
  if (!cfg) throw new Error(`Unknown provider: ${provider}`);
  if (isMockMode(provider)) {
    // Self-loopback URL — the callback handler accepts the synthetic
    // code below. Real deployments set *_OAUTH_CLIENT_ID to escape mock.
    const params = new URLSearchParams({
      code: `mock_code_${provider}_${Date.now()}`,
      state,
      mock: '1',
    });
    return `${redirectUri}?${params.toString()}`;
  }
  const clientId = process.env[cfg.clientIdEnv];
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: clientId,
    redirect_uri: redirectUri,
    scope: cfg.scopes.join(' '),
    state,
  });
  return `${cfg.authorizeUrl}?${params.toString()}`;
}

async function exchangeCodeForToken(provider, { code, redirectUri }) {
  const cfg = PROVIDERS[provider];
  if (!cfg) throw new Error(`Unknown provider: ${provider}`);
  if (isMockMode(provider) || code.startsWith('mock_code_')) {
    return {
      access_token: `mock_token_${provider}_${Math.random().toString(36).slice(2, 10)}`,
      token_type: 'Bearer',
      expires_in: 3600,
      mock: true,
      provider,
      issued_at: new Date().toISOString(),
    };
  }
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code,
    client_id: process.env[cfg.clientIdEnv],
    client_secret: process.env[cfg.secretEnv] || '',
    redirect_uri: redirectUri,
  });
  const res = await fetch(cfg.tokenUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Token exchange failed (${res.status}): ${text.slice(0, 300)}`);
  }
  return await res.json();
}

/**
 * Per-instance monthly USD cost (mid-2025 on-demand averages, us-east-1
 * / eastus / us-central1). Used to populate node.metadata.monthly_cost
 * so the core engine's actuator can compute potential savings. A real
 * deployment would pull live pricing from each provider.
 */
const MONTHLY_COST = {
  // AWS
  't3.medium': 30, 't3.large': 60, 'm5.large': 70,
  'm5.xlarge': 140, 'm5.2xlarge': 280, 'r5.xlarge': 184,
  'r5.large': 92, 'c5.xlarge': 124, 'c5.2xlarge': 248,
  // Azure
  'Standard_B2ms': 61, 'Standard_B4ms': 122,
  'Standard_D2s_v3': 70, 'Standard_D4s_v3': 140, 'Standard_D8s_v3': 280,
  'Standard_E4s_v3': 184, 'Standard_E8s_v3': 368,
  'Standard_F4s_v2': 123,
  // GCP
  'e2-small': 12, 'e2-medium': 24, 'e2-standard-2': 49,
  'n2-standard-2': 71, 'n2-standard-4': 142, 'n2-standard-8': 284,
  'n2-highmem-4': 191, 'c2-standard-4': 153,
};

function costFor(instanceType, replicas = 1) {
  const unit = MONTHLY_COST[instanceType] || 50;
  return Math.round(unit * Math.max(1, replicas));
}

/**
 * Stub topology discovery. Returns a small but realistic-looking
 * dependency graph per provider with monthly_cost attached so the
 * actuator's savings estimate is non-zero. Replace per-provider with
 * real EC2/ECS/EKS, Azure Resource Graph/AKS, GCE/GKE/Cloud Run adapters.
 */
async function discoverTopology(provider /* , { access_token } */) {
  const layouts = {
    aws: {
      nodes: [
        { id: 'alb-prod', tier: 'edge', type: 'load_balancer', provider: 'aws', instance_type: 't3.medium', replicas: 2 },
        { id: 'api-prod', tier: 'core', type: 'ecs_service', provider: 'aws', instance_type: 'm5.large', replicas: 3 },
        { id: 'auth-prod', tier: 'core', type: 'ecs_service', provider: 'aws', instance_type: 'm5.large', replicas: 2 },
        { id: 'orders-prod', tier: 'critical', type: 'ecs_service', provider: 'aws', instance_type: 'm5.xlarge', replicas: 4 },
        { id: 'rds-prod', tier: 'critical', type: 'rds_postgres', provider: 'aws', instance_type: 'r5.xlarge', replicas: 1 },
        { id: 'cache-prod', tier: 'supporting', type: 'elasticache', provider: 'aws', instance_type: 't3.medium', replicas: 2 },
        { id: 'workers-prod', tier: 'edge', type: 'ecs_service', provider: 'aws', instance_type: 't3.large', replicas: 6 },
      ],
      edges: [
        ['alb-prod', 'api-prod', 1.0],
        ['api-prod', 'auth-prod', 0.8],
        ['api-prod', 'orders-prod', 1.0],
        ['orders-prod', 'rds-prod', 0.95],
        ['api-prod', 'cache-prod', 0.55],
        ['orders-prod', 'workers-prod', 0.4],
        ['api-prod', 'staging-svc', 0.15],
        ['api-prod', 'devtools-bastion', 0.05],
      ],
      // Peripheral/discretionary nodes — low centrality + low tier =>
      // CEI < 0.25 => 'low' classification => 'consolidate' recommendation
      // => actuator produces savings.
      lowExtras: [
        { id: 'staging-svc',     tier: 'discretionary', type: 'ecs_service', provider: 'aws', instance_type: 't3.medium', replicas: 2 },
        { id: 'devtools-bastion',tier: 'discretionary', type: 'ec2',         provider: 'aws', instance_type: 't3.large',  replicas: 1 },
        { id: 'archive-store',   tier: 'supporting',    type: 's3_lifecycle',provider: 'aws', instance_type: 't3.medium', replicas: 1 },
      ],
    },
    azure: {
      nodes: [
        { id: 'agw-prod', tier: 'edge', type: 'application_gateway', provider: 'azure', instance_type: 'Standard_B2ms', replicas: 2 },
        { id: 'aks-api', tier: 'core', type: 'aks_pod', provider: 'azure', instance_type: 'Standard_D4s_v3', replicas: 3 },
        { id: 'aks-auth', tier: 'core', type: 'aks_pod', provider: 'azure', instance_type: 'Standard_D2s_v3', replicas: 2 },
        { id: 'aks-orders', tier: 'critical', type: 'aks_pod', provider: 'azure', instance_type: 'Standard_D8s_v3', replicas: 4 },
        { id: 'sql-prod', tier: 'critical', type: 'sql_db', provider: 'azure', instance_type: 'Standard_E4s_v3', replicas: 1 },
        { id: 'redis-prod', tier: 'supporting', type: 'cache_redis', provider: 'azure', instance_type: 'Standard_B2ms', replicas: 2 },
      ],
      edges: [
        ['agw-prod', 'aks-api', 1.0],
        ['aks-api', 'aks-auth', 0.8],
        ['aks-api', 'aks-orders', 1.0],
        ['aks-orders', 'sql-prod', 0.95],
        ['aks-api', 'redis-prod', 0.5],
        ['aks-api', 'aks-staging', 0.12],
        ['aks-api', 'jumpbox', 0.04],
      ],
      lowExtras: [
        { id: 'aks-staging',  tier: 'discretionary', type: 'aks_pod',   provider: 'azure', instance_type: 'Standard_B2ms', replicas: 1 },
        { id: 'jumpbox',      tier: 'discretionary', type: 'vm',        provider: 'azure', instance_type: 'Standard_B2ms', replicas: 1 },
        { id: 'logs-archive', tier: 'supporting',    type: 'storage',   provider: 'azure', instance_type: 'Standard_B2ms', replicas: 1 },
      ],
    },
    gcp: {
      nodes: [
        { id: 'lb-prod', tier: 'edge', type: 'cloud_lb', provider: 'gcp', instance_type: 'e2-medium', replicas: 2 },
        { id: 'gke-api', tier: 'core', type: 'gke_pod', provider: 'gcp', instance_type: 'n2-standard-4', replicas: 3 },
        { id: 'gke-auth', tier: 'core', type: 'gke_pod', provider: 'gcp', instance_type: 'n2-standard-2', replicas: 2 },
        { id: 'gke-orders', tier: 'critical', type: 'gke_pod', provider: 'gcp', instance_type: 'n2-standard-8', replicas: 4 },
        { id: 'spanner-prod', tier: 'critical', type: 'spanner', provider: 'gcp', instance_type: 'n2-highmem-4', replicas: 1 },
        { id: 'memorystore', tier: 'supporting', type: 'cache_redis', provider: 'gcp', instance_type: 'e2-medium', replicas: 2 },
      ],
      edges: [
        ['lb-prod', 'gke-api', 1.0],
        ['gke-api', 'gke-auth', 0.8],
        ['gke-api', 'gke-orders', 1.0],
        ['gke-orders', 'spanner-prod', 0.95],
        ['gke-api', 'memorystore', 0.5],
        ['gke-api', 'gke-dev', 0.1],
        ['gke-api', 'cloud-shell', 0.04],
      ],
      lowExtras: [
        { id: 'gke-dev',      tier: 'discretionary', type: 'gke_pod', provider: 'gcp', instance_type: 'e2-medium', replicas: 1 },
        { id: 'cloud-shell',  tier: 'discretionary', type: 'compute', provider: 'gcp', instance_type: 'e2-small',  replicas: 1 },
        { id: 'cold-storage', tier: 'supporting',    type: 'storage', provider: 'gcp', instance_type: 'e2-medium', replicas: 1 },
      ],
    },
  };
  const layout = layouts[provider] || { nodes: [], edges: [] };
  // Merge the peripheral / discretionary "lowExtras" into the main
  // node list so the CEI pipeline sees them. They have low centrality
  // by construction (edge weight 0.04..0.15 -> small PageRank impact)
  // and discretionary tier (low governance risk), so several land in
  // the 'low' classification -> 'consolidate' -> non-zero savings.
  if (Array.isArray(layout.lowExtras)) {
    layout.nodes = [...layout.nodes, ...layout.lowExtras];
    delete layout.lowExtras;
  }
  // Enrich every node with a monthly_cost based on its instance_type
  // and replica count so the actuator can compute potential savings.
  layout.nodes = layout.nodes.map((n) => ({
    ...n,
    monthly_cost: costFor(n.instance_type, n.replicas),
  }));
  return {
    provider,
    discovered_at: new Date().toISOString(),
    source: isMockMode(provider) ? 'stub' : 'live (stubbed)',
    topology: layout,
    governance_template: {
      tiers: {
        critical: { min_replicas: 3, encryption_required: true },
        core: { min_replicas: 2, encryption_required: true },
        edge: { min_replicas: 2 },
        supporting: { min_replicas: 1 },
        discretionary: { min_replicas: 0 },
      },
      policies: [],
    },
  };
}

module.exports = {
  PROVIDERS,
  isMockMode,
  listProviders,
  getAuthorizeUrl,
  exchangeCodeForToken,
  discoverTopology,
};
