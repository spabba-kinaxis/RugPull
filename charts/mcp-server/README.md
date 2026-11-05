# mcp-server Helm chart

Hosts one Model Context Protocol (MCP) server on Kubernetes over streamable HTTP.
The chart is generic: give it any MCP server image and it creates the Deployment,
Service, ServiceAccount, NetworkPolicy and optional ConfigMap, HPA, PodDisruptionBudget
and Ingress. Install it once per server.

## Install

```bash
# Create the secrets the server needs (never put them in a values file)
kubectl create namespace mcp-detector

helm upgrade --install rugpull-mcp ./charts/mcp-server \
  -n mcp-detector -f ./charts/mcp-server/examples/rugpull-mcp.yaml

helm test rugpull-mcp -n mcp-detector      # checks the health endpoint from inside the cluster
```

Examples in `examples/`:

| File | What it deploys |
| --- | --- |
| `rugpull-mcp.yaml` | The rug-pull detector's MCP server, with the baseline from a ConfigMap |
| `demo-mcp.yaml` | The malicious demo server. Demo only |
| `third-party.yaml` | A template for any other MCP server image |

## Values you must set

| Value | Meaning |
| --- | --- |
| `image.repository` | Image that runs the server. Required; the install fails without it |
| `image.tag` | Pin a version. Defaults to the chart's `appVersion` |
| `mcp.port` | Port the server listens on |
| `mcp.healthPath` | Path the probes call. Must return HTTP 200 with no token |
| `networkPolicy.allowedNamespaces` | Namespaces allowed to reach the server. The install fails if this is empty, so the server is never open to every pod |

## Configuration

- **Environment:** `env` (plain values) and `existingSecret` (every key of a Secret becomes an environment variable). Secrets are created out of band.
- **Files:** `configFiles` creates a ConfigMap, or `existingConfigMap` uses one you manage. Files are mounted at `configMount.path`. Changing `configFiles` rolls the pods.
- **Command:** `command` and `args` override the image entrypoint.
- **Scaling:** `replicaCount`, or `autoscaling.enabled` for an HPA.
- **Availability:** `podDisruptionBudget.enabled`.
- **Exposure:** the Service is `ClusterIP` by default. `ingress.enabled` is off, because an MCP server should sit behind the API gateway.

## Security defaults

- Non-root user 10001, read-only root filesystem, all capabilities dropped, RuntimeDefault seccomp.
- ServiceAccount token not mounted, since the server does not call the Kubernetes API.
- NetworkPolicy allows only the namespaces you list, on the server port.
- The Deployment has no privileged options.

## Checks

```bash
helm lint ./charts/mcp-server -f ./charts/mcp-server/examples/rugpull-mcp.yaml
helm template rugpull-mcp ./charts/mcp-server -n mcp-detector \
  -f ./charts/mcp-server/examples/rugpull-mcp.yaml | kubectl apply --dry-run=client -f -
```

The chart has not yet been rendered or linted in CI, because Helm was not available
in the environment where it was written. Run the two commands above before relying on it.
