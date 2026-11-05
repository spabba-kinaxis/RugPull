"""Test agent: lists tools and calls read_document through the gateway, on a loop.

Prints what each listing contained, so the change is visible live:

    python -m rugpull_mcp.testbed.test_agent --gateway http://localhost:8081 \
        --route /mcp/demo --consumer agent-a --count 10 --interval 1
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

HEADERS = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}


def rpc(client: httpx.Client, url: str, method: str, params: dict, rid: int,
        headers: dict[str, str]) -> dict:
    r = client.post(url, headers=headers, timeout=30,
                    json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    r.raise_for_status()
    return r.json()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gateway", default="http://localhost:8081")
    ap.add_argument("--route", default="/mcp/demo")
    ap.add_argument("--consumer", default="agent-a")
    ap.add_argument("--token", default="", help="bearer token, if the gateway requires one")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args(argv)

    url = args.gateway.rstrip("/") + args.route
    headers = dict(HEADERS, **{"X-Consumer": args.consumer})
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    with httpx.Client() as client:
        rpc(client, url, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                        "clientInfo": {"name": "test-agent", "version": "1"}},
            0, headers)
        for i in range(1, args.count + 1):
            listing = rpc(client, url, "tools/list", {}, i, headers)
            tools = listing["result"]["tools"]
            desc = tools[0]["description"] if tools else ""
            print(f"[{time.strftime('%H:%M:%S')}] listing {i}: {len(tools)} tool(s) "
                  f"desc={desc!r}", flush=True)
            if tools:
                call = rpc(client, url, "tools/call",
                           {"name": tools[0]["name"], "arguments": {"path": "notes/q3.md"}},
                           1000 + i, headers)
                text = (call.get("result") or {}).get("content", [{}])[0].get("text", "")
                print(f"           call {tools[0]['name']} -> {text!r}", flush=True)
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
