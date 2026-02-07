"""Kodi Voice Search integration for Home Assistant."""
from __future__ import annotations

import logging
from pathlib import Path

import aiohttp
import asyncio
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    CONF_KODI_HOST,
    CONF_KODI_PORT,
    CONF_KODI_USERNAME,
    CONF_KODI_PASSWORD,
    CONF_PIPELINE_ID,
    CONF_SEARCH_METHOD,
    SERVICE_SEARCH,
    SERVICE_PULL_UP,
    ATTR_QUERY,
    ATTR_MEDIA_TYPE,
    KODI_ADDON_ID,
    DEFAULT_SEARCH_METHOD,
)

_LOGGER = logging.getLogger(__name__)

# Assist pipeline domain for accessing debug data
ASSIST_PIPELINE_DOMAIN = "assist_pipeline"


def _get_most_recent_pipeline_id(hass: HomeAssistant) -> str | None:
    """Get the pipeline_id of the most recent pipeline run from assist_pipeline debug data.

    This is a workaround because Home Assistant doesn't pass pipeline_id to intent handlers.
    We look at the assist_pipeline debug data to find the most recently started pipeline run.
    """
    try:
        # Access assist_pipeline data
        pipeline_data = hass.data.get(ASSIST_PIPELINE_DOMAIN)
        if not pipeline_data:
            _LOGGER.debug("No assist_pipeline data found")
            return None

        # Get pipeline_debug dict - it's indexed by pipeline_id, then run_id
        pipeline_debug = getattr(pipeline_data, "pipeline_debug", None)
        if not pipeline_debug:
            _LOGGER.debug("No pipeline_debug data found")
            return None

        most_recent_pipeline_id = None
        most_recent_timestamp = None

        # Iterate through all pipelines and their runs
        for pipeline_id, runs in pipeline_debug.items():
            for run_id, run_debug in runs.items():
                # Get the timestamp of this run
                timestamp = getattr(run_debug, "timestamp", None)
                if timestamp:
                    if most_recent_timestamp is None or timestamp > most_recent_timestamp:
                        most_recent_timestamp = timestamp
                        most_recent_pipeline_id = pipeline_id

        if most_recent_pipeline_id:
            _LOGGER.debug(
                "Found most recent pipeline: %s (timestamp: %s)",
                most_recent_pipeline_id,
                most_recent_timestamp
            )
        else:
            _LOGGER.debug("No pipeline runs found in debug data")

        return most_recent_pipeline_id

    except Exception as err:
        _LOGGER.debug("Error accessing pipeline debug data: %s", err)
        return None


def _find_kodi_entry(hass: HomeAssistant, conversation_agent_id: str | None) -> str | None:
    """Find the appropriate Kodi entry based on pipeline routing.

    Routing logic:
    1. Try to get the actual pipeline_id from assist_pipeline debug data
    2. If pipeline_id matches a configured entry, use that entry
    3. Otherwise, use an entry with no pipeline_id (default)
    4. If no default, use the first configured entry
    """
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        return None

    # Get the actual pipeline_id from assist_pipeline debug data
    actual_pipeline_id = _get_most_recent_pipeline_id(hass)
    _LOGGER.debug("Actual pipeline_id from debug data: %s", actual_pipeline_id)

    default_entry_id = None
    first_entry_id = None

    for entry_id, config in entries.items():
        if first_entry_id is None:
            first_entry_id = entry_id

        configured_pipeline_id = config.get("pipeline_id")
        _LOGGER.debug(
            "Entry %s: host=%s, pipeline_id=%s (looking for: %s)",
            entry_id, config.get("host"), configured_pipeline_id, actual_pipeline_id
        )

        # Check if this entry matches the actual pipeline
        if actual_pipeline_id and configured_pipeline_id == actual_pipeline_id:
            _LOGGER.debug(
                "Routing to Kodi entry %s (matched pipeline %s)",
                entry_id,
                configured_pipeline_id,
            )
            return entry_id

        # Track entry without pipeline_id as default
        if not configured_pipeline_id and default_entry_id is None:
            default_entry_id = entry_id

    # Use default entry if available
    if default_entry_id:
        _LOGGER.debug("Routing to default Kodi entry %s", default_entry_id)
        return default_entry_id

    # Fall back to first entry
    _LOGGER.debug("Routing to first Kodi entry %s", first_entry_id)
    return first_entry_id


