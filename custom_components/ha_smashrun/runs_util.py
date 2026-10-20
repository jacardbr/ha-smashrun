"""Pure helpers over the run list (no Home Assistant imports)."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone, tzinfo
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


_HOUR = timedelta(hours=1)
# Any date in the next period, from a period start.
_NEXT_PERIOD = {"week": timedelta(days=7), "month": timedelta(days=32), "year": timedelta(days=366)}


def _utc_hour(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def sensor_statistics(
    runs: Iterable[Any],
    *,
    count: bool,
    period: Optional[str],
    cutoff: datetime,
    anchor_sum: Optional[float],
    tz: tzinfo,
) -> list[tuple[datetime, float, float, Optional[datetime]]]:
    """Hourly statistics rows for one history sensor, for the hours before `cutoff`.

    Returns (hour start UTC, state, sum, last_reset) rows, oldest first, for:
      - the "total" sensors (`period=None`): state = all-time total;
      - the "this week/month/year" sensors: state = total within the period,
        last_reset = period start, plus a row at every period start so the
        state drops to zero when a new period begins.

    `count` selects run counts instead of kilometres. A row describes the end
    of its hour, as the recorder does: a run counts in the hour it started.

    `cutoff` is the first hour already compiled by the recorder (or the current
    hour). When the recorder has a row there, `anchor_sum` is its `sum`; the
    imported sums are shifted so they end exactly where the recorder's begin,
    which keeps the "change" of every period correct across the boundary.
    """
    dated = sorted(
        (r.start, 1.0 if count else (r.distance_km or 0.0))
        for r in runs
        if r.start is not None
    )
    if not dated:
        return []
    starts = [start for start, _ in dated]
    prefix = [0.0]
    for _, amount in dated:
        prefix.append(prefix[-1] + amount)

    def total_before(moment: datetime) -> float:
        return prefix[bisect_left(starts, moment)]

    hours = {_utc_hour(start) for start in starts}
    if period is not None:
        boundary = period_start(starts[0].astimezone(tz), period)
        while _utc_hour(boundary) < cutoff:
            hours.add(_utc_hour(boundary))
            boundary = period_start(boundary + _NEXT_PERIOD[period], period)

    offset = 0.0
    if anchor_sum is not None:
        offset = anchor_sum - total_before(cutoff + _HOUR)

    rows: list[tuple[datetime, float, float, Optional[datetime]]] = []
    for hour in sorted(h for h in hours if h < cutoff):
        total = total_before(hour + _HOUR)
        if period is None:
            state, last_reset = total, None
        else:
            last_reset = period_start(hour.astimezone(tz), period)
            state = total - total_before(last_reset)
        rows.append((hour, round(state, 3), round(total + offset, 3), last_reset))
    return rows


# Personal record distances: key -> km.
RECORD_DISTANCES = {"5k": 5.0, "10k": 10.0, "21k": 21.0975, "42k": 42.195}
# GPS tracks often come out a little short: a 4.96 km run still counts as a 5K.
RECORD_TOLERANCE = 0.99


# Run count buckets: key -> (from km, to km), from included, to excluded.
# Runs outside every bucket (48 km or more, or without a distance) are not
# counted.
COUNT_BUCKETS = {
    "lt5k": (0.0, 4.5),
    "5k": (4.5, 9.5),
    "10k": (9.5, 20.5),
    "21k": (20.5, 41.5),
    "42k": (41.5, 48.0),
}


def count_buckets(limits: Sequence[float]) -> dict[str, tuple[float, float]]:
    """Buckets from 5 ascending limits in km: where the 5K, 10K, 21K and 42K
    start, then the end of the 42K. The "lt5k" bucket starts at 0.

    `[4.5, 9.5, 20.5, 41.5, 48]` gives `COUNT_BUCKETS`.
    """
    if len(limits) != 5 or any(not a < b for a, b in zip([0.0, *limits], limits)):
        raise ValueError("count limits must be 5 ascending distances above 0")
    edges = [0.0, *(float(km) for km in limits)]
    return {key: (edges[i], edges[i + 1]) for i, key in enumerate(COUNT_BUCKETS)}


def distance_bucket(
    distance_km: Optional[float], buckets: Optional[dict[str, tuple[float, float]]] = None
) -> Optional[str]:
    """Count bucket of a run: 3 km -> "lt5k", 7 km -> "5k", 50 km -> None."""
    if not distance_km:
        return None
    for key, (low, high) in (buckets or COUNT_BUCKETS).items():
        if low <= distance_km < high:
            return key
    return None


def yearly_summary(
    runs: Iterable[Any], tz: tzinfo, buckets: Optional[dict[str, tuple[float, float]]] = None
) -> dict[str, dict[str, Any]]:
    """Per local year: kilometres per month (12 values), the run count and
    the run count per distance bucket (see `distance_bucket`)."""
    years: dict[str, dict[str, Any]] = {}
    for run in runs:
        if run.start is None:
            continue
        local = run.start.astimezone(tz)
        year = years.setdefault(
            str(local.year),
            {"months_km": [0.0] * 12, "runs": 0, "counts": {k: 0 for k in COUNT_BUCKETS}},
        )
        year["months_km"][local.month - 1] += run.distance_km or 0.0
        year["runs"] += 1
        bucket = distance_bucket(run.distance_km, buckets)
        if bucket is not None:
            year["counts"][bucket] += 1
    for year in years.values():
        year["months_km"] = [round(km, 3) for km in year["months_km"]]
    return dict(sorted(years.items()))


def weekly_summary(runs: Iterable[Any], tz: tzinfo) -> dict[str, list[float]]:
    """Kilometres per ISO week (Monday to Sunday), 53 values per ISO year.

    A run on Monday 2025-12-29 is in week 1 of 2026; years with 52 weeks
    keep 0 in week 53.
    """
    weeks: dict[str, list[float]] = {}
    for run in runs:
        if run.start is None:
            continue
        iso_year, week, _ = run.start.astimezone(tz).isocalendar()
        weeks.setdefault(str(iso_year), [0.0] * 53)[week - 1] += run.distance_km or 0.0
    return {year: [round(km, 3) for km in kms] for year, kms in sorted(weeks.items())}


def personal_records(
    runs: Iterable[Any], tz: tzinfo
) -> dict[str, dict[str, Optional[dict[str, Any]]]]:
    """Fastest time per record distance, for all time ("all") and per local year.

    Only whole-run totals are available, so the time for a distance is
    estimated from the average pace of a run at least that long: a 12 km run
    at 5:00/km counts as a 50:00 10K. For races of the exact distance this is
    the real time.
    """
    best: dict[str, dict[str, Optional[dict[str, Any]]]] = {}

    def consider(scope: str, key: str, record: dict[str, Any]) -> None:
        current = best.setdefault(scope, {k: None for k in RECORD_DISTANCES}).get(key)
        if current is None or record["time_s"] < current["time_s"]:
            best[scope][key] = record

    for run in runs:
        if run.start is None or not run.distance_km or not run.duration_s:
            continue
        year = str(run.start.astimezone(tz).year)
        best.setdefault(year, {k: None for k in RECORD_DISTANCES})
        pace = run.duration_s / run.distance_km  # seconds per km
        for key, km in RECORD_DISTANCES.items():
            if run.distance_km < km * RECORD_TOLERANCE:
                continue
            record = {
                "run_id": run.run_id,
                "start": run.start.isoformat(),
                "time_s": round(pace * km, 1),
                "pace_s_per_km": round(pace, 1),
                "run_distance_km": round(run.distance_km, 3),
            }
            consider("all", key, record)
            consider(year, key, record)
    best.setdefault("all", {k: None for k in RECORD_DISTANCES})
    return best


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
