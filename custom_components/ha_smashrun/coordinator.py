"""Data update coordinator for Smashrun."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SmashrunApi,
    SmashrunAuthError,
    SmashrunBadge,
    SmashrunError,
    SmashrunLoginError,
    SmashrunRun,
    async_login,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_TOKEN_RENEWED_AT,
    FULL_REFRESH_HOURS,
    TOKEN_MAX_DAYS,
    TOKEN_RENEW_BEFORE_DAYS,
    TOKEN_RENEW_MIN_HOURS,
)
from .runs_util import runs_signature

_LOGGER = logging.getLogger(__name__)

type SmashrunConfigEntry = ConfigEntry[SmashrunCoordinator]


def token_data_from_login(token: str, expires_in: float | None) -> dict[str, Any]:
    """Entry data for a token from the login: the token, when it expires
    (Smashrun's expires_in, at most TOKEN_MAX_DAYS) and when it was obtained."""
    now = dt_util.utcnow()
    lifetime = timedelta(days=TOKEN_MAX_DAYS)
    if expires_in and expires_in > 0:
        lifetime = min(lifetime, timedelta(seconds=expires_in))
    return {
        CONF_ACCESS_TOKEN: token,
        CONF_TOKEN_EXPIRES_AT: (now + lifetime).isoformat(),
        CONF_TOKEN_RENEWED_AT: now.isoformat(),
    }


def has_credentials(entry: ConfigEntry) -> bool:
    """The email and password are saved, so the token renews itself."""
    return bool(entry.data.get(CONF_EMAIL) and entry.data.get(CONF_PASSWORD))


async def async_renew_token(
    hass: HomeAssistant, entry: ConfigEntry, api: SmashrunApi
) -> datetime | None:
    """Get a new token with the saved email and password.

    The API client and the config entry get the new token, its expiry and the
    renewal time (the entry update does not reload it). Returns when the new
    token expires, if Smashrun said. Raises SmashrunLoginError when the email
    or password no longer work.
    """
    token, expires_in = await async_login(
        async_get_clientsession(hass), entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD]
    )
    token_data = token_data_from_login(token, expires_in)
    api.set_token(token)
    hass.config_entries.async_update_entry(entry, data={**entry.data, **token_data})
    expires = dt_util.parse_datetime(token_data[CONF_TOKEN_EXPIRES_AT])
    _LOGGER.info("Got a new Smashrun access token for %s (expires %s)", entry.title, expires)
    return expires


def saved_token_expiry(entry: ConfigEntry) -> datetime | None:
    """The expiry saved with the token by the last login, at most
    TOKEN_MAX_DAYS after it was obtained (older entries may hold more)."""
    expires = dt_util.parse_datetime(entry.data.get(CONF_TOKEN_EXPIRES_AT) or "")
    renewed = dt_util.parse_datetime(entry.data.get(CONF_TOKEN_RENEWED_AT) or "")
    if expires and renewed:
        expires = min(expires, renewed + timedelta(days=TOKEN_MAX_DAYS))
    return expires


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
        # Snapshot used to tell option changes apart from other entry updates
        # (the history import's flag and cutoffs) when deciding to reload.
        self.options = dict(entry.options)
        # When the personal access token stops working (about 60 days): the
        # login's answer when the integration got the token itself, else the
        # token check.
        self.token_expires: datetime | None = saved_token_expiry(entry)
        # Location and track of runs the card asked about (by run id).
        self.run_details: dict[str, dict[str, Any]] = {}
        self._full_at = 0.0
        self._stats_signature: tuple | None = None

    @property
    def imperial(self) -> bool:
        """The user's Smashrun distance unit is miles."""
        return str(self.user.get("unitDistance") or "").lower() == "m"

    async def _async_update_data(self) -> SmashrunData:
        renewed = await self._async_renew_if_due()
        try:
            return await self._async_fetch()
        except SmashrunAuthError as err:
            # The token was rejected: with the email and password saved, get
            # a new one and try once more; otherwise ask for a new token.
            if not has_credentials(self.config_entry) or renewed:
                raise ConfigEntryAuthFailed(str(err)) from err
            try:
                self.token_expires = await async_renew_token(
                    self.hass, self.config_entry, self.api
                )
            except SmashrunLoginError as login_err:
                raise ConfigEntryAuthFailed(str(login_err)) from login_err
            except SmashrunError as login_err:
                raise UpdateFailed(f"Could not renew the Smashrun token: {login_err}") from login_err
            try:
                return await self._async_fetch()
            except SmashrunAuthError as retry_err:
                raise ConfigEntryAuthFailed(str(retry_err)) from retry_err

    async def _async_renew_if_due(self) -> bool:
        """Renew the token ahead of time when it expires within a few days.

        Best effort: if it fails, the current token keeps working until it
        actually expires (then the rejection above tries again or asks).
        """
        if not has_credentials(self.config_entry) or self.token_expires is None:
            return False
        now = dt_util.utcnow()
        if self.token_expires - now > timedelta(days=TOKEN_RENEW_BEFORE_DAYS):
            return False
        renewed = dt_util.parse_datetime(self.config_entry.data.get(CONF_TOKEN_RENEWED_AT) or "")
        if renewed and now - renewed < timedelta(hours=TOKEN_RENEW_MIN_HOURS):
            return False
        try:
            self.token_expires = await async_renew_token(
                self.hass, self.config_entry, self.api
            )
        except SmashrunError as err:
            _LOGGER.warning("Could not renew the Smashrun token ahead of time: %s", err)
            return False
        return True

    async def _async_fetch(self) -> SmashrunData:
        """Poll Smashrun. SmashrunAuthError is left to the caller."""
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
        except SmashrunAuthError:
            raise
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
        """Best effort: the token check feeds the "token expires" sensor.

        A token the integration got itself keeps the expiry the login gave.
        """
        if has_credentials(self.config_entry) and saved_token_expiry(self.config_entry):
            self.token_expires = saved_token_expiry(self.config_entry)
            return
        try:
            seconds = await self.api.async_get_token_expires_in()
        except SmashrunAuthError:
            raise
        except SmashrunError as err:
            _LOGGER.debug("Could not check the Smashrun token: %s", err)
            return
        # A token in use cannot have expired: zero or negative means the check
        # does not know (seen with tokens from the login), so keep what we have.
        if seconds is None or seconds <= 0:
            _LOGGER.debug("Smashrun token check gave expires_in=%s; ignored", seconds)
            return
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
