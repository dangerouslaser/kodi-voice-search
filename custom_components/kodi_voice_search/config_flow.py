"""Config flow for Kodi Voice Search integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import asyncssh
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

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

# Timeouts and retry settings
KODI_RESTART_WAIT = 5  # Seconds to wait for Kodi to shut down
KODI_POLL_INTERVAL = 2  # Seconds between ping attempts
KODI_RESTART_TIMEOUT = 60  # Max seconds to wait for Kodi to come back

STEP_KODI_DATA_SCHEMA = vol.Schema(
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


def check_voice_assistant_prerequisites(hass: HomeAssistant) -> dict[str, Any]:
    """Check if voice assistant prerequisites are met."""
    results = {
        "stt_available": False,
        "stt_provider": None,
        "assist_available": False,
        "pipeline_count": 0,
        "all_ready": False,
    }

    # Check for STT providers
    # Look for common STT integrations
    stt_integrations = ["wyoming", "whisper", "cloud", "google_cloud_speech"]
    for integration in stt_integrations:
        if integration in hass.config.components:
            results["stt_available"] = True
            results["stt_provider"] = integration
            break

    # Also check if stt domain has any entities
    entity_reg = er.async_get(hass)
    stt_entities = [
        entity for entity in entity_reg.entities.values()
        if entity.domain == "stt" or entity.platform in stt_integrations
    ]
    if stt_entities:
        results["stt_available"] = True
        if not results["stt_provider"]:
            results["stt_provider"] = stt_entities[0].platform

    # Check for Assist Pipeline
    if "assist_pipeline" in hass.config.components:
        results["assist_available"] = True
        # Try to get pipeline count
        try:
            from homeassistant.components.assist_pipeline import async_get_pipelines
            pipelines = async_get_pipelines(hass)
            results["pipeline_count"] = len(list(pipelines))
        except Exception:
            # If we can't get pipelines, assume at least default exists
            results["pipeline_count"] = 1

    # Check if everything is ready
    results["all_ready"] = results["stt_available"] and results["assist_available"]

    return results


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


async def ping_kodi(host: str, port: int, username: str, password: str) -> bool:
    """Ping Kodi to check if it's responding."""
    url = f"http://{host}:{port}/jsonrpc"
    payload = {"jsonrpc": "2.0", "method": "JSONRPC.Ping", "id": 1}
    auth = aiohttp.BasicAuth(username, password)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                auth=auth,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                result = await response.json()
                return result.get("result") == "pong"
    except Exception:
        return False


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


