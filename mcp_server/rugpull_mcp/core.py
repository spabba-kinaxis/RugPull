"""Pure detection logic for the MCP rug-pull detector.

Nothing in this module does I/O. It parses a tools/list response, fingerprints
each tool with SHA-256 over a canonical form, and compares a listing against an
approved baseline. The canonical form matches the design doc exactly, so hashes
recorded by one component verify in the others.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import Any

# Fields that define what an agent is shown about a tool. Anything else in the
# tool object (for example _meta) is ignored, as in the design doc.
FIELDS = ("name", "description", "inputSchema", "annotations")

MAX_TOOLS = 500            # a listing with more tools than this is rejected
MAX_DIFF_CHARS = 4000      # cap on each diff returned to a caller


class ListingError(ValueError):
    """The input is not a usable tools/list response."""


# --------------------------------------------------------------------------- #
# Fingerprinting
# --------------------------------------------------------------------------- #

def canonical(tool: dict[str, Any]) -> str:
    """Stable JSON form of the fields that define a tool."""
    picked = {k: tool.get(k) for k in FIELDS}
    return json.dumps(picked, sort_keys=True, separators=(",", ":"))


def tool_hash(tool: dict[str, Any]) -> str:
    """SHA-256 of the canonical form. Any one-character change changes it."""
    return hashlib.sha256(canonical(tool).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def parse_body(raw: str) -> dict[str, Any] | None:
    """Parse a JSON-RPC response body sent as plain JSON or as server-sent events.

    Returns the first JSON-RPC message that carries a ``result`` or ``error``,
    or ``None`` if the body holds none.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        msg = json.loads(raw)
        return msg if isinstance(msg, dict) else None
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                msg = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                return msg
    return None


def extract_tools(listing: Any) -> list[dict[str, Any]]:
    """Return the tool objects from any of the shapes a caller may pass.

    Accepted shapes:
      * a raw response body string (JSON or SSE), as Kong logs it
      * a JSON-RPC response: {"jsonrpc": "2.0", "result": {"tools": [...]}}
      * a result object: {"tools": [...]}
      * a bare list of tool objects
    """
    if isinstance(listing, str):
        try:
            listing = parse_body(listing)
        except json.JSONDecodeError as exc:
            raise ListingError(f"listing is not valid JSON: {exc}") from exc
        if listing is None:
            raise ListingError("no JSON-RPC result found in listing body")

    if isinstance(listing, dict) and "result" in listing:
        listing = listing["result"]
    if isinstance(listing, dict):
        if "error" in listing and "tools" not in listing:
            raise ListingError(f"listing is a JSON-RPC error: {listing['error']}")
        listing = listing.get("tools")
    if not isinstance(listing, list):
        raise ListingError("listing has no 'tools' array")
    if len(listing) > MAX_TOOLS:
        raise ListingError(f"listing has {len(listing)} tools; limit is {MAX_TOOLS}")

    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in listing:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ListingError("every tool must be an object with a string 'name'")
        if item["name"] in seen:
            raise ListingError(f"duplicate tool name in listing: {item['name']!r}")
        seen.add(item["name"])
        tools.append(item)
    return tools


def fingerprint(tools: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Map each tool name to its hash and description (the description is kept
    so reviewers can read the approved text in a diff)."""
    return {
        t["name"]: {"hash": tool_hash(t), "description": t.get("description") or ""}
        for t in tools
    }


def build_entry(tools: list[dict[str, Any]], paginated: bool = False) -> dict[str, Any]:
    """Baseline record for one server, ready to merge into approved-tools.json."""
    return {"paginated": paginated, "tools": fingerprint(tools)}


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #

def _split_sentences(text: str) -> list[str]:
    """One sentence per line, so an added sentence stands out in a diff."""
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]
    return parts or [""]


def description_diff(approved: str, observed: str) -> str:
    """Unified diff between two descriptions, one sentence per line."""
    lines = difflib.unified_diff(
        _split_sentences(approved),
        _split_sentences(observed),
        "approved",
        "observed",
        lineterm="",
    )
    text = "\n".join(lines)
    return text[:MAX_DIFF_CHARS]


def compare(
    observed: list[dict[str, Any]],
    approved: dict[str, dict[str, Any]],
    paginated: bool = False,
) -> dict[str, Any]:
    """Compare an observed listing with the approved tools for one server.

    ``approved`` maps tool name to ``{"hash": ..., "description": ...}``.
    ``removed`` is always empty when ``paginated`` is true, because one page
    cannot prove that a tool is gone.
    """
    seen = {t["name"]: t for t in observed}
    added = sorted(seen.keys() - approved.keys())
    removed = [] if paginated else sorted(approved.keys() - seen.keys())

    changed: list[dict[str, Any]] = []
    for name in sorted(seen.keys() & approved.keys()):
        if tool_hash(seen[name]) == approved[name]["hash"]:
            continue
        old_desc = approved[name].get("description") or ""
        new_desc = seen[name].get("description") or ""
        description_changed = old_desc != new_desc
        changed.append({
            "name": name,
            "what": "description" if description_changed else "schema_or_annotations",
            "diff": description_diff(old_desc, new_desc) if description_changed else "",
        })

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def has_changes(result: dict[str, Any]) -> bool:
    return bool(result["added"] or result["removed"] or result["changed"])
