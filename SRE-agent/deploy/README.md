# Deploying the SRE Agent

Kubernetes manifests for running the SRE Agent Slack bot in the `coral-demos`
namespace.

## Architecture note: no Service, no Ingress

The bot uses Slack **Socket Mode** — it opens an *outbound* WebSocket to Slack
and receives no inbound traffic. As a result there is intentionally **no
Service, Ingress, or LoadBalancer**: nothing needs to route traffic to the pod.

Socket Mode also means the Deployment must run **exactly one replica**. Multiple
replicas would each open a connection and double-process every Slack event.

## Apply order

```sh
# 1. Namespace
kubectl apply -f namespace.yaml

# 2. Secret — create the REAL secret (see below). Do NOT apply secret.example.yaml.

# 3. Deployment
kubectl apply -f deployment.yaml
```

## Creating the real Secret

`secret.example.yaml` is a template only — it documents the required keys with
placeholder values. Create the real Secret out-of-band so credentials never
land in version control:

```sh
kubectl create secret generic sre-agent-secrets \
  --namespace coral-demos \
  --from-literal=ANTHROPIC_API_KEY=... \
  --from-literal=SLACK_BOT_TOKEN=... \
  --from-literal=SLACK_APP_TOKEN=... \
  --from-literal=DD_API_KEY=... \
  --from-literal=DD_APP_KEY=... \
  --from-literal=DD_SITE=datadoghq.com \
  --from-literal=GITHUB_TOKEN=... \
  --from-literal=SENTRY_TOKEN=... \
  --from-literal=SENTRY_ORG=... \
  --from-literal=ALERTS_CHANNEL_ID=... \
  --from-literal=DATADOG_SLACK_APP_ID=...
```

## Container image

The image is built from a Dockerfile (added separately) and pushed to Amazon
ECR. Replace the `<ECR_REPO>:<TAG>` placeholder in `deployment.yaml` with the
real image reference before deploying.
