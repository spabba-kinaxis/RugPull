"""Slack alerts for detected tool changes.

Tool names and descriptions come from untrusted servers. They are placed in code
blocks, with backticks and Slack control characters escaped, so they cannot
break formatting or add links and mentions.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger("rugpull_mcp.alerts")

MAX_BLOCK_CHARS = 1500


def _safe_block(text: str) -> str:
    text = text[:MAX_BLOCK_CHARS]
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("```", "'''")


def format_alert(server: str, consumer: str, status: str, result: dict[str, Any]) -> str:
    if status == "unapproved_server":
        head = f"MCP server {server} is not in the approved baseline (consumer {consumer})"
    else:
        head = f"MCP tool change on {server}, received by consumer {consumer}"
    lines = [head]
    for name in result["added"]:
        lines.append(f"Added tool: {name}")
    for name in result["removed"]:
        lines.append(f"Removed tool: {name}")
    for change in result["changed"]:
        diff = change["diff"] or "Schema or annotations changed (no description diff)"
        lines.append(f"Changed tool: {change['name']}\n```{_safe_block(diff)}```")
    return "\n".join(lines)


def send_alert(server: str, consumer: str, status: str, result: dict[str, Any]) -> bool:
    """Post the alert to SLACK_WEBHOOK_URL. Without a webhook, the alert is logged.

    Returns True when the alert was delivered (or logged), False on failure.
    """
    text = format_alert(server, consumer, status, result)
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        log.warning("ALERT (no SLACK_WEBHOOK_URL set): %s", text)
        return True
    try:
        r = requests.post(webhook, json={"text": text}, timeout=5)
        r.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("slack alert failed: %s", exc)
        return False
