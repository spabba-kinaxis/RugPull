"""Ingest service: receives Kong HTTP Log records and detects tool changes.

Kong sends each request's log record to POST /ingest. The record is not in the
request path, so an outage here never blocks agents. For every tools/list
response, this service compares the tools with the approved baseline and
alerts once per distinct change.

Configuration (environment variables):
    BASELINE_PATH          approved-tools.json (default /baseline/approved-tools.json)
    BASELINE_TTL_SECONDS   how often the baseline is reloaded (default 30)
    RESPONSE_BODY_PATH     dotted path to the response body in a log record
                           (default "response.body"; verify against your Kong version)
    QUARANTINE_ENABLED     "true" to quarantine a changed server (default false)
    SLACK_WEBHOOK_URL      Slack incoming webhook; alerts are logged if unset
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from fastapi import Body, FastAPI

from . import baseline, core
from .alerts import send_alert

log = logging.getLogger("rugpull_ingest")

BASELINE_PATH = os.environ.get("BASELINE_PATH", "/baseline/approved-tools.json")
BASELINE_TTL = float(os.environ.get("BASELINE_TTL_SECONDS", "30"))
BODY_PATH = os.environ.get("RESPONSE_BODY_PATH", "response.body").split(".")
QUARANTINE_ENABLED = os.environ.get("QUARANTINE_ENABLED", "false").lower() == "true"


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.baseline: dict[str, Any] = {}
        self.loaded_at = 0.0
        self.alerted: set[tuple] = set()
        self.stats = {"records": 0, "tool_listings": 0, "alerts": 0, "quarantined": 0,
                      "errors": 0, "last_listing_at": None}


state = _State()
app = FastAPI(title="rugpull-ingest")


def get_baseline() -> dict[str, Any]:
    with state.lock:
        if time.time() - state.loaded_at > BASELINE_TTL:
            state.baseline = baseline.load_baseline(BASELINE_PATH)
            state.loaded_at = time.time()
        return state.baseline


def dig(obj: Any, path: list[str]) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def process_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Check one Kong log record. Returns a summary when it holds a tools/list response."""
    with state.lock:
        state.stats["records"] += 1

    raw = dig(record, BODY_PATH)
    if raw is None:
        return None
    try:
        msg = core.parse_body(raw) if isinstance(raw, str) else raw
        tools = dig(msg, ["result", "tools"]) if isinstance(msg, dict) else None
        if not isinstance(tools, list):
            return None
        observed = core.extract_tools({"tools": tools})
    except (json.JSONDecodeError, core.ListingError) as exc:
        log.warning("skipping unreadable tools/list body: %s", exc)
        with state.lock:
            state.stats["errors"] += 1
        return None

    server = dig(record, ["service", "name"])
    if not isinstance(server, str) or not server:
        return None
    consumer = dig(record, ["consumer", "username"]) or "unknown"

    with state.lock:
        state.stats["tool_listings"] += 1
        state.stats["last_listing_at"] = time.time()

    entry = get_baseline().get(server)
    if entry is None:
        status = "unapproved_server"
        result = core.compare(observed, {})
    else:
        status = "changed"
        result = core.compare(observed, entry.get("tools", {}),
                              paginated=bool(entry.get("paginated", False)))
        if not core.has_changes(result):
            status = "clean"

    summary = {"server": server, "consumer": consumer, "status": status,
               "alerted": False, **result}
    if status == "clean":
        return summary

    key = (server, status, tuple(sorted((t["name"], core.tool_hash(t)) for t in observed)))
    with state.lock:
        if key in state.alerted:
            return summary
        state.alerted.add(key)
        state.stats["alerts"] += 1

    summary["alerted"] = send_alert(server, consumer, status, result)
    log.warning("detected server=%s consumer=%s status=%s added=%s removed=%s changed=%s",
                server, consumer, status, result["added"], result["removed"],
                [c["name"] for c in result["changed"]])

    if QUARANTINE_ENABLED and status == "changed":
        from .quarantine import quarantine
        try:
            quarantine(server)
            with state.lock:
                state.stats["quarantined"] += 1
        except Exception as exc:  # keep ingesting even if the Admin API is down
            log.error("quarantine failed for %s: %s", server, exc)
            with state.lock:
                state.stats["errors"] += 1
    return summary


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/stats")
def stats() -> dict[str, Any]:
    with state.lock:
        return dict(state.stats)


@app.post("/ingest")
def ingest(payload: Any = Body(...)) -> dict[str, Any]:
    """Accepts one log record or a list of them. Always answers 200, so Kong does not retry."""
    records = payload if isinstance(payload, list) else [payload]
    results = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            summary = process_record(record)
        except Exception:  # never fail the log delivery
            log.exception("failed to process log record")
            with state.lock:
                state.stats["errors"] += 1
            continue
        if summary is not None:
            results.append(summary)
    return {"ok": True, "checked": len(results), "results": results}
