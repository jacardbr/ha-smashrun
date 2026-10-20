"""Diagnostics for Smashrun."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import SmashrunConfigEntry

TO_REDACT = {
    "token",
    "access_token",
    "refresh_token",
    "email",
    "password",
    "firstName",
    "lastName",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmashrunConfigEntry
) -> dict[str, Any]:
    """Tokens, email, password and names redacted; raw payloads included to
    debug field names."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "token_expires": coordinator.token_expires.isoformat()
        if coordinator.token_expires
        else None,
        "user": async_redact_data(coordinator.user, TO_REDACT),
        "last_run": data.last_run.raw if data.last_run else None,
        "run_count": len(data.runs),
        "first_runs": [r.raw for r in data.runs[:3]],
        "badges_available": data.badges_available,
        "badge_count": len(data.badges),
        "first_badges": [b.raw for b in data.badges[:3]],
    }
