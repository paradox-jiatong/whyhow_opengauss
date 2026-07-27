"""Router utilities shared by middleware and API handlers."""

from __future__ import annotations

import re

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def clean_url(path: str) -> str:
    """Normalize concrete request paths into stable route-like patterns."""
    if not path or path == "/":
        return "/"

    parts = []
    for part in path.strip("/").split("/"):
        if _UUID_RE.match(part):
            parts.append("{uuid}")
        elif part.isdigit():
            parts.append("{int}")
        else:
            parts.append(part)

    return "/" + "/".join(parts)
