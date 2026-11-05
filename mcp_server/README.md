# rugpull-mcp

An MCP server that exposes the core operations of the gateway-level MCP
rug-pull detector. It fingerprints tool listings, compares them with the approved
baseline, and prepares baseline entries for reviewed pull requests.

It is stateless and read-only. It never writes the baseline and never stores
payloads. Tool names and descriptions come from untrusted servers, so the
tool output treats them as data.

## Tools

| Tool | Purpose |
| --- | --- |
| `fingerprint_tools(listing)` | SHA-256 hash for each tool in a `tools/list` response. |
| `compare_tool_listing(server, listing, consumer?)` | Compares a listing with `approved-tools.json`. Returns `clean`, `changed` or `unapproved_server`, with `added`, `removed` and `changed` buckets. Changed descriptions include a sentence-level diff. |
| `build_baseline_entry(server, listing, paginated?)` | Builds the baseline record for a server and shows the change from the current baseline. Use it to prepare the approval PR. |

`listing` accepts the raw response body (JSON or `data:` SSE, as Kong logs it),
a parsed JSON-RPC response, a `{"tools": [...]}` result, or a bare list of tools.

The fingerprint is the same canonical SHA-256 as in the design doc, so hashes from
this server match hashes recorded by `scripts/make_baseline.py`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `BASELINE_PATH` | `/baseline/approved-tools.json` | Approved baseline (mounted ConfigMap). |
| `MCP_TRANSPORT` | `streamable-http` | `streamable-http` or `stdio`. |
| `MCP_HOST` | `0.0.0.0` | HTTP bind address. |
| `MCP_PORT` | `8000` | HTTP port. The MCP endpoint is `/mcp`. |
| `LOG_LEVEL` | `INFO` | Log level. |

HTTP routes: `/mcp` (MCP) and `/healthz` (probe).

## Run locally

```bash
cd mcp_server
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

pytest -q                       # unit tests

# Use an empty baseline or your own file:
echo '{}' > /tmp/approved-tools.json
BASELINE_PATH=/tmp/approved-tools.json python -m rugpull_mcp
# Endpoint: http://localhost:8000/mcp
```

## Build and deploy

```bash
docker build -t <registry>/rugpull-mcp:0.1.0 . && docker push <registry>/rugpull-mcp:0.1.0

# Edit <registry> and <client-namespace> in k8s/mcp-server.yaml, then:
kubectl apply -f k8s/mcp-server.yaml
kubectl -n mcp-detector rollout status deploy/rugpull-mcp
```

The Deployment runs as non-root with a read-only root filesystem and all
capabilities dropped. The NetworkPolicy allows ingress only from
`<client-namespace>`. Clients inside the cluster connect to:

```
http://rugpull-mcp.mcp-detector.svc.cluster.local:8000/mcp
```

Example registration for Claude Code:

```bash
claude mcp add --transport http rugpull http://rugpull-mcp.mcp-detector.svc.cluster.local:8000/mcp
```

## Approval flow

1. Call `build_baseline_entry` with a listing that a reviewer has checked.
2. Merge the returned `entry` under the server name in `approved-tools.json`.
3. Open a pull request. A second reviewer approves it.
4. The pipeline replaces the `mcp-baseline` ConfigMap. The server reads the
   baseline on each request, so the new baseline applies without a restart.

## Limitations

- This server is a comparison and preparation service. It does not watch Kong
  logs and does not quarantine anything. The Kong HTTP Log ingest service and the
  quarantine stretch goal from the design doc are separate components.
- `compare_tool_listing` does not de-duplicate alerts. The ingest service keeps
  that state.
- The baseline file is read on each call. Under Kubernetes, a ConfigMap update
  can take up to about a minute to reach the mounted file.
