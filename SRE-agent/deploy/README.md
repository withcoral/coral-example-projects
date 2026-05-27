# Deploying the SRE Agent

Kubernetes manifests for running the SRE Agent Slack bot. Configured for the
`coral-demos` namespace — rename in the manifests if that doesn't fit your
cluster (search-and-replace across `*.yaml` and `../scripts/demo_trigger_alert.sh`).

## Architecture note: no Service, no Ingress

The bot uses Slack **Socket Mode** — it opens an *outbound* WebSocket to Slack
and receives no inbound traffic. As a result there is intentionally **no
Service, Ingress, or LoadBalancer**: nothing needs to route traffic to the pod.

Socket Mode also means the Deployment must run **exactly one replica**. Multiple
replicas would each open a connection and double-process every Slack event.

## Before you apply

The image fields in `deployment.yaml` and `hello-service.yaml` are
placeholders (`<YOUR_REGISTRY>/...`). You need to:

1. Build the SRE agent image from the Dockerfile at the SRE-agent root
   (`docker build --platform linux/amd64 -t <reg>/coral-sre-agent:<tag> .`).
2. Build the demo `hello-service` image from `../demo-app/`
   (`docker build --platform linux/amd64 -t <reg>/hello-service:<tag> ./demo-app`).
3. Push both to a registry your cluster can pull from.
4. Substitute the `<YOUR_REGISTRY>/...:latest` references with your real
   image refs. For repeatable rollouts, pin by digest (`@sha256:...`) rather
   than by tag.

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
  --from-literal=DD_APPLICATION_KEY=... \
  --from-literal=DD_SITE=datadoghq.com \
  --from-literal=GITHUB_TOKEN=... \
  --from-literal=SENTRY_TOKEN=... \
  --from-literal=SENTRY_ORG=... \
  --from-literal=ALERTS_CHANNEL_ID=... \
  --from-literal=DATADOG_SLACK_APP_ID=...
```
