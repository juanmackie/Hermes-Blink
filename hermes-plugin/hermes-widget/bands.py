"""Widget size bands shared by publication guidance and offline previews."""
from __future__ import annotations

from typing import Any

BAND_XS_MAX_HEIGHT_DP = 130
BAND_S_MAX_HEIGHT_DP = 185
BAND_M_MAX_HEIGHT_DP = 300
SINGLE_COLUMN_MAX_WIDTH_DP = 245
BAND_BODY_LINES = {"xs": 0, "s": 0, "m": 3, "l": 8}
BAND_CHROME_LINES = {"xs": 3, "s": 4, "m": 6, "l": 6}
CHARS_PER_LINE_AT_245DP = 34


def size_band(width_dp: Any, height_dp: Any) -> str | None:
    """Return the band for usable widget dimensions, or None."""
    for value in (width_dp, height_dp):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
    if width_dp <= 0 or height_dp <= 0:
        return None
    if height_dp < BAND_XS_MAX_HEIGHT_DP:
        return "xs"
    if height_dp < BAND_S_MAX_HEIGHT_DP:
        return "s"
    if height_dp < BAND_M_MAX_HEIGHT_DP:
        return "m"
    return "l"


def is_single_column(width_dp: Any) -> bool:
    if isinstance(width_dp, bool) or not isinstance(width_dp, (int, float)):
        return True
    return width_dp < SINGLE_COLUMN_MAX_WIDTH_DP


def chars_per_line(width_dp: Any) -> int:
    """Approximate body characters per line: ~34 at 245dp."""
    if isinstance(width_dp, bool) or not isinstance(width_dp, (int, float)) or width_dp <= 0:
        return CHARS_PER_LINE_AT_245DP
    return max(8, int(CHARS_PER_LINE_AT_245DP * width_dp / 245))
