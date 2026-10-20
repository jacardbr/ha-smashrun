"""Write the whole run history to Home Assistant long-term statistics.

Two external statistics per account, with a cumulative `sum` per hour that had
a run:

  ha_smashrun:distance_<user id>   kilometres
  ha_smashrun:runs_<user id>       number of runs

They cover every run since the first one, so a statistics graph card with
`stat_types: [change]` and `period: week` (or month/year) shows the distance
per week across the whole history. They are rebuilt from scratch whenever the
run list changes, so edited or deleted runs are reflected too.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback

from .api import SmashrunRun
from .const import DOMAIN
from .runs_util import hourly_cumulative

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
