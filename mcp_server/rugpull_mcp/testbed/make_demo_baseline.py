"""Record the approved baseline for the demo server, from its clean first listing.

    python -m rugpull_mcp.testbed.make_demo_baseline http://127.0.0.1:8500 /tmp/approved-tools.json

Resets the demo server first, so the first listing is the clean one.
"""

from __future__ import annotations

import json
import sys

import httpx

from rugpull_mcp import core


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    demo_url, out_path = argv[1].rstrip("/"), argv[2]
    httpx.post(f"{demo_url}/reset", timeout=5).raise_for_status()
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    r = httpx.post(f"{demo_url}/mcp", json=body, timeout=10)
    r.raise_for_status()
    tools = r.json()["result"]["tools"]
    baseline = {"demo-mcp": {"paginated": False, "tools": core.fingerprint(tools)}}
    with open(out_path, "w") as f:
        json.dump(baseline, f, indent=2, sort_keys=True)
    httpx.post(f"{demo_url}/reset", timeout=5).raise_for_status()  # listing counter back to zero
    print(f"baseline written to {out_path}: {[t['name'] for t in tools]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
