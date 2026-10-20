"""The Smashrun integration."""

from __future__ import annotations

from homeassistant.const import CONF_ACCESS_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SmashrunApi, SmashrunAuthError, SmashrunError
from .coordinator import SmashrunConfigEntry, SmashrunCoordinator

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SmashrunConfigEntry) -> bool:
    """Set up Smashrun from a config entry."""
    api = SmashrunApi(async_get_clientsession(hass), entry.data[CONF_ACCESS_TOKEN])
    try:
        user = await api.async_get_userinfo()
    except SmashrunAuthError as err:
        # Expired (about every 60 days) or revoked: ask for a new token.
        raise ConfigEntryAuthFailed(str(err)) from err
    except SmashrunError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = SmashrunCoordinator(hass, entry, api, user)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))
    return True


async def _async_reload_on_update(hass: HomeAssistant, entry: SmashrunConfigEntry) -> None:
    """Reload when the options change (a new token reloads through reauth)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SmashrunConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