def _log_intent_debug(intent_obj: intent.Intent, intent_name: str) -> None:
    """Log debug information about an intent for troubleshooting pipeline routing."""
    _LOGGER.debug("=== %s Intent Debug Info ===", intent_name)
    _LOGGER.debug("conversation_agent_id: %s", getattr(intent_obj, "conversation_agent_id", None))
    _LOGGER.debug("assistant: %s", getattr(intent_obj, "assistant", None))
    _LOGGER.debug("device_id: %s", getattr(intent_obj, "device_id", None))
    _LOGGER.debug("satellite_id: %s", getattr(intent_obj, "satellite_id", None))
    _LOGGER.debug("platform: %s", getattr(intent_obj, "platform", None))
    _LOGGER.debug("language: %s", getattr(intent_obj, "language", None))
    _LOGGER.debug("intent_type: %s", getattr(intent_obj, "intent_type", None))
    if hasattr(intent_obj, 'context') and intent_obj.context:
        _LOGGER.debug("context.user_id: %s", getattr(intent_obj.context, "user_id", None))
        _LOGGER.debug("context.parent_id: %s", getattr(intent_obj.context, "parent_id", None))
    _LOGGER.debug("=" * (len(intent_name) + 26))


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

    if config_entry.version < 3:
        # Version 2 -> 3: Add search_method field (default = skin_specific)
        new_data = {**config_entry.data}
        new_data[CONF_SEARCH_METHOD] = DEFAULT_SEARCH_METHOD

        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            version=3,
        )
        _LOGGER.info(
            "Migrated Kodi Voice Search config entry to version 3. "
            "Search method set to 'skin_specific' (default). "
            "Reconfigure to change search method if needed."
        )

    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kodi Voice Search from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Register update listener to reload when options change
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    config = entry.data
    host = config[CONF_KODI_HOST]
    port = config[CONF_KODI_PORT]
    username = config[CONF_KODI_USERNAME]
    password = config[CONF_KODI_PASSWORD]

    # Get optional pipeline ID for multi-Kodi routing
    pipeline_id = config.get(CONF_PIPELINE_ID)

    # Get search method preference
    search_method = config.get(CONF_SEARCH_METHOD, DEFAULT_SEARCH_METHOD)

    # Store config for later use
    hass.data[DOMAIN][entry.entry_id] = {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "pipeline_id": pipeline_id,
        "search_method": search_method,
    }

    # Install custom sentences for voice commands
    await _install_custom_sentences(hass)

    # Register services only once (first entry)
    if not hass.services.has_service(DOMAIN, SERVICE_SEARCH):
        async def async_search(call: ServiceCall) -> None:
            """Handle the search service call."""
            query = call.data[ATTR_QUERY]
            entry_id = _find_kodi_entry(hass, None)
            if entry_id:
                await _execute_search(hass, entry_id, query)

        async def async_pull_up(call: ServiceCall) -> None:
            """Handle the pull up service call."""
            query = call.data[ATTR_QUERY]
            media_type = call.data.get(ATTR_MEDIA_TYPE, "all")
            entry_id = _find_kodi_entry(hass, None)
            if entry_id:
                await _execute_pull_up(hass, entry_id, query, media_type)

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


def _install_custom_sentences_sync(config_dir: str, content: str) -> bool:
    """Install custom sentences file (sync, runs in executor)."""
    try:
        sentences_dir = Path(config_dir) / "custom_sentences" / "en"
        sentences_file = sentences_dir / "kodi_voice_search.yaml"

        sentences_dir.mkdir(parents=True, exist_ok=True)

        if sentences_file.exists():
            existing_content = sentences_file.read_text()
            if existing_content.strip() == content.strip():
                _LOGGER.debug("Custom sentences file already up to date")
                return True

        sentences_file.write_text(content)
        _LOGGER.info("Installed custom sentences to %s", sentences_file)
        _LOGGER.warning(
            "Custom sentences installed. You may need to restart Home Assistant "
            "for voice commands to work."
        )
        return True

    except Exception as err:
        _LOGGER.error("Failed to install custom sentences: %s", err)
        return False


