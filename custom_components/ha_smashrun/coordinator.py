"""Data update coordinator for Smashrun."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SmashrunApi,
    SmashrunAuthError,
    SmashrunBadge,
    SmashrunError,
    SmashrunRun,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FULL_REFRESH_HOURS,
)
from .runs_util import runs_signature

_LOGGER = logging.getLogger(__name__)

type SmashrunConfigEntry = ConfigEntry[SmashrunCoordinator]


@dataclass
class SmashrunData:
    """Everything the entities read."""

    last_run: SmashrunRun | None
    runs: list[SmashrunRun] = field(default_factory=list)  # oldest first
    badges: list[SmashrunBadge] = field(default_factory=list)  # newest first
    badges_available: bool = True


class SmashrunCoordinator(DataUpdateCoordinator[SmashrunData]):
    """Polls the latest run; re-reads the whole history only when it changes."""

    config_entry: SmashrunConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SmashrunConfigEntry,
        api: SmashrunApi,
        user: dict[str, Any],
    ) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.api = api
        self.user = user
        self.uid = str(user["id"])
        # When the personal access token stops working (about 60 days).
        self.token_expires: datetime | None = None
        self._full_at = 0.0
        self._stats_signature: tuple | None = None

    @property
    def imperial(self) -> bool:
        """The user's Smashrun distance unit is miles."""
        return str(self.user.get("unitDistance") or "").lower() == "m"

    async def _async_update_data(self) -> SmashrunData:
        tz = dt_util.get_default_time_zone()
        previous = self.data
        try:
            last_run = await self.api.async_get_latest_run(tz)

            stale = time.monotonic() - self._full_at > FULL_REFRESH_HOURS * 3600
            changed = previous is None or _run_key(last_run) != _run_key(previous.last_run)
            if not (stale or changed):
                return SmashrunData(
                    last_run=last_run,
                    runs=previous.runs,
                    badges=previous.badges,
                    badges_available=previous.badges_available,
                )

            runs = await self.api.async_get_all_runs(tz)
            badges, badges_available = await self._async_get_badges(tz, previous)
            await self._async_update_token_expiry()
        except SmashrunAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SmashrunError as err:
            raise UpdateFailed(f"Error fetching Smashrun data: {err}") from err

        self._full_at = time.monotonic()
        self._async_update_statistics(runs)
        return SmashrunData(
            last_run=last_run,
            runs=runs,
            badges=badges,
            badges_available=badges_available,
        )

    async def _async_get_badges(
        self, tz, previous: SmashrunData | None
    ) -> tuple[list[SmashrunBadge], bool]:
        """Badges are best effort: keep the last list if the endpoint fails."""
        try:
            return await self.api.async_get_badges(tz), True
        except SmashrunAuthError:
            raise
        except SmashrunError as err:
            _LOGGER.debug("Could not fetch Smashrun badges: %s", err)
            if previous is not None and previous.badges_available:
                return previous.badges, True
            return [], False

    async def _async_update_token_expiry(self) -> None:
        """Best effort: the token check only feeds the "token expires" sensor."""
        try:
            seconds = await self.api.async_get_token_expires_in()
        except SmashrunAuthError:
            raise
        except SmashrunError as err:
            _LOGGER.debug("Could not check the Smashrun token: %s", err)
            return
        if seconds is not None:
            self.token_expires = dt_util.utcnow() + timedelta(seconds=seconds)

    def _async_update_statistics(self, runs: list[SmashrunRun]) -> None:
        """Rewrite the long-term distance/run statistics when the run list changed."""
        signature = runs_signature(runs)
        if signature == self._stats_signature:
            return
        try:
            from .history import async_write_statistics

            async_write_statistics(self.hass, self.uid, self.user, runs)
        except Exception:  # noqa: BLE001 - statistics must never break polling
            _LOGGER.warning("Could not write Smashrun statistics", exc_info=True)
            return
        self._stats_signature = signature


def _run_key(run: SmashrunRun | None) -> tuple | None:
    if run is None:
        return None
    return (run.run_id, run.distance_km, run.duration_s)
