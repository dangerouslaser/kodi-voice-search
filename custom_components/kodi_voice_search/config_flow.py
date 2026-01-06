"""Config flow for Kodi Voice Search integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import asyncssh
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
    CONF_SSH_USERNAME,
    CONF_SSH_PASSWORD,
    CONF_SSH_PORT,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_WINDOW_ID,
    DEFAULT_SSH_PORT,
    DEFAULT_SSH_USERNAME,
    KODI_ADDON_ID,
    KODI_ADDON_PATH,
    ADDON_XML,
    ADDON_PY,
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

INSTALL_METHOD_AUTO = "auto"
INSTALL_METHOD_MANUAL = "manual"

STEP_ADDON_MISSING_SCHEMA = vol.Schema(
    {
        vol.Required("install_method", default=INSTALL_METHOD_AUTO): vol.In(
            {
                INSTALL_METHOD_AUTO: "Auto-install via SSH (recommended)",
                INSTALL_METHOD_MANUAL: "I'll install it manually",
            }
        ),
    }
)

STEP_SSH_INSTALL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SSH_USERNAME, default=DEFAULT_SSH_USERNAME): str,
        vol.Optional(CONF_SSH_PASSWORD, default=""): str,
        vol.Required(CONF_SSH_PORT, default=DEFAULT_SSH_PORT): int,
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


async def install_addon_via_ssh(
    host: str,
    username: str,
    password: str | None,
    port: int
) -> bool:
    """Install the Kodi addon via SSH."""
    try:
        # Connect with or without password
        connect_kwargs = {
            "host": host,
            "port": port,
            "username": username,
            "known_hosts": None,  # Skip host key verification
        }

        if password:
            connect_kwargs["password"] = password
        else:
            # Try without password (some CoreELEC setups)
            connect_kwargs["password"] = ""

        async with asyncssh.connect(**connect_kwargs) as conn:
            # Create addon directory
            await conn.run(f"mkdir -p {KODI_ADDON_PATH}", check=True)

            # Write addon.xml
            escaped_xml = ADDON_XML.replace("'", "'\\''")
            await conn.run(
                f"cat > {KODI_ADDON_PATH}/addon.xml << 'ADDONXML'\n{ADDON_XML}\nADDONXML",
                check=True
            )

            # Write default.py
            await conn.run(
                f"cat > {KODI_ADDON_PATH}/default.py << 'ADDONPY'\n{ADDON_PY}\nADDONPY",
                check=True
            )

            _LOGGER.info("Successfully installed Kodi addon via SSH")
            return True

    except asyncssh.PermissionDenied:
        _LOGGER.error("SSH permission denied - check credentials")
        raise SSHFailed("Permission denied")
    except asyncssh.HostKeyNotVerifiable:
        _LOGGER.error("SSH host key not verifiable")
        raise SSHFailed("Host key verification failed")
    except Exception as err:
        _LOGGER.error("SSH installation failed: %s", err)
        raise SSHInstallFailed(str(err))


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kodi Voice Search."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._kodi_data: dict[str, Any] = {}

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
                # Store Kodi config for later
                self._kodi_data = user_input

                # If addon is not installed, offer to install it
                if not info["addon_installed"]:
                    return await self.async_step_addon_missing()

                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "window_id_help": "Arctic Fuse 2 uses 11185 for search"
            },
        )

    async def async_step_addon_missing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the addon missing step - ask how to install."""
        if user_input is not None:
            if user_input.get("install_method") == INSTALL_METHOD_AUTO:
                return await self.async_step_ssh_install()
            else:
                return await self.async_step_addon_confirm()

        return self.async_show_form(
            step_id="addon_missing",
            data_schema=STEP_ADDON_MISSING_SCHEMA,
        )

    async def async_step_ssh_install(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle SSH installation step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                # Use Kodi host for SSH
                await install_addon_via_ssh(
                    host=self._kodi_data[CONF_KODI_HOST],
                    username=user_input[CONF_SSH_USERNAME],
                    password=user_input.get(CONF_SSH_PASSWORD),
                    port=user_input[CONF_SSH_PORT],
                )
                # Show success message and create entry
                return await self.async_step_install_success()
            except SSHFailed:
                errors["base"] = "ssh_failed"
            except SSHInstallFailed:
                errors["base"] = "ssh_install_failed"
            except Exception:
                _LOGGER.exception("Unexpected SSH exception")
                errors["base"] = "ssh_install_failed"

        return self.async_show_form(
            step_id="ssh_install",
            data_schema=STEP_SSH_INSTALL_SCHEMA,
            errors=errors,
        )

    async def async_step_install_success(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show success message after SSH install."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"Kodi ({self._kodi_data[CONF_KODI_HOST]})",
                data=self._kodi_data,
            )

        return self.async_show_form(step_id="install_success")

    async def async_step_addon_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm setup despite missing addon (manual install)."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"Kodi ({self._kodi_data[CONF_KODI_HOST]})",
                data=self._kodi_data,
            )

        return self.async_show_form(step_id="addon_confirm")


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class SSHFailed(HomeAssistantError):
    """Error to indicate SSH connection failed."""


class SSHInstallFailed(HomeAssistantError):
    """Error to indicate SSH addon installation failed."""
