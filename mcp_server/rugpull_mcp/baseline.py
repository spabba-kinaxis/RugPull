"""Load the approved baseline (approved-tools.json)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("rugpull_mcp.baseline")


def load_baseline(path: Path | str) -> dict[str, Any]:
    """Read the baseline file. Raises ValueError if it is missing or malformed."""
    path = Path(path)
    try:
        with path.open() as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        log.error("baseline file missing at %s", path)
        raise ValueError("approved baseline is not available") from exc
    except (OSError, json.JSONDecodeError) as exc:
        log.error("baseline file unreadable at %s: %s", path, exc)
        raise ValueError("approved baseline is not available") from exc
    if not isinstance(data, dict):
        raise ValueError("approved baseline has an unexpected shape")
    return data
