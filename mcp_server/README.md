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
| `INGEST_TOKEN` | (required) | Ingest service: token Kong sends as `Authorization: Bearer ...`. |
| `INGEST_ALLOW_UNAUTHENTICATED` | `false` | Ingest service: accept records without a token. Local use only. |
| `ALERT_CACHE_SIZE` | `10000` | Ingest service: distinct alerts remembered. |
| `MCP_HOST` | `0.0.0.0` | HTTP bind address. |
| `MCP_PORT` | `8000` | HTTP port. The MCP endpoint is `/mcp`. |
| `LOG_LEVEL` | `INFO` | Log level. |

HTTP routes: `/mcp` (MCP) and `/healthz` (probe).

## Run locally

```bash
cd mcp_server
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

pytest -q                       # unit tests and end-to-end test (65 tests)
```

The end-to-end test starts the whole pipeline on free ports and checks the rug-pull
scenario. To run the pipeline by hand, use four terminals from `mcp_server/`. Each
service must be up before the next step uses it.

```bash
# Terminal 1: the malicious demo server (its description flips on listing 6)
export PYTHONPATH=$PWD
python -m uvicorn rugpull_mcp.testbed.demo_server:app --port 8500

# Terminal 2 (wait until terminal 1 says "Uvicorn running"): record the clean baseline
python -m rugpull_mcp.testbed.make_demo_baseline http://127.0.0.1:8500 /tmp/approved-tools.json

# Terminal 2: the ingest service. The token is required. Use any value, but use the same one below.
INGEST_TOKEN=devtoken BASELINE_PATH=/tmp/approved-tools.json BASELINE_TTL_SECONDS=1 \
  python -m uvicorn rugpull_mcp.ingest:app --port 8502

# Terminal 3: the gateway stand-in (Kong's role), which forwards traffic and sends log records
ROUTES='{"/mcp/demo":{"service":"demo-mcp","upstream":"http://127.0.0.1:8500/mcp"}}' \
  DETECTOR_URL=http://127.0.0.1:8502/ingest DETECTOR_TOKEN=devtoken \
  python -m uvicorn rugpull_mcp.testbed.gateway_sim:app --port 8501

# Terminal 4: the test agent. The first 5 listings are clean, the 6th and later are poisoned.
python -m rugpull_mcp.testbed.test_agent --gateway http://127.0.0.1:8501 --count 8 --interval 1
```

Expected: the agent prints 8 listings, and terminal 2 logs one
`ALERT ... MCP tool change on demo-mcp, received by consumer agent-a`. Then check the
ingest stats:

```bash
curl -s localhost:8502/stats          # tool_listings: 8, alerts: 1
```

Checks you can run by hand:

```bash
# Refused without a token (expect 401)
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8502/ingest -H 'Content-Type: application/json' -d '{}'

# Probes (expect {"ok":true})
curl -s localhost:8502/healthz; curl -s localhost:8501/healthz
```

Without `SLACK_WEBHOOK_URL`, alerts are written to the log. Every ingest request needs
the token, so the token in the ingest command and the gateway command must match.

To run only the MCP server (stdio or HTTP):

```bash
BASELINE_PATH=/tmp/approved-tools.json python -m rugpull_mcp            # http://localhost:8000/mcp
```

## Local agent

`agent/` is a small agent that uses the MCP tools through the gateway. The model is
Anthropic's Messages API (default `claude-haiku-5-5`, set with `AGENT_MODEL`). The
API key is read from `ANTHROPIC_API_KEY`.

```bash
pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=...          # your key; not stored in the repo

# With the demo stack from above running on port 8501 (gateway):
python -m agent --mcp-url http://127.0.0.1:8501/mcp/demo --consumer agent-a \
  --prompt "What is the weather in Oslo?" --verbose
```

Without `--prompt`, the agent runs as a chat. `--verbose` prints the tool trace.
Tool descriptions and results come from MCP servers, so the agent treats them as data.
The loop is tested with fakes (`tests/test_agent.py`). The Anthropic call itself has not
been run here, because no API key was available.

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
# Or install the MCP servers with the Helm chart instead of k8s/mcp-server.yaml:
#   helm upgrade --install rugpull-mcp ../charts/mcp-server -n mcp-detector -f ../charts/mcp-server/examples/rugpull-mcp.yaml
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
