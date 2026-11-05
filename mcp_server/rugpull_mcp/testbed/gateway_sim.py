"""Stand-in for Kong with the HTTP Log plugin, for local runs without a cluster.

Like Kong, it forwards each request to the upstream MCP server unchanged, returns
the upstream response to the caller, and POSTs a log record (service name,
consumer, request and response body) to the detector. The log record is sent
after the response, so the detector never adds latency to the agent.

Consumer identity comes from the X-Consumer header here. Kong derives it from
the auth plugin instead.

Configuration:
    ROUTES          JSON map of path prefix to {"service": name, "upstream": url}
    DETECTOR_URL    ingest endpoint, e.g. http://127.0.0.1:8080/ingest (optional)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response

log = logging.getLogger("gateway_sim")

ROUTES: dict[str, dict[str, str]] = json.loads(os.environ.get("ROUTES", "{}"))
DETECTOR_URL = os.environ.get("DETECTOR_URL", "")
HOP_BY_HOP = {"host", "content-length", "connection", "accept-encoding"}

app = FastAPI(title="gateway-sim")


def _match(path: str) -> tuple[str, dict[str, str]] | None:
    for prefix, route in ROUTES.items():
        if path == prefix or path.startswith(prefix + "/"):
            return prefix, route
    return None


async def _emit_log(record: dict[str, Any]) -> None:
    if not DETECTOR_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(DETECTOR_URL, json=record)
    except httpx.HTTPError as exc:
        log.error("failed to send log record: %s", exc)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy(path: str, request: Request) -> Response:
    full_path = "/" + path
    matched = _match(full_path)
    if matched is None:
        return Response(status_code=404, content=b'{"error":"no route"}',
                        media_type="application/json")
    _, route = matched

    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    async with httpx.AsyncClient(timeout=30) as client:
        upstream = await client.request(request.method, route["upstream"],
                                        headers=headers, content=body)

    response_text = upstream.text
    record = {
        "service": {"name": route["service"]},
        "consumer": {"username": request.headers.get("x-consumer", "anonymous")},
        "route": {"paths": [full_path]},
        "request": {"method": request.method, "path": full_path,
                    "body": body.decode(errors="replace")},
        "response": {"status": upstream.status_code, "body": response_text},
    }
    _schedule_log(record)

    passthrough = {k: v for k, v in upstream.headers.items()
                   if k.lower() in ("content-type", "mcp-session-id")}
    return Response(content=upstream.content, status_code=upstream.status_code,
                    headers=passthrough)


def _schedule_log(record: dict[str, Any]) -> None:
    import asyncio
    asyncio.get_running_loop().create_task(_emit_log(record))
