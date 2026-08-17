#!/usr/bin/env bash
# Deploy CloudOptimizer locally: minikube + terraform + devspace.
#
#   ./deploy.sh          deploy everything
#   ./deploy.sh destroy  tear it down (minikube itself is left running)
#   ./deploy.sh status   what is running and where
#
# The pipeline: start minikube -> build both images directly into minikube's
# container runtime (no registry involved; imagePullPolicy Never in the
# manifests guarantees the local build is what runs) -> terraform apply the
# stack -> wait for readiness -> print URLs. Iterate afterwards with
# `devspace dev`.
#
# Everything deployed is dev-grade (fixed credentials, no TLS) and the
# terraform config says so; production remains the platform's job.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$HERE/deploy/local"
PROFILE="${MINIKUBE_PROFILE:-minikube}"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

need() {
  command -v "$1" >/dev/null 2>&1 || fail "$1 is not installed. $2"
}

preflight() {
  say "Preflight"
  need minikube "brew install minikube"
  need terraform "brew install terraform"
  need docker "install Docker Desktop or a docker CLI"
  need kubectl "brew install kubernetes-cli (or use 'minikube kubectl')"
  # DevSpace is the inner loop, not the deploy; missing is a warning.
  command -v devspace >/dev/null 2>&1 \
    || echo "note: devspace not installed (brew install devspace) — deploy works without it; the live-sync dev loop needs it."
}

start_minikube() {
  say "Minikube"
  if minikube -p "$PROFILE" status >/dev/null 2>&1; then
    echo "profile '$PROFILE' already running"
  else
    minikube start -p "$PROFILE" --cpus=2 --memory=4g
  fi
  kubectl config use-context "$PROFILE" >/dev/null
}

build_images() {
  say "Building images into minikube's runtime"
  # `minikube image build` builds INSIDE the cluster's runtime: nothing to
  # push, nothing to pull, and imagePullPolicy Never in the manifests makes
  # "the image you just built is the image that runs" a guarantee rather
  # than a hope.
  # Repo-root context: the scenario datasets live at /scenarios and must
  # ship in the image (see core-engine/Dockerfile).
  minikube -p "$PROFILE" image build -f core-engine/Dockerfile \
    -t cloudoptimizer/core-engine:local "$HERE"
  minikube -p "$PROFILE" image build \
    --build-opt build-arg=NEXT_PUBLIC_CORE_ENGINE_URL=http://localhost:8000 \
    --build-opt build-arg=NEXT_PUBLIC_API_URL=http://localhost:8000 \
    -t cloudoptimizer/frontend:local "$HERE/frontend"
}

apply() {
  say "Terraform"
  terraform -chdir="$TF_DIR" init -input=false >/dev/null
  terraform -chdir="$TF_DIR" apply -input=false -auto-approve \
    -var "kube_context=$PROFILE"
}

wait_ready() {
  say "Waiting for readiness"
  kubectl -n cloudoptimizer rollout status deploy/postgres    --timeout=180s
  kubectl -n cloudoptimizer rollout status deploy/core-engine --timeout=300s
  kubectl -n cloudoptimizer rollout status deploy/frontend    --timeout=300s
}

urls() {
  say "Where everything is"
  cat <<EOF
  Run:   ./deploy.sh open

  ...then use:
    Dashboard  http://localhost:3000
    API        http://localhost:8000   (docs at /docs)

  Why the extra step: on Docker-driver minikube (the macOS default) the
  cluster's IP lives inside Docker's VM, so NodePort URLs like
  http://$(minikube -p "$PROFILE" ip):30300 time out from the host.
  'open' holds port-forwards on localhost instead, which always works.

  Next steps:
    1. ./deploy.sh open, then visit http://localhost:3000 and sign up.
    2. Create a cluster, copy its API key, feed it from any kubeconfig
       context (see docs/DEV.md).
    3. Iterate with:  devspace dev   (forwards the same ports itself)
EOF
}

open_tunnels() {
  say "Port-forwarding — leave this running; Ctrl-C to stop"
  # Announce readiness OUT LOUD. Without this, the terminal establishes the
  # tunnels and then sits silent -- which is correct behaviour that reads
  # exactly like a hang to anyone watching it. Observed live: a working
  # deployment reported as "stuck" because nothing said "you can go now".
  ( for _ in $(seq 1 60); do
      if curl -sfm 2 -o /dev/null http://localhost:3000/ \
         && curl -sfm 2 -o /dev/null http://localhost:8000/health; then
        printf '\n\033[1;32m✅ READY\033[0m — open \033[1mhttp://localhost:3000\033[0m in your browser.\n'
        printf '   (This terminal stays busy on purpose: it IS the connection.\n'
        printf '    Nothing more will print here. Ctrl-C when you are done.)\n\n'
        if [ "$(uname)" = "Darwin" ] && [ -z "${NO_BROWSER:-}" ]; then
          /usr/bin/open http://localhost:3000 || true
        fi
        exit 0
      fi
      sleep 2
    done
    printf '\n\033[31mstill not reachable after 120s\033[0m — check: ./deploy.sh status\n' ) &
  # kubectl port-forward pins the specific POD it resolves at startup and
  # dies when that pod is replaced -- which is every rollout, every image
  # rebuild, every devspace restart. Observed live: a forward bound to a pod
  # a rollout had just terminated failed on first connection with "No such
  # container". Each forward therefore runs in a reconnect loop: a dropped
  # forward re-resolves the service to the CURRENT pod within a second, and
  # a rollout costs one refresh in the browser instead of a dead terminal.
  trap 'kill 0' EXIT INT TERM
  ( while true; do
      kubectl --context "$PROFILE" -n cloudoptimizer \
        port-forward svc/core-engine 8000:8000 2>&1 \
        | grep --line-buffered -v "^Handling connection" || true
      echo "  [api] forward dropped (pod replaced?) — reconnecting"
      sleep 1
    done ) &
  ( while true; do
      kubectl --context "$PROFILE" -n cloudoptimizer \
        port-forward svc/frontend 3000:3000 2>&1 \
        | grep --line-buffered -v "^Handling connection" || true
      echo "  [dashboard] forward dropped (pod replaced?) — reconnecting"
      sleep 1
    done ) &
  wait
}

