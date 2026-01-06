"""Kodi Voice Search integration for Home Assistant."""
from __future__ import annotations

import logging
import aiohttp
import asyncio
import base64
import json

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import intent
import voluptuous as vol

from .const import (
    DOMAIN,
    CONF_KODI_HOST,
    CONF_KODI_PORT,
    CONF_KODI_USERNAME,
    CONF_KODI_PASSWORD,
    CONF_WINDOW_ID,
    SERVICE_SEARCH,
    ATTR_QUERY,
    KODI_ADDON_ID,
    DEFAULT_WINDOW_ID,
)

_LOGGER = logging.getLogger(__name__)

SEARCH_SCHEMA = vol.Schema({
    vol.Required(ATTR_QUERY): str,
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kodi Voice Search from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    config = entry.data
    host = config[CONF_KODI_HOST]
    port = config[CONF_KODI_PORT]
    username = config[CONF_KODI_USERNAME]
    password = config[CONF_KODI_PASSWORD]
    window_id = config.get(CONF_WINDOW_ID, DEFAULT_WINDOW_ID)
    
    # Store config for later use
    hass.data[DOMAIN][entry.entry_id] = {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "window_id": window_id,
    }
    
    async def async_search(call: ServiceCall) -> None:
        """Handle the search service call."""
        query = call.data[ATTR_QUERY]
        await _execute_search(hass, entry.entry_id, query)
    
    # Register service
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH,
        async_search,
        schema=SEARCH_SCHEMA,
    )
    
    # Register intent handler for voice commands
    intent.async_register(hass, KodiSearchIntentHandler())
    
    _LOGGER.info("Kodi Voice Search integration loaded for %s:%s", host, port)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.services.async_remove(DOMAIN, SERVICE_SEARCH)
    hass.data[DOMAIN].pop(entry.entry_id)
    return True


async def _execute_search(hass: HomeAssistant, entry_id: str, query: str) -> bool:
    """Execute search on Kodi."""
    config = hass.data[DOMAIN][entry_id]
    
    url = f"http://{config['host']}:{config['port']}/jsonrpc"
    
    payload = {
        "jsonrpc": "2.0",
        "method": "Addons.ExecuteAddon",
        "params": {
            "addonid": KODI_ADDON_ID,
            "params": f"window={config['window_id']}&search={query}"
        },
        "id": 1
    }
    
    auth = aiohttp.BasicAuth(config['username'], config['password'])
    
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
                    _LOGGER.error("Kodi error: %s", result["error"])
                    return False
                _LOGGER.debug("Kodi search executed: %s", query)
                return True
    except aiohttp.ClientError as err:
        _LOGGER.error("Error communicating with Kodi: %s", err)
        return False
    except asyncio.TimeoutError:
        _LOGGER.error("Timeout communicating with Kodi")
        return False


class KodiSearchIntentHandler(intent.IntentHandler):
    """Handle Kodi search intents."""
    
    intent_type = "KodiSearch"
    
    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = intent_obj.slots
        
        query = slots.get("query", {}).get("value", "")
        
        if not query:
            response = intent_obj.create_response()
            response.async_set_speech("I didn't catch what you wanted to search for.")
            return response
        
        # Find the first configured entry
        if DOMAIN in hass.data and hass.data[DOMAIN]:
            entry_id = next(iter(hass.data[DOMAIN]))
            success = await _execute_search(hass, entry_id, query)
            
            response = intent_obj.create_response()
            if success:
                response.async_set_speech(f"Searching for {query} on Kodi")
            else:
                response.async_set_speech("Sorry, I couldn't connect to Kodi")
            return response
        
        response = intent_obj.create_response()
        response.async_set_speech("Kodi Voice Search is not configured")
        return response


async def async_check_addon_installed(hass: HomeAssistant, entry_id: str) -> bool:
    """Check if the Kodi addon is installed."""
    config = hass.data[DOMAIN][entry_id]
    
    url = f"http://{config['host']}:{config['port']}/jsonrpc"
    
    payload = {
        "jsonrpc": "2.0",
        "method": "Addons.GetAddonDetails",
        "params": {
            "addonid": KODI_ADDON_ID
        },
        "id": 1
    }
    
    auth = aiohttp.BasicAuth(config['username'], config['password'])
    
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
