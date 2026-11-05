"""Pure detection logic for the MCP rug-pull detector.

Nothing in this module does I/O. It parses a tools/list response strictly,
fingerprints each tool with SHA-256 over its complete definition, and compares
a listing against an approved baseline.

Design choice: the hash covers the whole tool object, not only the four fields
named in the design doc. A poisoned title, outputSchema, _meta or icon would
otherwise pass unnoticed. The cost is that a server which adds volatile
metadata to tools will produce alerts. Those alerts are the detector working as
intended, and they can be approved through the baseline.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import Any

MAX_TOOLS = 500            # a listing with more tools than this is rejected
MAX_DIFF_CHARS = 4000      # cap on each diff returned to a caller


class ListingError(ValueError):
    """The input is not a usable tools/list response. Callers must treat it as unverified."""


# --------------------------------------------------------------------------- #
# Strict parsing
# --------------------------------------------------------------------------- #

def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            # Parsers disagree about duplicate keys (first or last wins), so a
            # body with duplicates could show the detector one tool and the agent another.
            raise ListingError(f"duplicate JSON key {key!r}")
        obj[key] = value
    return obj


def loads_strict(text: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ListingError(f"invalid JSON: {exc.msg}") from exc


def parse_messages(raw: str) -> list[dict[str, Any]]:
    """Every JSON-RPC message in a body, whether it is a single object, a batch
    array, or server-sent events. Any malformed JSON raises ListingError, so it
    cannot be skipped silently."""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw[0] in "{[":
        obj = loads_strict(raw)
        items = obj if isinstance(obj, list) else [obj]
        return [m for m in items if isinstance(m, dict)]
    messages: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                msg = loads_strict(payload)
                if isinstance(msg, dict):
                    messages.append(msg)
    return messages


def parse_body(raw: str) -> dict[str, Any] | None:
    """First JSON-RPC message carrying a result or error, or None."""
    for msg in parse_messages(raw):
        if "result" in msg or "error" in msg:
            return msg
    return None


def listing_results(raw: str) -> list[list[Any]]:
    """The tools arrays from every result in a body. A batch or SSE stream can hold several."""
    found = []
    for msg in parse_messages(raw):
        result = msg.get("result")
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            found.append(result["tools"])
    return found


def extract_tools(listing: Any) -> list[dict[str, Any]]:
    """Return the tool objects from any of the accepted shapes.

    Accepted shapes:
      * a raw response body string (JSON, batch array or SSE), as Kong logs it
      * a JSON-RPC response: {"jsonrpc": "2.0", "result": {"tools": [...]}}
      * a result object: {"tools": [...]}
      * a bare list of tool objects
    """
    if isinstance(listing, str):
        listing = parse_body(listing)
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


# --------------------------------------------------------------------------- #
# Fingerprinting
# --------------------------------------------------------------------------- #

def canonical(tool: dict[str, Any]) -> str:
    """Stable JSON form of the complete tool object."""
    return json.dumps(tool, sort_keys=True, separators=(",", ":"))


def tool_hash(tool: dict[str, Any]) -> str:
    """SHA-256 of the canonical form. Any change to any field changes the hash."""
    return hashlib.sha256(canonical(tool).encode()).hexdigest()


def fingerprint(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map each tool name to its hash, description and complete approved definition.

    The definition is stored so a reviewer, and the alert, can see exactly what
    changed in the schema or other fields. It holds tool metadata only, never
    tool call arguments or results.
    """
    return {
        t["name"]: {
            "hash": tool_hash(t),
            "description": t.get("description") or "",
            "definition": t,
        }
        for t in tools
    }


def build_entry(tools: list[dict[str, Any]], paginated: bool = False) -> dict[str, Any]:
    """Baseline record for one server, ready to merge into approved-tools.json."""
    return {"paginated": paginated, "tools": fingerprint(tools)}


# --------------------------------------------------------------------------- #
# Comparison and diffs
# --------------------------------------------------------------------------- #

def _split_sentences(text: str) -> list[str]:
    """One sentence per line, so an added sentence stands out in a diff."""
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]
    return parts or [""]


def description_diff(approved: str, observed: str) -> str:
    """Unified diff between two descriptions, one sentence per line."""
    lines = difflib.unified_diff(
        _split_sentences(approved), _split_sentences(observed),
        "approved description", "observed description", lineterm="",
    )
    return "\n".join(lines)[:MAX_DIFF_CHARS]


def changed_fields(approved: dict[str, Any], observed: dict[str, Any]) -> list[str]:
    """Names of fields other than description whose values differ."""
    keys = (set(approved) | set(observed)) - {"description"}
    return sorted(
        k for k in keys
        if (k in approved) != (k in observed) or approved.get(k) != observed.get(k)
    )


def field_diff(approved: dict[str, Any], observed: dict[str, Any], fields: list[str]) -> str:
    """Pretty-printed unified diff of each changed field, e.g. an inputSchema."""
    parts = []
    for key in fields:
        old = json.dumps(approved.get(key), indent=2, sort_keys=True, ensure_ascii=False) \
            if key in approved else "<absent>"
        new = json.dumps(observed.get(key), indent=2, sort_keys=True, ensure_ascii=False) \
            if key in observed else "<absent>"
        parts.append("\n".join(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            f"approved {key}", f"observed {key}", lineterm="")))
    return "\n".join(p for p in parts if p)[:MAX_DIFF_CHARS]


def compare(
    observed: list[dict[str, Any]],
    approved: dict[str, dict[str, Any]],
    paginated: bool = False,
) -> dict[str, Any]:
    """Compare an observed listing with the approved tools for one server.

    ``approved`` maps tool name to the record written by ``fingerprint``.
    ``removed`` is always empty when ``paginated`` is true, because one page
    cannot prove that a tool is gone.

    Each changed entry has ``what`` ("description", "fields", or
    "description_and_fields"), a description diff, the changed field names and a
    diff of those fields.
    """
    seen = {t["name"]: t for t in observed}
    added = sorted(seen.keys() - approved.keys())
    removed = [] if paginated else sorted(approved.keys() - seen.keys())

    changed: list[dict[str, Any]] = []
    for name in sorted(seen.keys() & approved.keys()):
        record = approved[name]
        new = seen[name]
        if tool_hash(new) == record["hash"]:
            continue

        old_desc = record.get("description") or ""
        new_desc = new.get("description") or ""
        desc_changed = old_desc != new_desc

        definition = record.get("definition")
        note = ""
        if definition is None:
            fields, fdiff = [], ""
            note = "approved definition not recorded; field changes cannot be shown. Re-record the baseline."
        else:
            fields = changed_fields(definition, new)
            fdiff = field_diff(definition, new, fields) if fields else ""

        if desc_changed and fields:
            what = "description_and_fields"
        elif desc_changed:
            what = "description"
        else:
            what = "fields"

        changed.append({
            "name": name,
            "what": what,
            "diff": description_diff(old_desc, new_desc) if desc_changed else "",
            "changed_fields": fields,
            "field_diff": fdiff,
            "note": note,
        })

    return {"added": added, "removed": removed, "changed": changed}


def has_changes(result: dict[str, Any]) -> bool:
    return bool(result["added"] or result["removed"] or result["changed"])
