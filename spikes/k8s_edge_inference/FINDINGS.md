# Spike 0.1 — Kubernetes edge inference → CEI

**Question:** does CEI, computed over edges inferable from read-only
Kubernetes state, produce a ranking a human would agree with?

**Answer:** yes for the graph, no for the recommendations. Edge inference
works better than assumed. The oscillation detector would disable the
product's recommendation engine on day one.

**Setup:** kind v1.29.2, Google Online Boutique v0.10.2 (12 workloads, 13
services), whose call graph is publicly documented. Reproduce with:

```bash
kind create cluster --name co-spike --image kindest/node:v1.29.2
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/v0.10.2/release/kubernetes-manifests.yaml
core-engine/.venv/bin/python spikes/k8s_edge_inference/infer.py --namespace default
```

---

## Finding 1 — env-var edge inference is accurate: 16/16, no false positives

| metric | result |
|---|---|
| recall | 16/16 (100%) |
| precision | 16/16 (100%) |

Every documented dependency was recovered from container env var values
alone, with no spurious edges. The heuristic even found
`loadgenerator → frontend` (via `FRONTEND_ADDR=frontend:80`) before the
hand-written ground-truth list included it.

This materially de-risks Phase 1. The main technical unknown going in was
whether a read-only agent could build a graph good enough for CEI to mean
anything without a service mesh or eBPF. On a conventional microservice
deployment, it can.

**Caveats before treating this as settled:**

- Online Boutique wires dependencies through env vars, which is idiomatic
  but not universal. Apps using hardcoded DNS names in config files, or
  service discovery via a mesh, will infer fewer edges. Confidence labels
  per edge are still needed.
- ConfigMap bodies were deliberately not read. Nothing was lost here.
- The address regex over-matches bare numbers: `PORT=8080` yields candidate
  host `8080`. Harmless (no service matches) but noisy in diagnostics; tighten
  before shipping.
- Inference runs off Deployment **specs**, not runtime state, so it works
  before pods are ready. Useful property: the topology map can render
  immediately after `helm install` rather than waiting for a metrics window.

## Finding 2 — the oscillation detector would disable the product on day one

`oscillating nodes: 12/12, suppression=True`

Every workload was flagged as oscillating, which activates system-wide
suppression, which blocks every recommendation. Same failure already
documented against all five NIW scenarios — now confirmed to reproduce on
live Kubernetes data.

Two independent causes, and both must be fixed:

1. **No real history exists**, so `DataCollector` falls through to
   `_generate_synthetic_history()` and fabricates Gaussian noise. (Also the
   root cause of the scenario telemetry never being used — see
   `core-engine/tests/golden/README.md`.)
2. **The detector is amplitude-blind.** It counts sign changes in the first
   derivative and normalizes by sample count, so any non-monotonic series
   scores 0.5–0.7 regardless of amplitude. A series with σ=0.001 scores
   *higher* than one with σ=0.05. Only perfectly flat or perfectly monotonic
   input scores zero, and real telemetry is neither.

Fixing (1) alone is not enough: real CPU telemetry is noisy, so it trips (2)
just as reliably as fabricated noise does.

**Consequence if shipped unfixed:** the product installs, renders a correct
topology map, and then reports "no action" for every workload forever.

## Finding 3 — centrality semantics need a deliberate decision

Ranking produced (composite of PageRank 0.35 / betweenness 0.30 / degree 0.20
/ closeness 0.15):

```
frontend               0.5794     checkoutservice        0.5031
productcatalogservice  0.4326     cartservice            0.3925
redis-cart             0.3479     shippingservice        0.3452
currencyservice        0.3444     recommendationservice  0.3385
adservice              0.3163     paymentservice         0.2889
emailservice           0.2764     loadgenerator          0.2746
```

Sane overall: `loadgenerator` ranks last (nothing depends on it), and the
shared backends land in the upper half.

`frontend` ranks first, which is either right or wrong depending on an
unresolved question: does centrality express **internal blast radius**
(frontend fails → no other service breaks → should rank low) or **user-facing
importance** (frontend fails → total outage → should rank first)? It has
in-degree 1 and out-degree 7, so PageRank says low and degree/betweenness say
high; the current blend happens to favor the latter.

Both answers are defensible. The product should choose one on purpose, since
this is the claim the whole wedge rests on — "we tell you the 5 that can take
your system down."

## Finding 4 — the degraded metrics path works

kind ships without metrics-server; `metrics_available: False` was detected
and the run continued on requested CPU/memory. Confirms the D5 design
assumption. Note that in this mode the numbers describe *allocation*, not
*usage*, which the UI has to say plainly — and that Phase 2's
requested-vs-used waste detection cannot function at all without
metrics-server, so installation guidance is a Phase 1 deliverable, not a
Phase 2 one.

## Implications for the plan

- **Week 1 unchanged.** Edge inference is validated; build the agent around
  env-var + selector + ingress inference as planned.
- **Week 3 gains scope.** Both oscillation causes must be fixed before the
  demo, or the demo shows a map with no recommendations. This was already
  budgeted as "fix the entropy problem"; it is larger than that, because the
  detector itself needs recalibrating against amplitude.
- **New decision needed:** centrality semantics (Finding 3), before CEI
  tuning in Week 3.
