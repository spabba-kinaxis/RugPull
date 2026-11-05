# RugPull

Gateway-level detection of MCP rug pulls.

An MCP server can pass review with harmless tool descriptions, then change them later to add hidden instructions. Scheduled scanners and client-side pinning miss changes made between checks. This project watches every `tools/list` response that passes through the API gateway (Kong), compares it with an approved baseline kept in git, and alerts within seconds when a tool was added, removed or changed.

## What is in this repo

| Path | What it is |
| --- | --- |
| [`mcp_server/`](mcp_server/) | The detector: MCP server, ingest service, alerts, quarantine, testbed, tests, Dockerfile and Kubernetes manifests |
| [`mcp_server/docs/PLAN.md`](mcp_server/docs/PLAN.md) | Status of each component, the phased plan to production, and open decisions |
| [`mcp_server/kong/kong.yaml`](mcp_server/kong/kong.yaml) | decK config for the MCP routes and the HTTP Log plugin |
| [`mcp_server/k8s/`](mcp_server/k8s/) | Kubernetes manifests for the MCP server, the ingest service, and an optional demo |
| [`mcp_server/agent/`](mcp_server/agent/) | A local agent that uses the MCP tools through the gateway, with a Claude model |
| [`charts/mcp-server/`](charts/mcp-server/) | Reusable Helm chart that hosts any MCP server on Kubernetes |

## How it works

```
agent ──► Kong (routes, auth) ──► MCP server
              │
              └─ HTTP Log record, after the response ─► ingest service
                                                          │
                       fingerprint each tool ◄────────────┤
                       compare with approved-tools.json   │
                                                          ├─► Slack alert (server, consumer, diff)
                                                          └─► quarantine route (optional)
```

- **Detection, not prevention.** The detector sits beside the traffic. Agents are never blocked by it, and an outage of the detector never blocks them either.
- **Baseline in git.** Each approved server and tool is recorded in `approved-tools.json`. Every approval is a reviewed pull request.
- **Whole-tool fingerprints.** Each tool is hashed over its complete definition. A poisoned title, schema or metadata field is caught, not only the description.
- **Unverifiable is an alert.** A `tools/list` response that cannot be parsed is reported, not skipped.

## Quick start

The detector is Python 3.11+. Tests run without Kong, Slack or a cluster.

```bash
cd mcp_server
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                           # 65 tests, including an end-to-end run
```

The end-to-end test starts the full pipeline on free ports, flips a demo tool's description, and checks that exactly one alert arrives within 10 seconds. The manual steps are in [`mcp_server/README.md`](mcp_server/README.md).

## Status

The code is complete and tested locally. Several parts still need a real environment before production:

- **Kong**: the log field that holds the response body, the consumer field, and the vault syntax for the ingest token are unverified.
- **Konnect**: quarantine endpoint shapes follow the design doc and are unverified.
- **Container image and Kubernetes**: the Dockerfile and manifests are written but have not been built or applied.
- **Slack**: tested against a fake webhook only.

Open decisions, including how baseline capture should avoid a privileged client, are listed in [`mcp_server/docs/PLAN.md`](mcp_server/docs/PLAN.md#5-open-questions-for-the-team).

## Limitations

- Changes are detected after the agent has received the listing. The alert arrives in seconds, but the agent may already have seen the change.
- Quarantine blocks the whole route, not a single tool, and only the route it is applied to.
- Tool results, prompts and resources are not monitored.
- Stdio servers never pass through the gateway and are out of scope.

## Documents

- [`mcp_server/README.md`](mcp_server/README.md): tools, configuration, running locally, deploying.