async def _install_custom_sentences(hass: HomeAssistant) -> bool:
    """Install custom sentences file to Home Assistant config directory."""
    bundled_file = Path(__file__).parent / "sentences" / "en" / "kodi_voice_search.yaml"
    try:
        content = await hass.async_add_executor_job(bundled_file.read_text)
    except Exception as err:
        _LOGGER.error("Failed to read bundled sentences file: %s", err)
        return False

    return await hass.async_add_executor_job(
        _install_custom_sentences_sync, hass.config.config_dir, content
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].pop(entry.entry_id)
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_SEARCH)
        hass.services.async_remove(DOMAIN, SERVICE_PULL_UP)
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update - reload the integration."""
    _LOGGER.info("Options updated for %s, reloading integration", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)


async def _execute_search(hass: HomeAssistant, entry_id: str, query: str) -> bool:
    """Execute search on Kodi using the configured search method."""
    config = hass.data[DOMAIN][entry_id]
    search_method = config.get("search_method", DEFAULT_SEARCH_METHOD)

    _LOGGER.debug("Executing search on Kodi %s:%s (method: %s)", config['host'], config['port'], search_method)

    # Build addon params based on search method
    # Use ||| delimiter to avoid & breaking params when query contains &
    addon_params = f"search={query}|||method={search_method}"

    result = await _kodi_request(
        hass,
        config,
        "Addons.ExecuteAddon",
        {"addonid": KODI_ADDON_ID, "params": addon_params},
    )
    if result is not None:
        _LOGGER.debug("Kodi search executed: %s (method: %s)", query, search_method)
        return True
    return False


async def _kodi_request(hass: HomeAssistant, config: dict, method: str, params: dict | None = None) -> dict | None:
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
    session = async_get_clientsession(hass)

    try:
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
    hass: HomeAssistant,
    config: dict,
    query: str,
    media_type: str = "all"
) -> tuple[list[dict], list[dict]]:
    """Search Kodi library for TV shows and movies matching the query.

    Uses server-side filtering via Kodi's JSON-RPC filter API.
    Returns tuple of (tv_shows, movies).
    """
    title_filter = {"filter": {"field": "title", "operator": "contains", "value": query}}
    tv_shows = []
    movies = []

    # Search TV shows
    if media_type in ("all", "tv"):
        result = await _kodi_request(
            hass,
            config,
            "VideoLibrary.GetTVShows",
            {"properties": ["title", "year", "thumbnail"], **title_filter},
        )
        if result and "tvshows" in result:
            tv_shows = result["tvshows"]

    # Search movies
    if media_type in ("all", "movie"):
        result = await _kodi_request(
            hass,
            config,
            "VideoLibrary.GetMovies",
            {"properties": ["title", "year", "thumbnail"], **title_filter},
        )
        if result and "movies" in result:
            movies = result["movies"]

    return tv_shows, movies


async def _navigate_to_content(
    hass: HomeAssistant,
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
            hass,
            config,
            "GUI.ActivateWindow",
            {"window": "videos", "parameters": [path]}
        )
        success = result is not None
        _LOGGER.debug("Navigation result: %s (success=%s)", result, success)
        return success

    elif content_type == "movie":
        # Movies don't have a navigable detail page like TV shows
        # Return False to trigger search fallback which works reliably
        _LOGGER.debug("Movie navigation not supported, using search fallback")
        return False

    else:
        _LOGGER.warning("Unknown content type: %s", content_type)
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
    tv_shows, movies = await _search_library(hass, config, query, media_type)
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
            success = await _navigate_to_content(hass, config, "tvshow", show["tvshowid"])
            if success:
                return True, f"Opening {show['title']}"
            _LOGGER.error("Failed to navigate to TV show %s", show["title"])
            return False, f"Failed to open {show['title']}"
        else:
            # Movies use search view (no direct detail page navigation)
            movie = movies[0]
            _LOGGER.info("Found movie: %s (id=%s), using search", movie["title"], movie["movieid"])
            success = await _execute_search(hass, entry_id, movie["title"])
            if success:
                return True, f"Showing {movie['title']}"
            return False, f"Failed to show {movie['title']}"

    else:
        # Multiple results - use search to show filtered results
        _LOGGER.debug("Multiple matches found, using search instead")
        success = await _execute_search(hass, entry_id, query)
        if success:
            return True, f"Found multiple matches for {query}"
        return False, "Failed to search"


class _KodiIntentHandlerBase(intent.IntentHandler):
    """Base class for Kodi intent handlers with shared routing logic."""

    _empty_query_message: str = "I didn't catch what you said."

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        query = intent_obj.slots.get("query", {}).get("value", "")
        conversation_agent_id = getattr(intent_obj, "conversation_agent_id", None)

        _log_intent_debug(intent_obj, self.intent_type)
        _LOGGER.info(
            "%s intent triggered with query: %s (agent_id: %s)",
            self.intent_type, query, conversation_agent_id,
        )

        if not query:
            response = intent_obj.create_response()
            response.async_set_speech(self._empty_query_message)
            return response

        if DOMAIN in hass.data and hass.data[DOMAIN]:
            entry_id = _find_kodi_entry(hass, conversation_agent_id)
            if entry_id:
                response = intent_obj.create_response()
                response.async_set_speech(await self._execute(hass, entry_id, query))
                return response

        response = intent_obj.create_response()
        response.async_set_speech("Kodi Voice Search is not configured")
        return response

    async def _execute(self, hass: HomeAssistant, entry_id: str, query: str) -> str:
        """Execute the intent action. Returns speech text."""
        raise NotImplementedError


class KodiSearchIntentHandler(_KodiIntentHandlerBase):
    """Handle Kodi search intents."""

    intent_type = "KodiSearch"
    _empty_query_message = "I didn't catch what you wanted to search for."

    async def _execute(self, hass: HomeAssistant, entry_id: str, query: str) -> str:
        success = await _execute_search(hass, entry_id, query)
        if success:
            return f"Searching for {query} on Kodi"
        return "Sorry, I couldn't connect to Kodi"


class KodiPullUpIntentHandler(_KodiIntentHandlerBase):
    """Handle Kodi pull up intents."""

    intent_type = "KodiPullUp"
    _empty_query_message = "I didn't catch what you wanted to pull up."

    async def _execute(self, hass: HomeAssistant, entry_id: str, query: str) -> str:
        _success, message = await _execute_pull_up(hass, entry_id, query)
        return message
