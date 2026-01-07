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
    CONF_PIPELINE_ID,
    CONF_SEARCH_METHOD,
    CONF_SSH_USERNAME,
    CONF_SSH_PASSWORD,
    CONF_SSH_PORT,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_WINDOW_ID,
    DEFAULT_SSH_PORT,
    DEFAULT_SSH_USERNAME,
    DEFAULT_SEARCH_METHOD,
    SEARCH_METHOD_SKIN,
    SEARCH_METHOD_DEFAULT,
    SEARCH_METHOD_GLOBAL,
    KODI_ADDON_ID,
    KODI_ADDON_PATH,
    KODI_ADDON_VERSION,
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

SEARCH_METHOD_OPTIONS = {
    SEARCH_METHOD_SKIN: "Skin-Specific (Arctic Fuse 2, etc.)",
    SEARCH_METHOD_DEFAULT: "Default Kodi Search",
    SEARCH_METHOD_GLOBAL: "Global Search Addon",
}


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

    # Check addon status (installed and version)
    addon_status = await check_addon_status(
        data[CONF_KODI_HOST],
        data[CONF_KODI_PORT],
        data[CONF_KODI_USERNAME],
        data[CONF_KODI_PASSWORD],
    )

    return {
        "title": f"Kodi ({data[CONF_KODI_HOST]})",
        "addon_installed": addon_status["installed"],
        "addon_needs_update": addon_status["needs_update"],
        "addon_version": addon_status["version"],
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


async def check_addon_status(host: str, port: int, username: str, password: str) -> dict:
    """Check addon installation status and version.

    Returns dict with:
        - installed: bool
        - version: str or None
        - needs_update: bool
    """
    url = f"http://{host}:{port}/jsonrpc"

    payload = {
        "jsonrpc": "2.0",
        "method": "Addons.GetAddonDetails",
        "params": {
            "addonid": KODI_ADDON_ID,
            "properties": ["version"]
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
                if "error" in result:
                    return {"installed": False, "version": None, "needs_update": False}

                addon = result.get("result", {}).get("addon", {})
                installed_version = addon.get("version", "0.0.0")
                needs_update = _version_compare(installed_version, KODI_ADDON_VERSION) < 0

                _LOGGER.debug(
                    "Addon status: installed=%s, version=%s, required=%s, needs_update=%s",
                    True, installed_version, KODI_ADDON_VERSION, needs_update
                )

                return {
                    "installed": True,
                    "version": installed_version,
                    "needs_update": needs_update
                }
    except Exception as err:
        _LOGGER.error("Failed to check addon status: %s", err)
        return {"installed": False, "version": None, "needs_update": False}


def _version_compare(v1: str, v2: str) -> int:
    """Compare two version strings. Returns -1 if v1 < v2, 0 if equal, 1 if v1 > v2."""
    def normalize(v):
        return [int(x) for x in v.split(".")]

    n1, n2 = normalize(v1), normalize(v2)

    # Pad shorter version with zeros
    while len(n1) < len(n2):
        n1.append(0)
    while len(n2) < len(n1):
        n2.append(0)

    for a, b in zip(n1, n2):
        if a < b:
            return -1
        if a > b:
            return 1
    return 0


async def check_addon_installed(host: str, port: int, username: str, password: str) -> bool:
    """Check if the helper addon is installed on Kodi (legacy wrapper)."""
    status = await check_addon_status(host, port, username, password)
    return status["installed"]


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

        # Only set password if provided - omitting lets asyncssh try key-based auth
        if password:
            connect_kwargs["password"] = password

        async with asyncssh.connect(**connect_kwargs) as conn:
            # Create addon directory
            await conn.run("mkdir -p " + KODI_ADDON_PATH, check=True)

            # Write addon.xml
            # Use string concatenation to avoid f-string issues with ADDON content
            xml_cmd = "cat > " + KODI_ADDON_PATH + "/addon.xml << 'ADDONXML'\n" + ADDON_XML + "\nADDONXML"
            await conn.run(xml_cmd, check=True)

            # Write default.py
            # ADDON_PY contains f-strings with {braces}, so we can't use f-strings here
            py_cmd = "cat > " + KODI_ADDON_PATH + "/default.py << 'ADDONPY'\n" + ADDON_PY + "\nADDONPY"
            await conn.run(py_cmd, check=True)

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

        # Only set password if provided - omitting lets asyncssh try key-based auth
        if password:
            connect_kwargs["password"] = password

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


def get_pipelines(hass: HomeAssistant) -> dict[str, str]:
    """Get available Assist pipelines as a dict of {id: name}."""
    pipelines = {}
    try:
        from homeassistant.components.assist_pipeline import async_get_pipelines
        for pipeline in async_get_pipelines(hass):
            pipelines[pipeline.id] = pipeline.name
    except Exception:
        pass
    return pipelines


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kodi Voice Search."""

    VERSION = 3

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._kodi_data: dict[str, Any] = {}
        self._ssh_data: dict[str, Any] = {}
        self._prerequisites: dict[str, Any] = {}
        self._available_pipelines: dict[str, str] = {}
        self._addon_version: str | None = None

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - check prerequisites."""
        # Check voice assistant prerequisites
        self._prerequisites = check_voice_assistant_prerequisites(self.hass)
        # Get available pipelines for later
        self._available_pipelines = get_pipelines(self.hass)

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
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Store Kodi config for later
                self._kodi_data = user_input
                self._addon_version = info.get("addon_version")

                # If addon is not installed, offer to install it
                if not info["addon_installed"]:
                    return await self.async_step_addon_missing()

                # If addon needs update, offer to update it
                if info["addon_needs_update"]:
                    return await self.async_step_addon_update()

                return await self._finish_setup()

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

    async def async_step_addon_update(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the addon update step - addon installed but outdated."""
        if user_input is not None:
            if user_input.get("install_method") == INSTALL_METHOD_AUTO:
                return await self.async_step_ssh_install()
            else:
                # User chose to skip update
                return await self._finish_setup()

        return self.async_show_form(
            step_id="addon_update",
            data_schema=STEP_ADDON_MISSING_SCHEMA,
            description_placeholders={
                "installed_version": self._addon_version or "unknown",
                "required_version": KODI_ADDON_VERSION,
            },
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
            return await self._finish_setup()

        return self.async_show_form(step_id="install_success")

    async def async_step_restart_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle case where SSH restart command failed."""
        if user_input is not None:
            return await self._finish_setup()

        return self.async_show_form(step_id="restart_failed")

    async def async_step_restart_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle case where Kodi didn't come back in time."""
        if user_input is not None:
            return await self._finish_setup()

        return self.async_show_form(step_id="restart_timeout")

    async def async_step_addon_not_detected(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle case where addon wasn't detected after restart."""
        if user_input is not None:
            return await self._finish_setup()

        return self.async_show_form(step_id="addon_not_detected")

    async def async_step_addon_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm setup despite missing addon (manual install)."""
        if user_input is not None:
            return await self._finish_setup()

        return self.async_show_form(step_id="addon_confirm")

    async def _finish_setup(self) -> FlowResult:
        """Complete setup - show search method and pipeline selection."""
        # Check if this is a reconfigure
        if self.context.get("entry_id"):
            # Reconfigure - just abort with success since addon was updated
            return self.async_abort(reason="reconfigure_successful")

        # Show search method selection step
        return await self.async_step_search_method()

    async def async_step_search_method(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle search method selection."""
        if user_input is not None:
            self._kodi_data[CONF_SEARCH_METHOD] = user_input.get(
                CONF_SEARCH_METHOD, DEFAULT_SEARCH_METHOD
            )

            # If there are multiple pipelines, show pipeline selection
            if len(self._available_pipelines) > 1:
                return await self.async_step_pipeline()
            # If only one pipeline, use it automatically
            elif len(self._available_pipelines) == 1:
                pipeline_id = next(iter(self._available_pipelines))
                self._kodi_data[CONF_PIPELINE_ID] = pipeline_id
            # If no pipelines, leave unset (will use default routing)

            return self.async_create_entry(
                title=f"Kodi ({self._kodi_data[CONF_KODI_HOST]})",
                data=self._kodi_data,
            )

        return self.async_show_form(
            step_id="search_method",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_SEARCH_METHOD, default=DEFAULT_SEARCH_METHOD
                ): vol.In(SEARCH_METHOD_OPTIONS),
            }),
        )

    async def async_step_pipeline(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle pipeline selection for multi-Kodi routing."""
        if user_input is not None:
            pipeline_id = user_input.get(CONF_PIPELINE_ID)
            if pipeline_id and pipeline_id != "_none_":
                self._kodi_data[CONF_PIPELINE_ID] = pipeline_id

            return self.async_create_entry(
                title=f"Kodi ({self._kodi_data[CONF_KODI_HOST]})",
                data=self._kodi_data,
            )

        # Build options: "None (default)" + all pipelines
        pipeline_options = {"_none_": "None (use as default)"}
        pipeline_options.update(self._available_pipelines)

        return self.async_show_form(
            step_id="pipeline",
            data_schema=vol.Schema({
                vol.Required(CONF_PIPELINE_ID, default="_none_"): vol.In(pipeline_options),
            }),
            description_placeholders={
                "pipeline_count": str(len(self._available_pipelines)),
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration - check addon status and offer update."""
        # Get the existing config entry
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if not entry:
            return self.async_abort(reason="reconfigure_failed")

        # Store existing data
        self._kodi_data = dict(entry.data)

        # Check addon status
        addon_status = await check_addon_status(
            self._kodi_data[CONF_KODI_HOST],
            self._kodi_data[CONF_KODI_PORT],
            self._kodi_data[CONF_KODI_USERNAME],
            self._kodi_data[CONF_KODI_PASSWORD],
        )

        self._addon_version = addon_status.get("version")

        if not addon_status["installed"]:
            return await self.async_step_addon_missing()

        if addon_status["needs_update"]:
            return await self.async_step_addon_update()

        # Addon is up to date
        return self.async_abort(
            reason="addon_up_to_date",
            description_placeholders={"installed_version": self._addon_version or "unknown"},
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Kodi Voice Search."""

    def __init__(self) -> None:
        """Initialize options flow."""
        super().__init__()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        # Get available pipelines
        available_pipelines = get_pipelines(self.hass)

        if user_input is not None:
            # Validate new credentials before saving
            host = user_input.get(CONF_KODI_HOST, self.config_entry.data.get(CONF_KODI_HOST))
            port = user_input.get(CONF_KODI_PORT, self.config_entry.data.get(CONF_KODI_PORT, DEFAULT_PORT))
            username = user_input.get(CONF_KODI_USERNAME, self.config_entry.data.get(CONF_KODI_USERNAME))
            password = user_input.get(CONF_KODI_PASSWORD, self.config_entry.data.get(CONF_KODI_PASSWORD))

            if not await ping_kodi(host, port, username, password):
                errors["base"] = "cannot_connect"
            else:
                # Merge with existing data
                new_data = {**self.config_entry.data}

                # Update Kodi connection settings
                new_data[CONF_KODI_HOST] = host
                new_data[CONF_KODI_PORT] = port
                new_data[CONF_KODI_USERNAME] = username
                new_data[CONF_KODI_PASSWORD] = password
                new_data[CONF_WINDOW_ID] = user_input.get(
                    CONF_WINDOW_ID, self.config_entry.data.get(CONF_WINDOW_ID, DEFAULT_WINDOW_ID)
                )
                new_data[CONF_SEARCH_METHOD] = user_input.get(
                    CONF_SEARCH_METHOD, self.config_entry.data.get(CONF_SEARCH_METHOD, DEFAULT_SEARCH_METHOD)
                )

                # Handle pipeline selection
                pipeline_id = user_input.get(CONF_PIPELINE_ID)
                if pipeline_id and pipeline_id != "_none_":
                    new_data[CONF_PIPELINE_ID] = pipeline_id
                elif CONF_PIPELINE_ID in new_data:
                    # Remove pipeline if "None" selected
                    if pipeline_id == "_none_":
                        del new_data[CONF_PIPELINE_ID]

                # Update the config entry data
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data=new_data,
                    title=f"Kodi ({new_data[CONF_KODI_HOST]})",
                )

                return self.async_create_entry(title="", data={})

        # Build pipeline options
        pipeline_options = {"_none_": "None (use as default)"}
        pipeline_options.update(available_pipelines)

        # Get current values for defaults
        current_data = self.config_entry.data
        current_pipeline = current_data.get(CONF_PIPELINE_ID, "_none_")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_KODI_HOST,
                    default=current_data.get(CONF_KODI_HOST, "")
                ): str,
                vol.Required(
                    CONF_KODI_PORT,
                    default=current_data.get(CONF_KODI_PORT, DEFAULT_PORT)
                ): int,
                vol.Required(
                    CONF_KODI_USERNAME,
                    default=current_data.get(CONF_KODI_USERNAME, DEFAULT_USERNAME)
                ): str,
                vol.Required(
                    CONF_KODI_PASSWORD,
                    default=current_data.get(CONF_KODI_PASSWORD, DEFAULT_PASSWORD)
                ): str,
                vol.Required(
                    CONF_WINDOW_ID,
                    default=current_data.get(CONF_WINDOW_ID, DEFAULT_WINDOW_ID)
                ): str,
                vol.Required(
                    CONF_SEARCH_METHOD,
                    default=current_data.get(CONF_SEARCH_METHOD, DEFAULT_SEARCH_METHOD)
                ): vol.In(SEARCH_METHOD_OPTIONS),
                vol.Required(
                    CONF_PIPELINE_ID,
                    default=current_pipeline if current_pipeline else "_none_"
                ): vol.In(pipeline_options),
            }),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class SSHFailed(HomeAssistantError):
    """Error to indicate SSH connection failed."""


class SSHInstallFailed(HomeAssistantError):
    """Error to indicate SSH addon installation failed."""
