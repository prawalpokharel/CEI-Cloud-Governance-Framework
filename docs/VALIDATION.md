# Week 4 — multi-cloud validation runbook

Verifies one Helm install behaves identically on EKS, AKS, and GKE.

**This is yours to run, not mine.** It requires cloud credentials, and I
neither need nor should have them. Everything below is designed so each
provider costs under an hour and the clusters are deleted the same day.

Validated already on kind (v1.29.2, arm64): agent connects, RBAC sufficient,
16/16 dependency edges inferred, metrics-server data flowing, dashboard
renders. What remains is confirming the same holds on managed control planes.

---

## Before you start

Publish the image once (the chart's default repository points at it):

```bash
git tag agent-v0.1.0 && git push origin agent-v0.1.0
```

That triggers `.github/workflows/release-agent.yml`, which builds
linux/amd64 + linux/arm64 and fails if either architecture is missing from
the manifest.

Set budget alerts on all three accounts first. A forgotten GPU node pool is
the expensive failure mode here, not the control plane.

---

## Per-provider cluster creation

Smallest viable cluster in each case — this validates the agent, not scale.

### EKS

```bash
eksctl create cluster --name co-validate --region us-east-1 \
  --nodes 2 --node-type t3.medium --managed
```

~15 minutes. **Metrics-server is not installed by default on EKS** — this is
the single most likely surprise:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

### AKS

```bash
az aks create --resource-group co-validate --name co-validate \
  --node-count 2 --node-vm-size Standard_B2s --generate-ssh-keys
az aks get-credentials --resource-group co-validate --name co-validate
```

~10 minutes. Metrics-server **is** preinstalled.

### GKE

```bash
gcloud container clusters create co-validate \
  --zone us-central1-a --num-nodes 2 --machine-type e2-medium
```

~8 minutes. Metrics-server is preinstalled.

> **Autopilot caveat:** if you use Autopilot rather than Standard, expect the
> install to differ. Autopilot rejects some pod configurations and manages
> `kube-system` itself, so the agent may not be able to read every namespace.
> Validate on Standard first so a failure is attributable to the agent rather
> than to Autopilot's constraints.

---

## The run, per provider

```bash
# 1. Register the cluster in the dashboard, copy the key
# 2. Install
helm install cloudoptimizer oci://ghcr.io/prawalpokharel/charts/cloudoptimizer-agent \
  --namespace cloudoptimizer --create-namespace \
  --set apiKey=co_live_xxxxx

# 3. Watch it connect
kubectl -n cloudoptimizer logs -f deploy/cloudoptimizer-cloudoptimizer-agent
```

### What to check

| Check | Expected | If it fails |
|---|---|---|
| Pod reaches `Running`, 0 restarts | — | `kubectl describe pod` — usually image arch or resource limits |
| Log line `Kubernetes credentials loaded from in-cluster` | — | ServiceAccount not bound |
| Log line `Collected seq=1: …` | non-zero workloads and services | RBAC — check for 403s in the log |
| `metrics=yes` | on all three | metrics-server missing (expected on EKS until installed) |
| Provider detected | `eks` / `aks` / `gke` | `detect_provider()` needs a new label pattern — worth fixing, it is cosmetic but visible |
| Edges inferred | non-zero on a cluster with real apps | a bare cluster has nothing to infer; deploy Online Boutique to get a meaningful number |
| Dashboard shows cluster as `connected` | within ~60s | check egress restrictions |
| CEI ranking looks sane | shared backends above leaf services | if not, capture the snapshot and open an issue |

Deploy a known app so there is something to infer:

```bash
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/v0.10.2/release/kubernetes-manifests.yaml
```

Expect **16 edges** — the number the agent produces on kind for the same
manifest. A different number on managed Kubernetes is a finding worth
chasing, not a rounding difference.

### Capture before tearing down

- Screenshot: dashboard cluster list showing all three connected
- Screenshot: topology map per provider
- `kubectl -n cloudoptimizer logs deploy/... > logs-<provider>.txt`
- Note anything provider-specific for the docs

---

## Tear down the same day

```bash
eksctl delete cluster --name co-validate --region us-east-1
az aks delete --resource-group co-validate --name co-validate --yes
gcloud container clusters delete co-validate --zone us-central1-a --quiet
```

Then confirm in each console that node groups are actually gone — a deleted
cluster occasionally leaves an orphaned node group billing quietly.

---

## Known unknowns

Things that cannot be verified on kind and are the real reason to run this:

1. **Managed control planes rate-limit list calls.** The agent lists six
   resource types per cycle. On a large cluster with an aggressive interval
   this could hit throttling that kind never exhibits.
2. **`kube-system` UID stability across upgrades.** Cluster identity keys on
   it. A managed control-plane upgrade should not recreate the namespace, but
   this is worth confirming before it matters in production.
3. **Egress policy.** Some clusters restrict outbound traffic; the agent needs
   HTTPS to the ingest endpoint and nothing else.
4. **Provider detection labels.** `detect_provider()` matches on `providerID`
   prefixes and known label prefixes. Managed offerings change these.
