"""Small value helpers shared by store domains."""
from __future__ import annotations

from typing import Any

def _short_str(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed[:limit] or None
