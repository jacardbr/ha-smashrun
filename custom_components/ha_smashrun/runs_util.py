"""Pure helpers over the run list (no Home Assistant imports)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

KM_PER_MILE = 1.609344

PERIODS = ("week", "month", "year")


def period_start(now: datetime, period: str) -> datetime:
    """Start (local midnight) of the week (Monday), month or year containing `now`."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    if period == "year":
        return day.replace(month=1, day=1)
    raise ValueError(period)


def totals(runs: Iterable[Any], since: Optional[datetime] = None) -> tuple[float, int]:
    """(kilometres, run count) of the runs that started at or after `since`."""
    km = 0.0
    count = 0
    for run in runs:
        if since is not None and (run.start is None or run.start < since):
            continue
        km += run.distance_km or 0.0
        count += 1
    return km, count


def hourly_cumulative(runs: Iterable[Any]) -> list[tuple[datetime, float, int]]:
    """Running totals per UTC hour, oldest first: (hour start, km so far, runs so far).

    Only hours with at least one run get a row, which is what long-term
    statistics with a `sum` expect.
    """
    buckets: dict[datetime, list[float]] = {}
    for run in runs:
        if run.start is None:
            continue
        hour = run.start.astimezone(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        bucket = buckets.setdefault(hour, [0.0, 0])
        bucket[0] += run.distance_km or 0.0
        bucket[1] += 1

    rows: list[tuple[datetime, float, int]] = []
    km = 0.0
    count = 0
    for hour in sorted(buckets):
        km += buckets[hour][0]
        count += int(buckets[hour][1])
        rows.append((hour, round(km, 3), count))
    return rows


def runs_signature(runs: Iterable[Any]) -> tuple[tuple[str, Optional[float]], ...]:
    """Identity of a run list, to tell whether the statistics need rebuilding."""
    return tuple((r.run_id, r.distance_km) for r in runs)


def format_duration(seconds: Optional[float]) -> Optional[str]:
    """`3725` -> "1:02:05", `325` -> "5:25"."""
    if seconds is None:
        return None
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_pace(minutes: Optional[float]) -> Optional[str]:
    """Decimal minutes -> "m:ss" (5.5 -> "5:30")."""
    if minutes is None:
        return None
    return format_duration(minutes * 60)
