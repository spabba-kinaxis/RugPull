# End-to-end development plan: rug-pull detector

Source: *Gateway-Level MCP Rug-Pull Detector* design doc (Nov 4, 2026).

## 1. Where things stand

| Component | Path | Status |
| --- | --- | --- |
| Detection core: canonical SHA-256, SSE parsing, compare, diffs | `rugpull_mcp/core.py` | Built, unit tested |
| MCP server with three tools | `rugpull_mcp/server.py` | Built, tested over streamable HTTP with the MCP client |
| Baseline loader | `rugpull_mcp/baseline.py` | Built, tested |
| Ingest service (Kong HTTP Log receiver) | `rugpull_mcp/ingest.py` | Built, unit and end-to-end tested |
| Slack alerts | `rugpull_mcp/alerts.py` | Built, tested with a fake webhook |
| Quarantine via Konnect Admin API | `rugpull_mcp/quarantine.py` | Built, tested against a fake API. Endpoint shapes unverified against live Konnect |
| Demo server (malicious flip after 5 listings) | `rugpull_mcp/testbed/demo_server.py` | Built, tested end to end |
| Gateway stand-in for Kong + HTTP Log | `rugpull_mcp/testbed/gateway_sim.py` | Built, tested end to end. Replaced by Kong in production |
| Test agent | `rugpull_mcp/testbed/test_agent.py` | Built, manually run |
| Kubernetes manifests | `k8s/` | Written, YAML parses. Not applied to a cluster |
| Kong decK config | `kong/kong.yaml` | Written. Not synced to Konnect |
| Container image | `Dockerfile` | Written. Not built (no Docker daemon here) |

**Verified here:** 46 tests pass (`pytest`). They include a full end-to-end run with
separate processes: the test agent, gateway stand-in, demo server, ingest service,
and a fake Slack and Konnect. Measured alert latency after the description flip:
about 0.2 s, against the 10 s target.

**Not verified here:** Kong itself (log field names, the MCP body setting, the auth
plugin), Konnect, Slack, Azure Firewall, Kubernetes, and the Docker image build.

## 2. Architecture as built

```
agent ──► gateway-sim (Kong stand-in) ──► demo-mcp (or real MCP server)
              │
              └─ log record, after response ─► rugpull-ingest ──► Slack webhook
                                                   │
                                                   └─► Konnect Admin API (quarantine, stretch)

reviewer ──► rugpull-mcp tools (fingerprint / compare / build entry) ──► PR to approved-tools.json
                                                                          │
                                       pipeline ──► ConfigMap mcp-baseline ◄┘
```

The ingest service and the MCP server share `core.py` and `baseline.py`. Hashes match
across the components because they use the same canonical form.

## 3. Remaining work, in order

Each phase ends with a check. A phase is done when its check passes.

### Phase A: Verify the Kong assumptions (blocking for production)
1. Deploy a non-production Kong data plane with `kong/kong.yaml`.
2. Send one `tools/list` through the route. Inspect the HTTP Log record.
3. Set `RESPONSE_BODY_PATH` to where the body appears. If bodies are not logged, use the
   sidecar fallback from the design doc. The ingest code does not change.
4. Confirm the consumer name appears at `consumer.username`. Adjust the code if not.
- **Check:** a real log record reaches `/ingest`, and `/stats` shows `tool_listings` rising.

### Phase B: Deploy to a non-production cluster
1. Build and push the image: `docker build -t <registry>/rugpull-mcp:0.1.0 .`
2. Apply `k8s/mcp-server.yaml`, then create the `rugpull-detector` secret, then apply `k8s/detector.yaml`.
3. Add `hooks.slack.com` and the Konnect API host to the Azure Firewall allowlist.
4. Optional for the demo: apply `k8s/demo.yaml`.
- **Check:** `/healthz` returns OK on both services. A clean listing produces no alert.

### Phase C: Demo acceptance (the design doc's success criteria)
Run the test cases from the design doc against the cluster:

| Test | Expected |
| --- | --- |
| Unchanged listing, three times | No alert |
| Description flips on listing 6 | One alert within 10 s, with a diff showing the added sentence |
| Ten more listings | Still one alert |
| Second, unapproved server | One alert listing all its tools as added |
| Approval PR merges, ConfigMap updates | No further alerts within 2 minutes |

The end-to-end test in `tests/e2e/test_e2e.py` runs the same scenario locally.

### Phase D: Baseline approval automation
1. Use `build_baseline_entry` to prepare each approval PR.
2. Require a second reviewer on `main`.
3. Add a pipeline job that applies the `mcp-baseline` ConfigMap on merge.
- **Check:** merging a baseline change stops the matching alert.

### Phase E: Quarantine (stretch goal)
1. Create a scoped Konnect PAT for the non-production control plane only.
2. Set `QUARANTINE_ENABLED=true` and fill in `ROUTE_IDS`.
3. Confirm the Konnect endpoint shapes in `quarantine.py` against the current API docs.
4. Make decK ignore `mcp-quarantine` items (`--select-tag`, as in `kong/kong.yaml`).
5. Release with `python -m rugpull_mcp.quarantine release <server>` after the baseline merges.
- **Check:** the next `tools/call` to the changed route returns 403.

### Phase F: Hardening and observability
- Add Datadog metrics from `/stats`, plus an alert when `tool_listings` stays at zero (the design doc's detector-down signal).
- Add a route-level alert for the `/healthz` probe.
- Add an alert-state store (Redis or a ConfigMap) if the ingest service must scale past one replica.
- Review the Konnect PAT scope and rotate the secret after the hackathon.

## 4. Decisions made here

- **One image, three roles.** The MCP server, ingest service and demo all ship in one
  image, with different start commands. This keeps one build and one version.
- **Gateway stand-in for local runs.** `gateway_sim` lets the full pipeline run without
  Kong. It is clearly labelled and is not used in production.
- **Duplicate suppression is per distinct change.** The key is the server, status, and
  the set of observed tool hashes. A reverted change alerts again.
- **Ingest always answers 200.** Kong does not retry on errors, and a bad record cannot
  stall the log pipeline.
- **Ingest runs as one replica.** Alert state is in memory, so two replicas could each
  alert once per change.
- **Quarantine is idempotent.** It finds an existing `mcp-quarantine` plugin before adding one.

## 5. Open questions for the team

1. Which Kong version and which AI MCP plugin setting puts the response body in the log record?
2. Which auth plugin maps agent tokens to consumers, and what name does it set?
3. Does the Konnect control plane allow decK `--select-tag`, or should quarantine open a PR instead?
4. Which Slack channel and which on-call reviewers receive alerts?
