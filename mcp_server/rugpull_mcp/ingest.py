"""Ingest service: receives Kong HTTP Log records and detects tool changes.

Kong sends each request's log record to POST /ingest. The record is not in the
request path, so an outage here never blocks agents. For every tools/list
response, this service compares the tools with the approved baseline and
alerts once per distinct change for each consumer.

Anything it cannot verify raises an "unverifiable" alert rather than being
skipped: malformed JSON, duplicate keys, or a tools/list body that cannot be read.

Configuration (environment variables):
    BASELINE_PATH                  approved-tools.json (default /baseline/approved-tools.json)
    BASELINE_TTL_SECONDS           how often the baseline is reloaded (default 30)
    RESPONSE_BODY_PATH             dotted path to the response body in a log record
                                   (default "response.body"; verify against your Kong version)
    INGEST_TOKEN                   shared secret Kong must present as
                                   "Authorization: Bearer <token>" or "X-Ingest-Token"
    INGEST_ALLOW_UNAUTHENTICATED   "true" to accept records without a token (local use only;
                                   without a token and without this flag, every request is refused)
    ALERT_CACHE_SIZE               how many distinct alerts to remember (default 10000)
    QUARANTINE_ENABLED             "true" to quarantine a changed server (default false)
    SLACK_WEBHOOK_URL              Slack incoming webhook; alerts are logged if unset
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request

from . import baseline, core
from .alerts import send_alert

log = logging.getLogger("rugpull_ingest")

BASELINE_PATH = os.environ.get("BASELINE_PATH", "/baseline/approved-tools.json")
BASELINE_TTL = float(os.environ.get("BASELINE_TTL_SECONDS", "30"))
BODY_PATH = os.environ.get("RESPONSE_BODY_PATH", "response.body").split(".")
QUARANTINE_ENABLED = os.environ.get("QUARANTINE_ENABLED", "false").lower() == "true"
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")
ALLOW_UNAUTHENTICATED = os.environ.get("INGEST_ALLOW_UNAUTHENTICATED", "false").lower() == "true"
ALERT_CACHE_SIZE = int(os.environ.get("ALERT_CACHE_SIZE", "10000"))


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.baseline: dict[str, Any] = {}
        self.loaded_at = 0.0
        self.alerted: OrderedDict[tuple, None] = OrderedDict()
        self.stats = {"records": 0, "tool_listings": 0, "alerts": 0, "unverifiable": 0,
                      "quarantined": 0, "errors": 0, "unauthorized": 0,
                      "last_listing_at": None}

    def remember(self, key: tuple) -> bool:
        """Record an alert key. Returns True if it is new. Caller holds the lock.

        The store is bounded: the oldest keys are evicted first, so memory
        cannot grow without limit under a stream of distinct changes.
        """
        if key in self.alerted:
            self.alerted.move_to_end(key)
            return False
        self.alerted[key] = None
        while len(self.alerted) > ALERT_CACHE_SIZE:
            self.alerted.popitem(last=False)
        return True


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


def is_authorized(request: Request) -> bool:
    """Constant-time comparison. Without a configured token, every request is refused
    unless unauthenticated mode is explicitly enabled."""
    if not INGEST_TOKEN:
        return ALLOW_UNAUTHENTICATED
    presented = request.headers.get("x-ingest-token", "")
    auth = request.headers.get("authorization", "")
    if not presented and auth.lower().startswith("bearer "):
        presented = auth[7:]
    return hmac.compare_digest(presented.encode(), INGEST_TOKEN.encode())


def _requested_method(record: dict[str, Any]) -> str | None:
    body = dig(record, ["request", "body"])
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return None
    return body.get("method") if isinstance(body, dict) else None


def _is_listing_candidate(record: dict[str, Any], text: str) -> bool:
    return _requested_method(record) == "tools/list" or '"tools"' in text


def _alert_unverifiable(server: str, consumer: str, reason: str, text: str) -> dict[str, Any]:
    digest = hashlib.sha256(text.encode()).hexdigest()
    key = (server, consumer, "unverifiable", digest)
    result = {"added": [], "removed": [], "changed": [], "reason": reason}
    with state.lock:
        state.stats["unverifiable"] += 1
        new = state.remember(key)
        if new:
            state.stats["alerts"] += 1
    sent = send_alert(server, consumer, "unverifiable", result) if new else False
    log.error("unverifiable tools/list server=%s consumer=%s reason=%s", server, consumer, reason)
    return {"server": server, "consumer": consumer, "status": "unverifiable",
            "reason": reason, "alerted": sent, "added": [], "removed": [], "changed": []}


def _check_listing(server: str, consumer: str, tools_raw: list[Any]) -> dict[str, Any]:
    try:
        observed = core.extract_tools({"tools": tools_raw})
    except core.ListingError as exc:
        return _alert_unverifiable(server, consumer, str(exc), json.dumps(tools_raw))

    with state.lock:
        state.stats["tool_listings"] += 1
        state.stats["last_listing_at"] = time.time()

    entry = get_baseline().get(server)
    if entry is None:
        status = "unapproved_server"
        result = core.compare(observed, {})
    else:
        result = core.compare(observed, entry.get("tools", {}),
                              paginated=bool(entry.get("paginated", False)))
        status = "changed" if core.has_changes(result) else "clean"

    summary = {"server": server, "consumer": consumer, "status": status,
               "alerted": False, **result}
    if status == "clean":
        return summary

    # The key includes the consumer, so every agent that received the change is reported.
    key = (server, consumer, status,
           tuple(sorted((t["name"], core.tool_hash(t)) for t in observed)))
    with state.lock:
        new = state.remember(key)
        if new:
            state.stats["alerts"] += 1
    if not new:
        return summary

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
        except Exception as exc:  # keep ingesting if the Admin API is down
            log.error("quarantine failed for %s: %s", server, exc)
            with state.lock:
                state.stats["errors"] += 1
    return summary


def process_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Check one Kong log record. Returns one summary per tools/list result it held."""
    with state.lock:
        state.stats["records"] += 1

    raw = dig(record, BODY_PATH)
    if raw is None:
        return []
    text = raw if isinstance(raw, str) else json.dumps(raw)

    server = dig(record, ["service", "name"])
    server = server if isinstance(server, str) and server else "unknown"
    consumer = dig(record, ["consumer", "username"]) or "unknown"
    consumer = str(consumer)

    try:
        listings = core.listing_results(text)
    except core.ListingError as exc:
        if _is_listing_candidate(record, text):
            return [_alert_unverifiable(server, consumer, str(exc), text)]
        return []

    if not listings:
        if _requested_method(record) == "tools/list":
            messages = core.parse_messages(text)
            if not any("error" in m for m in messages):
                return [_alert_unverifiable(server, consumer,
                                            "tools/list response has no tools array", text)]
        return []

    return [_check_listing(server, consumer, tools) for tools in listings]


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/stats")
def stats() -> dict[str, Any]:
    with state.lock:
        return dict(state.stats)


@app.post("/ingest")
def ingest(request: Request, payload: Any = Body(...)) -> dict[str, Any]:
    """Accepts one log record or a list of them. Answers 200 for every authorized
    record, even one that fails, so Kong does not retry. Unauthorized requests get 401."""
    if not is_authorized(request):
        with state.lock:
            state.stats["unauthorized"] += 1
        raise HTTPException(status_code=401, detail="unauthorized")

    records = payload if isinstance(payload, list) else [payload]
    results: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            results.extend(process_record(record))
        except Exception:  # never fail log delivery
            log.exception("failed to process log record")
            with state.lock:
                state.stats["errors"] += 1
    return {"ok": True, "checked": len(results), "results": results}
