"""Config flow for Kodi Voice Search integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    CONF_KODI_HOST,
    CONF_KODI_PORT,
    CONF_KODI_USERNAME,
    CONF_KODI_PASSWORD,
    CONF_WINDOW_ID,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_WINDOW_ID,
    KODI_ADDON_ID,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_KODI_HOST): str,
        vol.Required(CONF_KODI_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_KODI_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_KODI_PASSWORD, default=DEFAULT_PASSWORD): str,
        vol.Required(CONF_WINDOW_ID, default=DEFAULT_WINDOW_ID): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    
    url = f"http://{data[CONF_KODI_HOST]}:{data[CONF_KODI_PORT]}/jsonrpc"
    
    payload = {
        "jsonrpc": "2.0",
        "method": "JSONRPC.Ping",
        "id": 1
    }
    
    auth = aiohttp.BasicAuth(data[CONF_KODI_USERNAME], data[CONF_KODI_PASSWORD])
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                auth=auth,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                result = await response.json()
                if result.get("result") != "pong":
                    raise CannotConnect
    except aiohttp.ClientError as err:
        _LOGGER.error("Cannot connect to Kodi: %s", err)
        raise CannotConnect from err
    except Exception as err:
        _LOGGER.error("Unexpected error: %s", err)
        raise CannotConnect from err
    
    # Check if addon is installed
    addon_installed = await check_addon_installed(
        data[CONF_KODI_HOST],
        data[CONF_KODI_PORT],
        data[CONF_KODI_USERNAME],
        data[CONF_KODI_PASSWORD],
    )
    
    return {
        "title": f"Kodi ({data[CONF_KODI_HOST]})",
        "addon_installed": addon_installed,
    }


async def check_addon_installed(host: str, port: int, username: str, password: str) -> bool:
    """Check if the helper addon is installed on Kodi."""
    url = f"http://{host}:{port}/jsonrpc"
    
    payload = {
        "jsonrpc": "2.0",
        "method": "Addons.GetAddonDetails",
        "params": {"addonid": KODI_ADDON_ID},
        "id": 1
    }
    
    auth = aiohttp.BasicAuth(username, password)
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                auth=auth,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                result = await response.json()
                return "error" not in result
    except Exception:
        return False


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kodi Voice Search."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Store whether addon is installed for showing in description
                if not info["addon_installed"]:
                    return await self.async_step_addon_warning(user_input)
                
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "window_id_help": "Arctic Fuse 2 uses 11185 for search"
            },
        )

    async def async_step_addon_warning(
        self, user_input: dict[str, Any]
    ) -> FlowResult:
        """Warn user that addon is not installed."""
        return self.async_show_form(
            step_id="addon_confirm",
            description_placeholders={
                "addon_instructions": (
                    "The script.openwindow addon is not installed on Kodi. "
                    "Please install it manually. See the documentation for instructions."
                )
            },
            errors={},
        )

    async def async_step_addon_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm setup despite missing addon."""
        if user_input is not None:
            # Get the stored data from the previous step
            return self.async_create_entry(
                title=f"Kodi ({self._user_input[CONF_KODI_HOST]})",
                data=self._user_input,
            )
        
        return self.async_show_form(step_id="addon_confirm")


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
