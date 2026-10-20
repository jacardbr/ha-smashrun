"""WebSocket API used by the Lovelace card."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .runs_util import COUNT_BUCKETS, RECORD_DISTANCES, personal_records, yearly_summary

@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the commands."""
    websocket_api.async_register_command(hass, ws_summary)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/summary",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_summary(hass: HomeAssistant, connection, msg) -> None:
    """Distance and run counts per year, and personal records."""
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if msg.get("entry_id"):
        entries = [e for e in entries if e.entry_id == msg["entry_id"]]
    if not entries:
        connection.send_error(msg["id"], "not_found", "Smashrun is not set up")
        return

    coordinator = entries[0].runtime_data
    runs = coordinator.data.runs  # oldest first
    tz = dt_util.get_default_time_zone()
    username = coordinator.user.get("username")

    def url(run_id: str) -> str | None:
        return f"https://smashrun.com/{username}/run/{run_id}" if username else None

    records = personal_records(runs, tz)
    for scope in records.values():
        for record in scope.values():
            if record is not None:
                record["url"] = url(record["run_id"])

    connection.send_result(
        msg["id"],
        {
            "username": username,
            "years": yearly_summary(runs, tz),
            "record_distances": RECORD_DISTANCES,
            "records": records,
            "count_buckets": COUNT_BUCKETS,
        },
    )
