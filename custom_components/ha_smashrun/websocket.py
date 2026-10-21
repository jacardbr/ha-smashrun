"""WebSocket API used by the Lovelace card."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .api import SmashrunError
from .const import DOMAIN
from .runs_util import (
    COUNT_BUCKETS,
    RECORD_DISTANCES,
    best_days,
    count_buckets,
    personal_records,
    run_details,
    runs_between,
    weekly_summary,
    yearly_summary,
)

@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the commands."""
    websocket_api.async_register_command(hass, ws_summary)
    websocket_api.async_register_command(hass, ws_run)
    websocket_api.async_register_command(hass, ws_runs)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/runs",
        vol.Required("first"): vol.Match(r"^\d{4}-\d{2}-\d{2}$"),
        vol.Required("last"): vol.Match(r"^\d{4}-\d{2}-\d{2}$"),
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_runs(hass: HomeAssistant, connection, msg) -> None:
    """The runs of a date range (local dates), from the runs already read:
    no request to Smashrun."""
    entry = _loaded_entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Smashrun is not set up")
        return
    runs = entry.runtime_data.data.runs
    tz = dt_util.get_default_time_zone()
    connection.send_result(
        msg["id"], {"runs": runs_between(runs, tz, msg["first"], msg["last"])}
    )


def _loaded_entry(hass: HomeAssistant, entry_id: str | None):
    """The Smashrun entry to answer for (the first loaded one by default)."""
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
    return entries[0] if entries else None


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/run",
        vol.Required("run_id"): vol.All(str, vol.Match(r"^\d{1,15}$")),
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_run(hass: HomeAssistant, connection, msg) -> None:
    """One run's location and track (fetched from Smashrun once, then cached)."""
    entry = _loaded_entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Smashrun is not set up")
        return
    coordinator = entry.runtime_data
    run_id = msg["run_id"]
    details = coordinator.run_details.get(run_id)
    if details is None:
        try:
            details = run_details(await coordinator.api.async_get_activity(run_id))
        except SmashrunError as err:
            connection.send_error(msg["id"], "smashrun_error", str(err))
            return
        coordinator.run_details[run_id] = details
    connection.send_result(msg["id"], {"run_id": run_id, **details})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/summary",
        vol.Optional("entry_id"): str,
        # Where the 5K, 10K, 21K and 42K count ranges start, then the end of
        # the 42K, in km (card options). Default: COUNT_BUCKETS.
        vol.Optional("count_limits"): [vol.Coerce(float)],
    }
)
@callback
def ws_summary(hass: HomeAssistant, connection, msg) -> None:
    """Distance per week, month and year, run counts and personal records."""
    entry = _loaded_entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Smashrun is not set up")
        return

    buckets = COUNT_BUCKETS
    if msg.get("count_limits"):
        try:
            buckets = count_buckets(msg["count_limits"])
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_format", str(err))
            return

    coordinator = entry.runtime_data
    runs = coordinator.data.runs  # oldest first
    tz = dt_util.get_default_time_zone()
    username = coordinator.user.get("username")

    def url(run_id: str) -> str | None:
        return f"https://smashrun.com/{username}/run/{run_id}" if username else None

    records = personal_records(runs, tz, buckets)
    for scope in records.values():
        for record in scope.values():
            if record is not None:
                record["url"] = url(record["run_id"])

    connection.send_result(
        msg["id"],
        {
            "username": username,
            "years": yearly_summary(runs, tz, buckets),
            "weeks": weekly_summary(runs, tz),
            "best_days": best_days(runs, tz),
            "record_distances": RECORD_DISTANCES,
            "records": records,
            "count_buckets": buckets,
        },
    )