async def enable_addon(host: str, port: int, username: str, password: str) -> bool:
    """Enable the addon via JSON-RPC."""
    url = f"http://{host}:{port}/jsonrpc"

    payload = {
        "jsonrpc": "2.0",
        "method": "Addons.SetAddonEnabled",
        "params": {
            "addonid": KODI_ADDON_ID,
            "enabled": True
        },
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
                success = result.get("result") == "OK"
                if success:
                    _LOGGER.info("Successfully enabled Kodi addon")
                return success
    except Exception as err:
        _LOGGER.error("Failed to enable addon: %s", err)
        return False


async def install_addon_via_ssh(
    host: str,
    username: str,
    password: str | None,
    port: int
) -> bool:
    """Install the Kodi addon via SSH."""
    try:
        connect_kwargs = {
            "host": host,
            "port": port,
            "username": username,
            "known_hosts": None,
        }

        if password:
            connect_kwargs["password"] = password
        else:
            connect_kwargs["password"] = ""

        async with asyncssh.connect(**connect_kwargs) as conn:
            # Create addon directory
            await conn.run(f"mkdir -p {KODI_ADDON_PATH}", check=True)

            # Write addon.xml
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


async def restart_kodi_via_ssh(
    host: str,
    username: str,
    password: str | None,
    port: int
) -> bool:
    """Restart Kodi service via SSH."""
    try:
        connect_kwargs = {
            "host": host,
            "port": port,
            "username": username,
            "known_hosts": None,
        }

        if password:
            connect_kwargs["password"] = password
        else:
            connect_kwargs["password"] = ""

        async with asyncssh.connect(**connect_kwargs) as conn:
            result = await conn.run("systemctl restart kodi", check=False)
            if result.exit_status == 0:
                _LOGGER.info("Successfully restarted Kodi via SSH")
                return True
            else:
                _LOGGER.error("Failed to restart Kodi: %s", result.stderr)
                return False

    except Exception as err:
        _LOGGER.error("SSH restart failed: %s", err)
        return False


async def wait_for_kodi(
    host: str,
    port: int,
    username: str,
    password: str,
    timeout: int = KODI_RESTART_TIMEOUT
) -> bool:
    """Wait for Kodi to come back online after restart."""
    _LOGGER.info("Waiting for Kodi to restart...")

    # Wait for Kodi to shut down first
    await asyncio.sleep(KODI_RESTART_WAIT)

    elapsed = 0
    while elapsed < timeout:
        if await ping_kodi(host, port, username, password):
            _LOGGER.info("Kodi is back online after %d seconds", elapsed + KODI_RESTART_WAIT)
            return True
        await asyncio.sleep(KODI_POLL_INTERVAL)
        elapsed += KODI_POLL_INTERVAL

    _LOGGER.error("Timeout waiting for Kodi to restart")
    return False


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kodi Voice Search."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._kodi_data: dict[str, Any] = {}
        self._ssh_data: dict[str, Any] = {}
        self._prerequisites: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - check prerequisites."""
        # Check voice assistant prerequisites
        self._prerequisites = check_voice_assistant_prerequisites(self.hass)

        if self._prerequisites["all_ready"]:
            # All prerequisites met, proceed to Kodi config
            return await self.async_step_kodi()
        else:
            # Show prerequisites warning
            return await self.async_step_prerequisites_missing()

    async def async_step_prerequisites_missing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle missing prerequisites - show warning."""
        if user_input is not None:
            # User chose to continue anyway
            if user_input.get("continue_anyway"):
                return await self.async_step_kodi()
            # Otherwise, abort
            return self.async_abort(reason="prerequisites_not_met")

        # Build description placeholders based on what's missing
        missing_items = []
        if not self._prerequisites["stt_available"]:
            missing_items.append("Speech-to-Text (Whisper)")
        if not self._prerequisites["assist_available"]:
            missing_items.append("Assist Pipeline")

        return self.async_show_form(
            step_id="prerequisites_missing",
            data_schema=vol.Schema({
                vol.Required("continue_anyway", default=False): bool,
            }),
            description_placeholders={
                "missing_items": ", ".join(missing_items),
                "stt_status": "✓ Found" if self._prerequisites["stt_available"] else "✗ Not found",
                "stt_provider": self._prerequisites["stt_provider"] or "None",
                "assist_status": "✓ Found" if self._prerequisites["assist_available"] else "✗ Not found",
                "pipeline_count": str(self._prerequisites["pipeline_count"]),
            },
        )

    async def async_step_kodi(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Kodi connection configuration."""
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
            step_id="kodi",
            data_schema=STEP_KODI_DATA_SCHEMA,
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
                # Store SSH credentials for restart step
                self._ssh_data = user_input

                # Install addon via SSH
                await install_addon_via_ssh(
                    host=self._kodi_data[CONF_KODI_HOST],
                    username=user_input[CONF_SSH_USERNAME],
                    password=user_input.get(CONF_SSH_PASSWORD),
                    port=user_input[CONF_SSH_PORT],
                )

                # Proceed to restart Kodi
                return await self.async_step_restarting()

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

    async def async_step_restarting(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Restart Kodi and wait for it to come back."""
        # Restart Kodi via SSH
        restart_success = await restart_kodi_via_ssh(
            host=self._kodi_data[CONF_KODI_HOST],
            username=self._ssh_data[CONF_SSH_USERNAME],
            password=self._ssh_data.get(CONF_SSH_PASSWORD),
            port=self._ssh_data[CONF_SSH_PORT],
        )

        if not restart_success:
            # Restart failed, show manual restart message
            return await self.async_step_restart_failed()

        # Wait for Kodi to come back online
        kodi_online = await wait_for_kodi(
            host=self._kodi_data[CONF_KODI_HOST],
            port=self._kodi_data[CONF_KODI_PORT],
            username=self._kodi_data[CONF_KODI_USERNAME],
            password=self._kodi_data[CONF_KODI_PASSWORD],
        )

        if not kodi_online:
            # Timeout waiting for Kodi
            return await self.async_step_restart_timeout()

        # Kodi is back, now enable the addon
        await enable_addon(
            host=self._kodi_data[CONF_KODI_HOST],
            port=self._kodi_data[CONF_KODI_PORT],
            username=self._kodi_data[CONF_KODI_USERNAME],
            password=self._kodi_data[CONF_KODI_PASSWORD],
        )

        # Verify addon is now installed and enabled
        addon_ready = await check_addon_installed(
            host=self._kodi_data[CONF_KODI_HOST],
            port=self._kodi_data[CONF_KODI_PORT],
            username=self._kodi_data[CONF_KODI_USERNAME],
            password=self._kodi_data[CONF_KODI_PASSWORD],
        )

        if addon_ready:
            return await self.async_step_install_success()
        else:
            return await self.async_step_addon_not_detected()

    async def async_step_install_success(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show success message - addon installed and verified."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"Kodi ({self._kodi_data[CONF_KODI_HOST]})",
                data=self._kodi_data,
            )

        return self.async_show_form(step_id="install_success")

    async def async_step_restart_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle case where SSH restart command failed."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"Kodi ({self._kodi_data[CONF_KODI_HOST]})",
                data=self._kodi_data,
            )

        return self.async_show_form(step_id="restart_failed")

    async def async_step_restart_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle case where Kodi didn't come back in time."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"Kodi ({self._kodi_data[CONF_KODI_HOST]})",
                data=self._kodi_data,
            )

        return self.async_show_form(step_id="restart_timeout")

    async def async_step_addon_not_detected(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle case where addon wasn't detected after restart."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"Kodi ({self._kodi_data[CONF_KODI_HOST]})",
                data=self._kodi_data,
            )

        return self.async_show_form(step_id="addon_not_detected")

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