install_agent() {
  # The customer flow, locally: after signing up in the dashboard and
  # creating a cluster, install the agent into the (minikube) cluster with
  # the API key the dashboard showed. The agent then observes the cluster
  # and ships snapshots to the core engine over in-cluster DNS -- the same
  # path a real customer's agent takes to the hosted endpoint, minus TLS.
  local api_key="${1:-}"
  [ -n "$api_key" ] || fail "usage: ./deploy.sh agent <API_KEY>   (from the dashboard's 'create cluster' step)"

  say "Building the agent image into minikube (skipped if present)"
  minikube -p "$PROFILE" image ls | grep -q "cloudoptimizer/agent:local" \
    || minikube -p "$PROFILE" image build -t cloudoptimizer/agent:local "$HERE/agent"

  say "Installing the agent chart (read-only ClusterRole, as shipped)"
  helm upgrade --install cloudoptimizer-agent "$HERE/charts/cloudoptimizer-agent" \
    --kube-context "$PROFILE" \
    --namespace cloudoptimizer-agent --create-namespace \
    --set apiKey="$api_key" \
    --set endpoint="http://core-engine.cloudoptimizer.svc.cluster.local:8000" \
    --set image.repository=cloudoptimizer/agent \
    --set image.tag=local \
    --set image.pullPolicy=Never

  kubectl --context "$PROFILE" -n cloudoptimizer-agent rollout status \
    deploy --timeout=180s 2>/dev/null \
    || kubectl --context "$PROFILE" -n cloudoptimizer-agent get pods
  say "Done"
  cat <<EOF
  The agent pod is now observing this cluster and shipping snapshots.
  Within ~60s, refresh the dashboard: the cluster shows connected, with
  topology, CEI, health, cost, and (from the second snapshot) the drift
  rail. Follow the agent itself with:
    kubectl --context $PROFILE -n cloudoptimizer-agent logs -f -l app.kubernetes.io/name=cloudoptimizer-agent
EOF
}

demo_cluster() {
  # A production-shaped estate to point the agent at: twelve services across
  # two namespaces with REAL runtime dependencies (readiness probes check
  # upstream reachability, so failures genuinely cascade) and the flaws a
  # real estate carries -- a hub with no PodDisruptionBudget, a service
  # nobody owns on a :latest tag, an idle over-provisioned worker, a
  # single-replica user-facing gateway. Every dashboard panel gets something
  # true to say.
  say "Applying the production-sim demo estate"
  kubectl --context "$PROFILE" apply -f "$HERE/deploy/demo-cluster/production-sim.yaml"
  say "Waiting for the estate to settle (data tier first, then the cascade)"
  kubectl --context "$PROFILE" -n shop rollout status statefulset/orders-db --timeout=240s || true
  for d in session-cache catalog checkout payments search email-worker api-gateway; do
    kubectl --context "$PROFILE" -n shop rollout status "deploy/$d" --timeout=240s || true
  done
  kubectl --context "$PROFILE" -n platform rollout status deploy/legacy-ledger --timeout=240s || true
  say "Done"
  cat <<EOF
  The agent's next snapshot (within ~60s) picks the estate up. Refresh the
  dashboard: topology, CEI ranking, health, resilience findings, an
  ownerless-service finding, cost waste, remediation proposals, and
  prescriptions all light up from this estate. Remove it with:
    kubectl --context $PROFILE delete -f deploy/demo-cluster/production-sim.yaml
EOF
}

status() {
  kubectl -n cloudoptimizer get deploy,svc,pvc 2>/dev/null \
    || echo "nothing deployed (namespace 'cloudoptimizer' absent on context '$PROFILE')"
}

destroy() {
  say "Destroying the stack (minikube itself is left running)"
  terraform -chdir="$TF_DIR" destroy -input=false -auto-approve \
    -var "kube_context=$PROFILE" || true
  echo "done. 'minikube -p $PROFILE delete' removes the cluster entirely."
}

case "${1:-deploy}" in
  deploy)
    preflight
    start_minikube
    build_images
    apply
    wait_ready
    urls
    ;;
  open)    open_tunnels ;;
  agent)   install_agent "${2:-}" ;;
  demo-cluster) demo_cluster ;;
  destroy) destroy ;;
  status)  status ;;
  *) fail "unknown command '${1}'. Usage: ./deploy.sh [deploy|open|agent <API_KEY>|demo-cluster|destroy|status]" ;;
esac
