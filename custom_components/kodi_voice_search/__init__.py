"""Kodi Voice Search integration for Home Assistant."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import aiohttp
import asyncio
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import intent

from .const import (
    DOMAIN,
    CONF_KODI_HOST,
    CONF_KODI_PORT,
    CONF_KODI_USERNAME,
    CONF_KODI_PASSWORD,
    CONF_WINDOW_ID,
    CONF_PIPELINE_ID,
    SERVICE_SEARCH,
    SERVICE_PULL_UP,
    ATTR_QUERY,
    ATTR_MEDIA_TYPE,
    KODI_ADDON_ID,
    DEFAULT_WINDOW_ID,
)

_LOGGER = logging.getLogger(__name__)

# Current config entry version
CONFIG_VERSION = 2

SEARCH_SCHEMA = vol.Schema({
    vol.Required(ATTR_QUERY): str,
})

PULL_UP_SCHEMA = vol.Schema({
    vol.Required(ATTR_QUERY): str,
    vol.Optional(ATTR_MEDIA_TYPE, default="all"): vol.In(["all", "tv", "movie"]),
})


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry to new version."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        # Version 1 -> 2: Add pipeline_id field (empty = default routing)
        new_data = {**config_entry.data}
        # No pipeline_id means this Kodi will be the default (fallback) target
        # Users can reconfigure to assign a specific pipeline later

        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            version=2,
        )
        _LOGGER.info(
            "Migrated Kodi Voice Search config entry to version 2. "
            "This Kodi instance will handle all voice searches by default. "
            "Reconfigure to assign a specific Voice Assistant pipeline if needed."
        )

    return True

# Custom sentences content
CUSTOM_SENTENCES = '''language: "en"
intents:
  KodiSearch:
    data:
      - sentences:
          - "(search|find|look for) {query} [on] [kodi]"
          - "kodi (search|find) [for] {query}"

  KodiPullUp:
    data:
      - sentences:
          - "(pull up|open|show [me]) {query} [on] [kodi]"
          - "kodi (pull up|open|show) {query}"
          - "(go to|navigate to) {query} [on] [kodi]"

lists:
  query:
    wildcard: true
'''


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kodi Voice Search from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    config = entry.data
    host = config[CONF_KODI_HOST]
    port = config[CONF_KODI_PORT]
    username = config[CONF_KODI_USERNAME]
    password = config[CONF_KODI_PASSWORD]
    window_id = config.get(CONF_WINDOW_ID, DEFAULT_WINDOW_ID)

    # Get optional pipeline ID for multi-Kodi routing
    pipeline_id = config.get(CONF_PIPELINE_ID)

    # Store config for later use
    hass.data[DOMAIN][entry.entry_id] = {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "window_id": window_id,
        "pipeline_id": pipeline_id,
    }

    # Install custom sentences for voice commands
    await _install_custom_sentences(hass)

    async def async_search(call: ServiceCall) -> None:
        """Handle the search service call."""
        query = call.data[ATTR_QUERY]
        await _execute_search(hass, entry.entry_id, query)

    async def async_pull_up(call: ServiceCall) -> None:
        """Handle the pull up service call."""
        query = call.data[ATTR_QUERY]
        media_type = call.data.get(ATTR_MEDIA_TYPE, "all")
        await _execute_pull_up(hass, entry.entry_id, query, media_type)

    # Register services
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH,
        async_search,
        schema=SEARCH_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PULL_UP,
        async_pull_up,
        schema=PULL_UP_SCHEMA,
    )

    # Register intent handlers for voice commands
    intent.async_register(hass, KodiSearchIntentHandler())
    intent.async_register(hass, KodiPullUpIntentHandler())

    _LOGGER.info("Kodi Voice Search integration loaded for %s:%s", host, port)

    return True


async def _install_custom_sentences(hass: HomeAssistant) -> bool:
    """Install custom sentences file to Home Assistant config directory."""
    try:
        # Get the Home Assistant config directory
        config_dir = Path(hass.config.config_dir)
        sentences_dir = config_dir / "custom_sentences" / "en"
        sentences_file = sentences_dir / "kodi_voice_search.yaml"

        # Create directory if it doesn't exist
        sentences_dir.mkdir(parents=True, exist_ok=True)

        # Check if file already exists and has correct content
        if sentences_file.exists():
            existing_content = sentences_file.read_text()
            if existing_content.strip() == CUSTOM_SENTENCES.strip():
                _LOGGER.debug("Custom sentences file already up to date")
                return True

        # Write the sentences file
        sentences_file.write_text(CUSTOM_SENTENCES)
        _LOGGER.info("Installed custom sentences to %s", sentences_file)

        # Note: User may need to restart HA or reload intents for changes to take effect
        _LOGGER.warning(
            "Custom sentences installed. You may need to restart Home Assistant "
            "for voice commands to work."
        )

        return True

    except Exception as err:
        _LOGGER.error("Failed to install custom sentences: %s", err)
        return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.services.async_remove(DOMAIN, SERVICE_SEARCH)
    hass.services.async_remove(DOMAIN, SERVICE_PULL_UP)
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


async def _kodi_request(config: dict, method: str, params: dict | None = None) -> dict | None:
    """Make a JSON-RPC request to Kodi."""
    url = f"http://{config['host']}:{config['port']}/jsonrpc"
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "id": 1,
    }
    if params:
        payload["params"] = params

    _LOGGER.debug("Kodi request: %s %s", method, params)
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
                _LOGGER.debug("Kodi response: %s", result)
                if "error" in result:
                    _LOGGER.error("Kodi error for %s: %s", method, result["error"])
                    return None
                return result.get("result")
    except Exception as err:
        _LOGGER.error("Kodi request failed for %s: %s", method, err)
        return None


async def _search_library(
    config: dict,
    query: str,
    media_type: str = "all"
) -> tuple[list[dict], list[dict]]:
    """Search Kodi library for TV shows and movies matching the query.

    Returns tuple of (tv_shows, movies).
    """
    tv_shows = []
    movies = []
    query_lower = query.lower()

    # Search TV shows
    if media_type in ("all", "tv"):
        result = await _kodi_request(
            config,
            "VideoLibrary.GetTVShows",
            {"properties": ["title", "year", "thumbnail"]}
        )
        if result and "tvshows" in result:
            for show in result["tvshows"]:
                if query_lower in show["title"].lower():
                    tv_shows.append(show)

    # Search movies
    if media_type in ("all", "movie"):
        result = await _kodi_request(
            config,
            "VideoLibrary.GetMovies",
            {"properties": ["title", "year", "thumbnail"]}
        )
        if result and "movies" in result:
            for movie in result["movies"]:
                if query_lower in movie["title"].lower():
                    movies.append(movie)

    return tv_shows, movies


async def _navigate_to_content(
    config: dict,
    content_type: str,
    content_id: int
) -> bool:
    """Navigate Kodi to a specific content page."""
    if content_type == "tvshow":
        # TV shows use standard GUI.ActivateWindow
        path = f"videodb://tvshows/titles/{content_id}/"
        _LOGGER.debug("Navigating to TV show: %s", path)

        result = await _kodi_request(
            config,
            "GUI.ActivateWindow",
            {"window": "videos", "parameters": [path]}
        )
        success = result is not None
        _LOGGER.debug("Navigation result: %s (success=%s)", result, success)
        return success

    elif content_type == "movie":
        # Movies use our addon to open Video info dialog (12003)
        # GUI.ActivateWindow doesn't accept numeric window IDs
        path = f"videodb://movies/{content_id}/"
        _LOGGER.debug("Opening movie info for movie ID %s with path: %s", content_id, path)

        result = await _kodi_request(
            config,
            "Addons.ExecuteAddon",
            {
                "addonid": KODI_ADDON_ID,
                "params": f"window=12003&path={path}"
            }
        )
        success = result is not None
        _LOGGER.debug("Addon result: %s (success=%s)", result, success)
        return success

    return False


async def _execute_pull_up(
    hass: HomeAssistant,
    entry_id: str,
    query: str,
    media_type: str = "all"
) -> tuple[bool, str]:
    """Execute pull up command on Kodi.

    Returns tuple of (success, message).
    """
    config = hass.data[DOMAIN][entry_id]

    _LOGGER.debug("Pulling up '%s' (type: %s)", query, media_type)

    # Search library
    tv_shows, movies = await _search_library(config, query, media_type)
    total_results = len(tv_shows) + len(movies)

    _LOGGER.debug("Found %d TV shows and %d movies", len(tv_shows), len(movies))

    if total_results == 0:
        # No results - report not found
        return False, f"I couldn't find {query} in your library"

    elif total_results == 1:
        # Exactly one result - navigate directly
        if tv_shows:
            show = tv_shows[0]
            _LOGGER.info("Found TV show: %s (id=%s)", show["title"], show["tvshowid"])
            success = await _navigate_to_content(config, "tvshow", show["tvshowid"])
            if success:
                return True, f"Opening {show['title']}"
            _LOGGER.error("Failed to navigate to TV show %s", show["title"])
            return False, f"Failed to open {show['title']}"
        else:
            movie = movies[0]
            _LOGGER.info("Found movie: %s (id=%s)", movie["title"], movie["movieid"])
            success = await _navigate_to_content(config, "movie", movie["movieid"])
            if success:
                return True, f"Opening {movie['title']}"
            _LOGGER.error("Failed to navigate to movie %s", movie["title"])
            return False, f"Failed to open {movie['title']}"

    else:
        # Multiple results - use search to show filtered results
        _LOGGER.debug("Multiple matches found, using search instead")
        success = await _execute_search(hass, entry_id, query)
        if success:
            return True, f"Found multiple matches for {query}"
        return False, "Failed to search"


class KodiSearchIntentHandler(intent.IntentHandler):
    """Handle Kodi search intents."""

    intent_type = "KodiSearch"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = intent_obj.slots

        query = slots.get("query", {}).get("value", "")

        # Get the conversation agent ID (pipeline) that triggered this intent
        conversation_agent_id = getattr(intent_obj, "conversation_agent_id", None)

        _LOGGER.info(
            "KodiSearch intent triggered with query: %s (agent_id: %s)",
            query,
            conversation_agent_id,
        )

        if not query:
            response = intent_obj.create_response()
            response.async_set_speech("I didn't catch what you wanted to search for.")
            return response

        # Find the right Kodi entry based on pipeline routing
        if DOMAIN in hass.data and hass.data[DOMAIN]:
            entry_id = self._find_kodi_entry(hass, conversation_agent_id)
            if entry_id:
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

    def _find_kodi_entry(self, hass: HomeAssistant, conversation_agent_id: str | None) -> str | None:
        """Find the appropriate Kodi entry based on conversation agent ID.

        Routing logic:
        1. If conversation_agent_id matches a configured pipeline_id, use that entry
        2. Otherwise, use an entry with no pipeline_id (default)
        3. If no default, use the first configured entry
        """
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            return None

        default_entry_id = None
        first_entry_id = None

        for entry_id, config in entries.items():
            if first_entry_id is None:
                first_entry_id = entry_id

            pipeline_id = config.get("pipeline_id")

            # Check if this entry matches the conversation agent
            if conversation_agent_id and pipeline_id == conversation_agent_id:
                _LOGGER.debug(
                    "Routing to Kodi entry %s (matched pipeline %s)",
                    entry_id,
                    pipeline_id,
                )
                return entry_id

            # Track entry without pipeline_id as default
            if not pipeline_id and default_entry_id is None:
                default_entry_id = entry_id

        # Use default entry if available
        if default_entry_id:
            _LOGGER.debug("Routing to default Kodi entry %s", default_entry_id)
            return default_entry_id

        # Fall back to first entry
        _LOGGER.debug("Routing to first Kodi entry %s", first_entry_id)
        return first_entry_id


class KodiPullUpIntentHandler(intent.IntentHandler):
    """Handle Kodi pull up intents."""

    intent_type = "KodiPullUp"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = intent_obj.slots

        query = slots.get("query", {}).get("value", "")

        # Get the conversation agent ID (pipeline) that triggered this intent
        conversation_agent_id = getattr(intent_obj, "conversation_agent_id", None)

        _LOGGER.info(
            "KodiPullUp intent triggered with query: %s (agent_id: %s)",
            query,
            conversation_agent_id,
        )

        if not query:
            response = intent_obj.create_response()
            response.async_set_speech("I didn't catch what you wanted to pull up.")
            return response

        # Find the right Kodi entry based on pipeline routing
        if DOMAIN in hass.data and hass.data[DOMAIN]:
            entry_id = self._find_kodi_entry(hass, conversation_agent_id)
            if entry_id:
                success, message = await _execute_pull_up(hass, entry_id, query)

                response = intent_obj.create_response()
                response.async_set_speech(message)
                return response

        response = intent_obj.create_response()
        response.async_set_speech("Kodi Voice Search is not configured")
        return response

    def _find_kodi_entry(self, hass: HomeAssistant, conversation_agent_id: str | None) -> str | None:
        """Find the appropriate Kodi entry based on conversation agent ID.

        Routing logic:
        1. If conversation_agent_id matches a configured pipeline_id, use that entry
        2. Otherwise, use an entry with no pipeline_id (default)
        3. If no default, use the first configured entry
        """
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            return None

        default_entry_id = None
        first_entry_id = None

        for entry_id, config in entries.items():
            if first_entry_id is None:
                first_entry_id = entry_id

            pipeline_id = config.get("pipeline_id")

            # Check if this entry matches the conversation agent
            if conversation_agent_id and pipeline_id == conversation_agent_id:
                _LOGGER.debug(
                    "Routing to Kodi entry %s (matched pipeline %s)",
                    entry_id,
                    pipeline_id,
                )
                return entry_id

            # Track entry without pipeline_id as default
            if not pipeline_id and default_entry_id is None:
                default_entry_id = entry_id

        # Use default entry if available
        if default_entry_id:
            _LOGGER.debug("Routing to default Kodi entry %s", default_entry_id)
            return default_entry_id

        # Fall back to first entry
        _LOGGER.debug("Routing to first Kodi entry %s", first_entry_id)
        return first_entry_id
