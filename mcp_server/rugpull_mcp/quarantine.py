"""Quarantine a changed MCP server by adding a Request Termination plugin to its route.

Uses the Konnect Admin API. Plugins created here carry the tag "mcp-quarantine",
so decK can be scoped to ignore them (see docs/PLAN.md). Endpoint shapes follow
the design doc; verify them against your Konnect version before production.
"""

from __future__ import annotations

import json
import logging
import os
import sys

import requests

log = logging.getLogger("rugpull_mcp.quarantine")

TAG = "mcp-quarantine"
MESSAGE = "Quarantined: MCP tool change pending review"


class QuarantineConfig:
    def __init__(self) -> None:
        self.api = os.environ.get("KONNECT_API", "")
        self.cp = os.environ.get("CONTROL_PLANE_ID", "")
        self.pat = os.environ.get("KONNECT_PAT", "")
        self.route_ids: dict[str, str] = json.loads(os.environ.get("ROUTE_IDS", "{}"))

    def route_url(self, server: str) -> str:
        if server not in self.route_ids:
            raise KeyError(f"no route id configured for server {server!r}")
        return (f"{self.api}/v2/control-planes/{self.cp}"
                f"/core-entities/routes/{self.route_ids[server]}/plugins")

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.pat}", "Content-Type": "application/json"}


def quarantine(server: str, cfg: QuarantineConfig | None = None) -> str | None:
    """Add the quarantine plugin unless one already exists. Returns its id."""
    cfg = cfg or QuarantineConfig()
    url = cfg.route_url(server)
    existing = _quarantine_plugins(cfg, url)
    if existing:
        return existing[0]["id"]
    body = {
        "name": "request-termination",
        "tags": [TAG],
        "config": {"status_code": 403, "message": MESSAGE},
    }
    r = requests.post(url, json=body, headers=cfg.headers(), timeout=10)
    r.raise_for_status()
    plugin_id = r.json().get("id")
    log.warning("quarantined server=%s plugin=%s", server, plugin_id)
    return plugin_id


def release(server: str, cfg: QuarantineConfig | None = None) -> int:
    """Delete every quarantine plugin on the server's route. Returns how many were deleted."""
    cfg = cfg or QuarantineConfig()
    url = cfg.route_url(server)
    deleted = 0
    for plugin in _quarantine_plugins(cfg, url):
        r = requests.delete(f"{url}/{plugin['id']}", headers=cfg.headers(), timeout=10)
        r.raise_for_status()
        deleted += 1
    log.warning("released server=%s deleted=%d", server, deleted)
    return deleted


def _quarantine_plugins(cfg: QuarantineConfig, url: str) -> list[dict]:
    r = requests.get(url, headers=cfg.headers(), timeout=10)
    r.raise_for_status()
    return [p for p in r.json().get("data", []) if TAG in (p.get("tags") or [])]


def main(argv: list[str]) -> int:
    """CLI: python -m rugpull_mcp.quarantine release <server>"""
    if len(argv) != 3 or argv[1] != "release":
        print("usage: python -m rugpull_mcp.quarantine release <server>", file=sys.stderr)
        return 2
    print(f"deleted {release(argv[2])} plugin(s)")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    raise SystemExit(main(sys.argv))
