# Local deployment of CloudOptimizer onto minikube.
#
# Terraform here manages Kubernetes objects, not cloud accounts: the same
# declarative workflow that will drive a production cluster, pointed at the
# minikube context. deploy.sh builds the images into minikube's runtime and
# then applies this.
#
# Everything in this file is dev-grade on purpose (fixed credentials, one
# postgres replica, no TLS) and none of it may travel to production.

terraform {
  required_version = ">= 1.5"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
  }
}

provider "kubernetes" {
  config_path    = "~/.kube/config"
  config_context = var.kube_context
}

variable "kube_context" {
  description = "Kubeconfig context to deploy into."
  type        = string
  default     = "minikube"
}

variable "app_secret_key" {
  description = "Session-signing secret. Dev default; override for anything shared."
  type        = string
  default     = "dev-only-secret-do-not-use-in-production-0000"
  sensitive   = true
}

locals {
  namespace = "cloudoptimizer"
  pg_user   = "cloudopt"
  pg_pass   = "devpass" # dev-grade by design; see header
  pg_db     = "cloudoptimizer"
}

resource "kubernetes_namespace" "cloudoptimizer" {
  metadata {
    name   = local.namespace
    labels = { "app.kubernetes.io/managed-by" = "terraform" }
  }
}

# ---------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------

resource "kubernetes_secret" "postgres" {
  metadata {
    name      = "postgres-credentials"
    namespace = local.namespace
  }
  data = {
    POSTGRES_USER     = local.pg_user
    POSTGRES_PASSWORD = local.pg_pass
    POSTGRES_DB       = local.pg_db
  }
  depends_on = [kubernetes_namespace.cloudoptimizer]
}

resource "kubernetes_persistent_volume_claim" "postgres" {
  metadata {
    name      = "postgres-data"
    namespace = local.namespace
  }
  spec {
    access_modes = ["ReadWriteOnce"]
    resources {
      requests = { storage = "2Gi" }
    }
  }
  depends_on = [kubernetes_namespace.cloudoptimizer]
}

resource "kubernetes_deployment" "postgres" {
  metadata {
    name      = "postgres"
    namespace = local.namespace
    labels    = { app = "postgres" }
  }
  spec {
    replicas = 1
    # Recreate, not RollingUpdate: two postgres pods sharing one RWO volume
    # deadlocks the rollout.
    strategy { type = "Recreate" }
    selector { match_labels = { app = "postgres" } }
    template {
      metadata { labels = { app = "postgres" } }
      spec {
        container {
          name  = "postgres"
          image = "postgres:16-alpine"
          env_from {
            secret_ref { name = kubernetes_secret.postgres.metadata[0].name }
          }
          port { container_port = 5432 }
          volume_mount {
            name       = "data"
            mount_path = "/var/lib/postgresql/data"
            sub_path   = "pgdata"
          }
          readiness_probe {
            exec { command = ["pg_isready", "-U", local.pg_user, "-d", local.pg_db] }
            period_seconds        = 3
            initial_delay_seconds = 3
          }
          resources {
            requests = { cpu = "100m", memory = "256Mi" }
          }
        }
        volume {
          name = "data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.postgres.metadata[0].name
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "postgres" {
  metadata {
    name      = "postgres"
    namespace = local.namespace
  }
  spec {
    selector = { app = "postgres" }
    port {
      port        = 5432
      target_port = 5432
    }
  }
}

# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

resource "kubernetes_secret" "core_engine" {
  metadata {
    name      = "core-engine-config"
    namespace = local.namespace
  }
  data = {
    DATABASE_URL   = "postgres://${local.pg_user}:${local.pg_pass}@postgres:5432/${local.pg_db}"
    APP_SECRET_KEY = var.app_secret_key
  }
  depends_on = [kubernetes_namespace.cloudoptimizer]
}

resource "kubernetes_deployment" "core_engine" {
  metadata {
    name      = "core-engine"
    namespace = local.namespace
    labels    = { app = "core-engine" }
  }
  spec {
    replicas = 1
    selector { match_labels = { app = "core-engine" } }
    template {
      metadata { labels = { app = "core-engine" } }
      spec {
        container {
          name = "core-engine"
          # Built by deploy.sh directly into minikube's container runtime;
          # Never means "use that build", not "pull from a registry that
          # does not have it".
          image             = "cloudoptimizer/core-engine:local"
          image_pull_policy = "Never"
          env_from {
            secret_ref { name = kubernetes_secret.core_engine.metadata[0].name }
          }
          port { container_port = 8000 }
          readiness_probe {
            http_get {
              path = "/health"
              port = 8000
            }
            period_seconds        = 5
            initial_delay_seconds = 5
            failure_threshold     = 12
          }
          resources {
            requests = { cpu = "250m", memory = "512Mi" }
          }
        }
      }
    }
  }
  depends_on = [kubernetes_deployment.postgres]
}

resource "kubernetes_service" "core_engine" {
  metadata {
    name      = "core-engine"
    namespace = local.namespace
  }
  spec {
    selector = { app = "core-engine" }
    type     = "NodePort"
    port {
      port        = 8000
      target_port = 8000
      node_port   = 30800
    }
  }
}

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

resource "kubernetes_deployment" "frontend" {
  metadata {
    name      = "frontend"
    namespace = local.namespace
    labels    = { app = "frontend" }
  }
  spec {
    replicas = 1
    selector { match_labels = { app = "frontend" } }
    template {
      metadata { labels = { app = "frontend" } }
      spec {
        container {
          name              = "frontend"
          image             = "cloudoptimizer/frontend:local"
          image_pull_policy = "Never"
          env {
            name  = "PORT"
            value = "3000"
          }
          port { container_port = 3000 }
          readiness_probe {
            http_get {
              path = "/"
              port = 3000
            }
            period_seconds        = 5
            initial_delay_seconds = 5
            failure_threshold     = 12
          }
          resources {
            requests = { cpu = "100m", memory = "256Mi" }
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "frontend" {
  metadata {
    name      = "frontend"
    namespace = local.namespace
  }
  spec {
    selector = { app = "frontend" }
    type     = "NodePort"
    port {
      port        = 3000
      target_port = 3000
      node_port   = 30300
    }
  }
}

output "api_url" {
  value = "http://$(minikube ip):30800 — or `minikube service -n cloudoptimizer core-engine --url`"
}

output "frontend_url" {
  value = "http://$(minikube ip):30300 — or `minikube service -n cloudoptimizer frontend --url`"
}
