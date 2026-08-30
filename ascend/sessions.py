"""ICT kill-zone / session weighting for ASCEND-30 (deterministic, display-only).

Maps a millisecond timestamp to a named session window in UTC and returns a
session weight (configurable). Higher weight windows are preferred for
higher-frequency entries; all windows are displayed as context, never a hard
rejection unless you set enabled + a weight floor.
"""
from __future__ import annotations

from datetime import datetime, timezone


def session_at(ms: int, kill_zones: list[dict]) -> tuple[str, float]:
    """Return (session_name, weight). 'OFF' / 1.0 when no window matches."""
    hour = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).hour
    for kz in kill_zones:
        start = int(kz.get("startUtcHour", 0))
        end = int(kz.get("endUtcHour", 0))
        in_window = (start <= hour < end) if start <= end else (hour >= start or hour < end)
        if in_window:
            return str(kz.get("name", "?")).upper(), float(kz.get("weight", 1.0))
    return "OFF", 1.0
