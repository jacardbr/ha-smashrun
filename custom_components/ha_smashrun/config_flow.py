"""Config flow for Smashrun (personal access token)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    SmashrunApi,
    SmashrunAuthError,
    SmashrunConnectionError,
    SmashrunError,
    clean_token,
)
from .const import (
    CONF_IMPORT_HISTORY,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)

_TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCESS_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)
_USER_SCHEMA = _TOKEN_SCHEMA.extend(
    {vol.Required(CONF_IMPORT_HISTORY, default=True): bool}
)


async def _validate(
    hass: HomeAssistant, token: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Read the profile with the token. Returns (userinfo, error_key)."""
    api = SmashrunApi(async_get_clientsession(hass), token)
    try:
        return await api.async_get_userinfo(), None
    except SmashrunAuthError:
        return None, "invalid_auth"
    except SmashrunConnectionError:
        return None, "cannot_connect"
    except SmashrunError:
        _LOGGER.exception("Smashrun token check failed")
        return None, "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error checking the Smashrun token")
        return None, "unknown"


class SmashrunConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smashrun."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = clean_token(user_input[CONF_ACCESS_TOKEN])
            user, error = await _validate(self.hass, token)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(str(user["id"]))
                self._abort_if_unique_id_configured()
                name = user.get("username") or " ".join(
                    p for p in (user.get("firstName"), user.get("lastName")) if p
                )
                return self.async_create_entry(
                    title=f"Smashrun ({name or user['id']})",
                    data={
                        CONF_ACCESS_TOKEN: token,
                        CONF_IMPORT_HISTORY: user_input.get(CONF_IMPORT_HISTORY, False),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _USER_SCHEMA,
                {k: v for k, v in (user_input or {}).items() if k != CONF_ACCESS_TOKEN},
            ),
            errors=errors,
            description_placeholders={"token_url": TOKEN_URL},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_replace_token(
            "reauth_confirm", self._get_reauth_entry(), user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Paste a new token before the old one expires."""
        return await self._async_replace_token(
            "reconfigure", self._get_reconfigure_entry(), user_input
        )

    async def _async_replace_token(
        self, step_id: str, entry: ConfigEntry, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = clean_token(user_input[CONF_ACCESS_TOKEN])
            user, error = await _validate(self.hass, token)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(str(user["id"]))
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_ACCESS_TOKEN: token}
                )

        return self.async_show_form(
            step_id=step_id,
            data_schema=_TOKEN_SCHEMA,
            errors=errors,
            description_placeholders={"token_url": TOKEN_URL},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SmashrunOptionsFlow()


class SmashrunOptionsFlow(OptionsFlow):
    """Options menu: update interval, and a button to import the history."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "import_history"]
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                )
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_import_history(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then import every past run into the sensors' statistics."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")

        if user_input is None:
            return self.async_show_form(
                step_id="import_history", data_schema=vol.Schema({})
            )

        from .history import async_import_history

        try:
            result = await async_import_history(self.hass, self.config_entry)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Smashrun history import failed")
            return self.async_show_form(
                step_id="import_history",
                data_schema=vol.Schema({}),
                errors={"base": "import_failed"},
            )

        return self.async_abort(
            reason="import_done",
            description_placeholders={
                "runs": str(result["runs_found"]),
                "points": str(sum(result["statistics_imported"].values())),
            },
        )
