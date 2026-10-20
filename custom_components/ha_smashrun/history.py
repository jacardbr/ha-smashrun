"""Write the whole run history to Home Assistant long-term statistics.

1. The history sensors (total distance/runs, distance/runs this week, month,
   year) get their past statistics imported on request (setup option, the
   options menu or the `ha_smashrun.import_history` action), so their graphs
   start with the first run instead of the day the integration was added.

2. Two external statistics per account, rebuilt automatically, with a
   cumulative `sum` per hour that had a run:

  ha_smashrun:distance_<user id>   kilometres
  ha_smashrun:runs_<user id>       number of runs

They cover every run since the first one, so a statistics graph card with
`stat_types: [change]` and `period: week` (or month/year) shows the distance
per week across the whole history. They are rebuilt from scratch whenever the
run list changes, so edited or deleted runs are reflected too.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import DistanceConverter

from .api import SmashrunRun
from .const import CONF_STATS_CUTOFFS, DOMAIN
from .runs_util import PERIODS, hourly_cumulative, sensor_statistics

if TYPE_CHECKING:
    from .coordinator import SmashrunConfigEntry

_LOGGER = logging.getLogger(__name__)


def statistic_ids(uid: str) -> tuple[str, str]:
    """(distance, run count) statistic ids for an account."""
    return f"{DOMAIN}:distance_{uid}", f"{DOMAIN}:runs_{uid}"


@callback
def async_write_statistics(
    hass: HomeAssistant, uid: str, user: dict[str, Any], runs: list[SmashrunRun]
) -> None:
    """Replace both statistics with the given run list (queued on the recorder)."""
    distance_id, runs_id = statistic_ids(uid)
    who = user.get("username") or uid

    # Clearing first drops hours that no longer have a run (deleted runs).
    # The recorder runs its queue in order, so this happens before the import.
    get_instance(hass).async_clear_statistics([distance_id, runs_id])

    rows = hourly_cumulative(runs)
    if not rows:
        return

    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"Smashrun {who} distance",
            source=DOMAIN,
            statistic_id=distance_id,
            unit_class="distance",
            unit_of_measurement=UnitOfLength.KILOMETERS,
        ),
        [StatisticData(start=hour, state=km, sum=km) for hour, km, _ in rows],
    )
    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"Smashrun {who} runs",
            source=DOMAIN,
            statistic_id=runs_id,
            unit_class=None,
            unit_of_measurement=None,
        ),
        [StatisticData(start=hour, state=n, sum=n) for hour, _, n in rows],
    )
    _LOGGER.debug("Wrote Smashrun statistics: %d runs in %d hours", len(runs), len(rows))


# Sensor key -> (counts runs instead of km, period or None for all-time).
HISTORY_SENSORS: dict[str, tuple[bool, str | None]] = {
    "total_distance": (False, None),
    "total_runs": (True, None),
    **{f"distance_this_{p}": (False, p) for p in PERIODS},
    **{f"runs_this_{p}": (True, p) for p in PERIODS},
}

_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


async def async_import_history(
    hass: HomeAssistant, entry: SmashrunConfigEntry
) -> dict[str, Any]:
    """Import the past statistics of every enabled history sensor.

    For each sensor, the hours before the first statistics row compiled by the
    recorder are written, with sums shifted to join that row seamlessly.
    That first hour is remembered in the entry, so running it again rewrites
    the same imported rows and never touches the recorder's own.
    """
    coordinator = entry.runtime_data
    tz = dt_util.get_default_time_zone()
    runs = await coordinator.api.async_get_all_runs(tz)

    registry = er.async_get(hass)
    cutoffs: dict[str, str] = dict(entry.data.get(CONF_STATS_CUTOFFS) or {})
    imported: dict[str, int] = {}

    for key, (count, period) in HISTORY_SENSORS.items():
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{coordinator.uid}_{key}"
        )
        if entity_id is None:
            continue
        reg_entry = registry.async_get(entity_id)
        if reg_entry is None or reg_entry.disabled_by is not None:
            continue

        stored = cutoffs.get(entity_id)
        cutoff, anchor_sum = await _async_recorder_start(
            hass, entity_id, dt_util.parse_datetime(stored) if stored else None
        )
        rows = sensor_statistics(
            runs, count=count, period=period, cutoff=cutoff, anchor_sum=anchor_sum, tz=tz
        )
        cutoffs[entity_id] = cutoff.isoformat()
        if rows:
            imported[entity_id] = _write_sensor_statistics(hass, entity_id, rows, count)

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_STATS_CUTOFFS: cutoffs}
    )
    _LOGGER.info(
        "Imported Smashrun history: %d runs, %d hourly rows across %d sensors",
        len(runs),
        sum(imported.values()),
        len(imported),
    )
    return {"runs_found": len(runs), "statistics_imported": imported}


async def _async_recorder_start(
    hass: HomeAssistant, entity_id: str, since: datetime | None
) -> tuple[datetime, float | None]:
    """(first hour compiled by the recorder, its sum), searching from `since`.

    Without such a row, the current hour (and no anchor): the recorder will
    then continue from the last imported row.
    """
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        since or _EPOCH,
        None,
        {entity_id},
        "hour",
        None,
        {"sum"},
    )
    rows = stats.get(entity_id) or []
    if rows:
        first = rows[0]
        start = first["start"]
        if not isinstance(start, datetime):
            start = datetime.fromtimestamp(start, tz=timezone.utc)
        return start, first.get("sum")
    return dt_util.utcnow().replace(minute=0, second=0, microsecond=0), None


def _write_sensor_statistics(
    hass: HomeAssistant,
    entity_id: str,
    rows: list[tuple[datetime, float, float, datetime | None]],
    count: bool,
) -> int:
    """Write the rows in the unit the entity currently reports; returns the count."""
    state = hass.states.get(entity_id)
    unit = state.attributes.get("unit_of_measurement") if state is not None else None

    unit_class: str | None = None
    if not count:
        unit_class = "distance"
        unit = unit or UnitOfLength.KILOMETERS
        if unit != UnitOfLength.KILOMETERS:
            rows = [
                (
                    start,
                    DistanceConverter.convert(value, UnitOfLength.KILOMETERS, unit),
                    DistanceConverter.convert(total, UnitOfLength.KILOMETERS, unit),
                    last_reset,
                )
                for start, value, total, last_reset in rows
            ]
    else:
        unit = unit or "runs"

    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=None,
        source="recorder",
        statistic_id=entity_id,
        unit_class=unit_class,
        unit_of_measurement=unit,
    )
    statistics = [
        StatisticData(start=start, state=value, sum=total, last_reset=last_reset)
        for start, value, total, last_reset in rows
    ]
    async_import_statistics(hass, metadata, statistics)
    return len(statistics)
