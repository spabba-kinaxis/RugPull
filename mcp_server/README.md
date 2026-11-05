# rugpull-mcp

Detects MCP tool descriptions that change after approval. The package contains:

- **MCP server** (`rugpull_mcp.server`): three tools for fingerprinting, comparing and
  preparing baseline entries. Used by reviewers and agents.
- **Ingest service** (`rugpull_mcp.ingest`): receives Kong HTTP Log records, detects
  changes against the baseline, and sends a Slack alert once per distinct change.
  Optionally quarantines the changed route.
- **Testbed** (`rugpull_mcp.testbed`): a deliberately malicious demo server, a gateway
  stand-in for Kong, and a test agent.

The full plan, status and remaining steps are in [`docs/PLAN.md`](docs/PLAN.md).

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

pytest -q                       # unit tests and end-to-end test (46 tests)
```

The end-to-end test starts the whole pipeline on free ports and checks the rug-pull
scenario. To run the pipeline by hand, start the services in separate terminals:

```bash
export PYTHONPATH=$PWD
python -m uvicorn rugpull_mcp.testbed.demo_server:app --port 8500           # demo MCP server, flips after 5 listings
python -m rugpull_mcp.testbed.make_demo_baseline http://127.0.0.1:8500 /tmp/approved-tools.json   # record the clean baseline
ROUTES='{"/mcp/demo":{"service":"demo-mcp","upstream":"http://127.0.0.1:8500/mcp"}}' \
  DETECTOR_URL=http://127.0.0.1:8502/ingest \
  python -m uvicorn rugpull_mcp.testbed.gateway_sim:app --port 8501          # Kong stand-in
BASELINE_PATH=/tmp/approved-tools.json python -m uvicorn rugpull_mcp.ingest:app --port 8502
python -m rugpull_mcp.testbed.test_agent --gateway http://localhost:8501 --count 10
```

Without `SLACK_WEBHOOK_URL`, alerts are written to the log.

To run only the MCP server (stdio or HTTP):

```bash
BASELINE_PATH=/tmp/approved-tools.json python -m rugpull_mcp            # http://localhost:8000/mcp
```

## Build and deploy

One image runs all roles. Only the start command differs.

```bash
docker build -t <registry>/rugpull-mcp:0.1.0 . && docker push <registry>/rugpull-mcp:0.1.0

# Replace <registry>, <client-namespace> and <kong-namespace> in the manifests, then:
kubectl apply -f k8s/mcp-server.yaml                  # namespace, baseline ConfigMap, MCP server
kubectl -n mcp-detector create secret generic rugpull-detector \
  --from-literal=SLACK_WEBHOOK_URL="$SLACK_WEBHOOK_URL" --from-literal=KONNECT_PAT="$KONNECT_PAT" \
  --from-literal=CONTROL_PLANE_ID="$CONTROL_PLANE_ID" --from-literal=KONNECT_API="$KONNECT_API" \
  --from-literal=ROUTE_IDS='{"demo-mcp":"<route uuid>"}'
kubectl apply -f k8s/detector.yaml                    # ingest service (Kong posts logs here)
kubectl apply -f k8s/demo.yaml                        # optional: demo server and gateway stand-in
kubectl -n mcp-detector rollout status deploy/rugpull-ingest
```

Kong configuration is in [`kong/kong.yaml`](kong/kong.yaml). Verify the field names
noted there before relying on them.

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

- The MCP server is stateless and does not de-duplicate alerts. The ingest service keeps
  that state in memory, so run it as a single replica.
- Kong, Konnect, Slack and Kubernetes were not available in the build environment.
  The end-to-end test uses a gateway stand-in and fake Slack and Konnect services.
  `docs/PLAN.md` lists what to verify.
- The baseline file is read on each call. Under Kubernetes, a ConfigMap update
  can take up to about a minute to reach the mounted file.
