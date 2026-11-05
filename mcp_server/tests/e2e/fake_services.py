"""Test double for Slack and the Konnect Admin API. Records every call."""

import threading
from typing import Any

from fastapi import FastAPI, Request

app = FastAPI()
_lock = threading.Lock()
_slack: list[dict[str, Any]] = []
_konnect: list[dict[str, Any]] = []


@app.post("/slack/webhook")
async def slack(request: Request) -> dict[str, bool]:
    body = await request.json()
    with _lock:
        _slack.append({"text": body.get("text", "")})
    return {"ok": True}


@app.get("/slack/messages")
def slack_messages() -> list[dict[str, Any]]:
    with _lock:
        return list(_slack)


@app.api_route("/konnect/{path:path}", methods=["GET", "POST", "DELETE"])
async def konnect(path: str, request: Request) -> dict[str, Any]:
    raw = await request.body()
    with _lock:
        _konnect.append({"method": request.method, "path": "/" + path,
                         "body": raw.decode() if raw else ""})
    if request.method == "POST":
        return {"id": "plugin-1"}
    return {"data": []}


@app.get("/konnect-calls")
def konnect_calls() -> list[dict[str, Any]]:
    with _lock:
        return list(_konnect)


@app.post("/reset")
def reset() -> dict[str, bool]:
    with _lock:
        _slack.clear()
        _konnect.clear()
    return {"ok": True}


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}
