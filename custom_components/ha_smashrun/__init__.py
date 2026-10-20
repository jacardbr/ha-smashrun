"""The Smashrun integration."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ACCESS_TOKEN, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import SmashrunApi, SmashrunAuthError, SmashrunError, SmashrunLoginError
from .const import CONF_IMPORT_HISTORY, DOMAIN
from .coordinator import (
    SmashrunConfigEntry,
    SmashrunCoordinator,
    async_renew_token,
    has_credentials,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_FILE = "ha-smashrun-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILE}"

SERVICE_IMPORT_HISTORY = "import_history"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

# Sensor keys that existed in earlier versions. Their registry entries are
# removed so they do not linger as "no longer provided".
_REMOVED_SENSORS = (
    "last_run",
    "last_run_heart_rate_average",
    "last_run_cadence",
    "latest_badge",
)


def _async_remove_old_entities(hass: HomeAssistant, uid: str) -> None:
    registry = er.async_get(hass)
    for key in _REMOVED_SENSORS:
        if entity_id := registry.async_get_entity_id("sensor", DOMAIN, f"{uid}_{key}"):
            registry.async_remove(entity_id)


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the Lovelace card and load it in the frontend (best effort)."""
    try:
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig
        from homeassistant.loader import async_get_integration

        path = Path(__file__).parent / "frontend" / CARD_FILE
        if not path.is_file() or getattr(hass, "http", None) is None:
            return
        integration = await async_get_integration(hass, DOMAIN)
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(path), cache_headers=False)]
        )
        # The version and the file's modification time in the query string
        # bust the browser cache on updates, also when the file changes
        # without a new version (manual copies during development).
        mtime = await hass.async_add_executor_job(lambda: int(path.stat().st_mtime))
        add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}-{mtime}")
    except Exception:  # noqa: BLE001 - the card must never break the integration
        _LOGGER.warning("Could not register the Smashrun Lovelace card", exc_info=True)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the Lovelace card, its WebSocket command and the actions."""
    await _async_register_card(hass)

    from . import websocket as smashrun_websocket

    smashrun_websocket.async_register(hass)

    async def _import_history(call: ServiceCall) -> ServiceResponse:
        from .history import async_import_history

        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        if entry_id:
            entry = hass.config_entries.async_get_entry(entry_id)
            if (
                entry is None
                or entry.domain != DOMAIN
                or entry.state is not ConfigEntryState.LOADED
            ):
                raise ServiceValidationError(
                    f"{entry_id} is not a loaded Smashrun config entry"
                )
            entries = [entry]
        else:
            entries = hass.config_entries.async_loaded_entries(DOMAIN)
            if not entries:
                raise ServiceValidationError("No loaded Smashrun config entry")

        results: dict[str, object] = {}
        for entry in entries:
            try:
                results[entry.entry_id] = await async_import_history(hass, entry)
            except HomeAssistantError:
                raise
            except Exception as err:  # SmashrunError and anything unexpected
                raise HomeAssistantError(
                    f"Smashrun history import failed: {err}"
                ) from err
        return {"entries": results} if call.return_response else None

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_HISTORY,
        _import_history,
        schema=vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SmashrunConfigEntry) -> bool:
    """Set up Smashrun from a config entry."""
    api = SmashrunApi(async_get_clientsession(hass), entry.data[CONF_ACCESS_TOKEN])
    token_expires = None
    try:
        user = await api.async_get_userinfo()
    except SmashrunAuthError as err:
        # Expired (about every 60 days) or revoked. With the email and
        # password saved, get a new token; otherwise ask for one.
        if not has_credentials(entry):
            raise ConfigEntryAuthFailed(str(err)) from err
        try:
            token_expires = await async_renew_token(hass, entry, api)
            user = await api.async_get_userinfo()
        except (SmashrunLoginError, SmashrunAuthError) as login_err:
            raise ConfigEntryAuthFailed(str(login_err)) from login_err
        except SmashrunError as login_err:
            raise ConfigEntryNotReady(str(login_err)) from login_err
    except SmashrunError as err:
        raise ConfigEntryNotReady(str(err)) from err

    _async_remove_old_entities(hass, str(user["id"]))

    coordinator = SmashrunCoordinator(hass, entry, api, user)
    if token_expires is not None:
        coordinator.token_expires = token_expires
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))

    # One-shot, requested in the setup form. Runs in the background once the
    # sensors exist in the entity registry; the flag is cleared when it succeeds.
    if entry.data.get(CONF_IMPORT_HISTORY):
        entry.async_create_background_task(
            hass,
            _async_import_history_once(hass, entry),
            f"{DOMAIN}_import_history_{entry.entry_id}",
        )
    return True


async def _async_import_history_once(
    hass: HomeAssistant, entry: SmashrunConfigEntry
) -> None:
    """Import the history, then clear the flag so it never runs again."""
    from .history import async_import_history

    try:
        await async_import_history(hass, entry)
    except Exception as err:  # noqa: BLE001
        # Keep the flag: the import is retried the next time the entry loads.
        # The ha_smashrun.import_history action can also be run manually.
        _LOGGER.warning(
            "Smashrun history import failed, will retry on next start: %s", err
        )
        return
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_IMPORT_HISTORY: False}
    )


async def _async_reload_on_options(hass: HomeAssistant, entry: SmashrunConfigEntry) -> None:
    """Reload only when the options changed.

    The history import also updates the entry (its flag and cutoffs), and a new
    token reloads through reauth/reconfigure on its own.
    """
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None and dict(entry.options) == coordinator.options:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SmashrunConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
