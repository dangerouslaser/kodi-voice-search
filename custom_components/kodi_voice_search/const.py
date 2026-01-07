"""Constants for Kodi Voice Search integration."""

DOMAIN = "kodi_voice_search"

CONF_KODI_HOST = "kodi_host"
CONF_KODI_PORT = "kodi_port"
CONF_KODI_USERNAME = "kodi_username"
CONF_KODI_PASSWORD = "kodi_password"
CONF_WINDOW_ID = "window_id"
CONF_PIPELINE_ID = "pipeline_id"
CONF_SEARCH_METHOD = "search_method"

# Search method options
SEARCH_METHOD_SKIN = "skin_specific"
SEARCH_METHOD_DEFAULT = "default"
SEARCH_METHOD_GLOBAL = "global_search"

# SSH configuration for auto-install
CONF_SSH_USERNAME = "ssh_username"
CONF_SSH_PASSWORD = "ssh_password"
CONF_SSH_PORT = "ssh_port"

DEFAULT_PORT = 8080
DEFAULT_USERNAME = "kodi"
DEFAULT_PASSWORD = "kodi"
DEFAULT_WINDOW_ID = "11185"
DEFAULT_SSH_PORT = 22
DEFAULT_SSH_USERNAME = "root"
DEFAULT_SEARCH_METHOD = SEARCH_METHOD_SKIN

SERVICE_SEARCH = "search"
SERVICE_PULL_UP = "pull_up"
ATTR_QUERY = "query"
ATTR_MEDIA_TYPE = "media_type"

# Arctic Fuse 2 search window
AF2_SEARCH_WINDOW = "11185"
AF2_DISCOVER_WINDOW = "11105"

KODI_ADDON_ID = "script.openwindow"
KODI_ADDON_PATH = "/storage/.kodi/addons/script.openwindow"
KODI_ADDON_VERSION = "2.0.0"

# Global Search addon
GLOBAL_SEARCH_ADDON_ID = "script.globalsearch"

# Addon file contents
ADDON_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<addon id="script.openwindow" name="Open Window" version="2.0.0" provider-name="kodi-voice-search">
  <requires>
    <import addon="xbmc.python" version="3.0.0"/>
  </requires>
  <extension point="xbmc.python.script" library="default.py"/>
  <extension point="xbmc.addon.metadata">
    <summary lang="en">Smart search helper for Kodi skins</summary>
    <description lang="en">A skin-aware helper addon that handles search with proper focus management. Supports Arctic Fuse 2, Estuary, and other skins via JSON-RPC.</description>
    <license>MIT</license>
    <platform>all</platform>
  </extension>
</addon>'''

ADDON_PY = '''"""
Open Window - Smart Kodi Search Addon
Handles search with skin-aware focus management.

Supports multiple search methods:
- skin_specific: Uses skin-specific window and focus handling
- default: Uses Kodi's built-in search
- global_search: Uses script.globalsearch addon

Usage via JSON-RPC:
    Addons.ExecuteAddon with params:
    - search=<query>         : The search term
    - method=<method>        : Search method (skin_specific, default, global_search)
    - window=<window_id>     : Optional window override (for skin_specific)
    - focus=<control_id>     : Optional focus control override
    - focusdelay=<ms>        : Optional delay before focus (default 1500)
"""

import sys
import xbmc
import xbmcgui

# Skin configurations - add more skins as needed
SKIN_CONFIGS = {
    "skin.arctic.fuse.2": {
        "search_window": "11185",
        "search_property": "CustomSearchTerm",
        "results_control": 5000,
        "alt_results_control": 5001,
        "wait_for_ready": "Window.IsVisible(11185)",
        "wait_for_search": 1500,  # AF2 AlarmClock delay
    },
    "skin.estuary": {
        "search_window": "10140",
        "search_property": None,  # Estuary uses different mechanism
        "results_control": None,
        "wait_for_search": 500,
    },
    "_default": {
        "search_window": None,
        "search_property": None,
        "results_control": None,
        "wait_for_search": 500,
    }
}


def get_skin_config():
    """Get config for current skin, or default."""
    skin = xbmc.getSkinDir()
    xbmc.log(f'[script.openwindow] Detected skin: {skin}', xbmc.LOGINFO)
    return SKIN_CONFIGS.get(skin, SKIN_CONFIGS["_default"])


def wait_for_condition(condition, timeout_ms=5000, poll_ms=100):
    """Wait for a Kodi visibility condition to be true."""
    elapsed = 0
    while elapsed < timeout_ms:
        if xbmc.getCondVisibility(condition):
            return True
        xbmc.sleep(poll_ms)
        elapsed += poll_ms
    return False


def set_focus_with_retry(control_id, alt_control_id=None, retries=3):
    """Try to set focus, with fallback to alternate control."""
    for attempt in range(retries):
        xbmc.executebuiltin(f'SetFocus({control_id})')
        xbmc.sleep(100)
        if xbmc.getCondVisibility(f'Control.HasFocus({control_id})'):
            xbmc.log(f'[script.openwindow] Focus set to {control_id} on attempt {attempt + 1}', xbmc.LOGINFO)
            return True

    if alt_control_id:
        xbmc.executebuiltin(f'SetFocus({alt_control_id})')
        xbmc.sleep(100)
        if xbmc.getCondVisibility(f'Control.HasFocus({alt_control_id})'):
            xbmc.log(f'[script.openwindow] Focus set to alt control {alt_control_id}', xbmc.LOGINFO)
            return True

    xbmc.log(f'[script.openwindow] Failed to set focus to {control_id}', xbmc.LOGWARNING)
    return False


def execute_skin_search(search_term, window_override=None, focus_override=None, focus_delay=None):
    """Execute search using skin-specific logic."""
    config = get_skin_config()

    # Use overrides if provided
    window_id = window_override or config.get("search_window")
    focus_control = focus_override or config.get("results_control")
    delay = focus_delay if focus_delay is not None else config.get("wait_for_search", 1500)

    # Set search property
    search_property = config.get("search_property")
    if search_property:
        xbmc.executebuiltin(f'SetProperty({search_property},{search_term},Home)')
        xbmc.log(f'[script.openwindow] Set {search_property}: {search_term}', xbmc.LOGINFO)

    # Open search window
    if window_id:
        xbmc.executebuiltin(f'ActivateWindow({window_id})')
        xbmc.log(f'[script.openwindow] Activated window: {window_id}', xbmc.LOGINFO)

        # Wait for window to be ready
        wait_condition = config.get("wait_for_ready")
        if wait_condition:
            if wait_for_condition(wait_condition, timeout_ms=3000):
                xbmc.log(f'[script.openwindow] Window ready', xbmc.LOGINFO)
            else:
                xbmc.log(f'[script.openwindow] Timeout waiting for window', xbmc.LOGWARNING)

    # Wait for skin to process search (e.g., AF2's AlarmClock)
    if delay > 0:
        xbmc.sleep(delay)

    # Focus results container
    if focus_control:
        alt_control = config.get("alt_results_control")
        set_focus_with_retry(focus_control, alt_control)


def execute_default_search(search_term):
    """Execute search using Kodi's built-in search."""
    # Use VideoLibrary search via GUI
    xbmc.executebuiltin(f'ActivateWindow(videos,videodb://movies/titles/?search={search_term})')
    xbmc.log(f'[script.openwindow] Default search: {search_term}', xbmc.LOGINFO)


def execute_global_search(search_term):
    """Execute search using script.globalsearch addon."""
    xbmc.executebuiltin(f'RunScript(script.globalsearch,searchstring={search_term})')
    xbmc.log(f'[script.openwindow] Global search: {search_term}', xbmc.LOGINFO)


def main():
    """Main entry point."""
    search_term = None
    method = "skin_specific"  # Default method
    window_override = None
    focus_override = None
    focus_delay = None

    # Handle both space-separated args and &-separated params
    all_params = '&'.join(sys.argv[1:]).split('&')

    for arg in all_params:
        if '=' in arg:
            key, value = arg.split('=', 1)
            key = key.strip().lower()
            value = value.strip()

            if key == 'search':
                search_term = value
            elif key == 'method':
                method = value
            elif key == 'window':
                window_override = value
            elif key == 'focus':
                try:
                    focus_override = int(value)
                except ValueError:
                    pass
            elif key == 'focusdelay':
                try:
                    focus_delay = int(value)
                except ValueError:
                    pass

    if not search_term:
        xbmc.log('[script.openwindow] No search term provided', xbmc.LOGWARNING)
        return

    xbmc.log(f'[script.openwindow] Search: "{search_term}" method: {method}', xbmc.LOGINFO)

    if method == "global_search":
        execute_global_search(search_term)
    elif method == "default":
        execute_default_search(search_term)
    else:  # skin_specific
        execute_skin_search(search_term, window_override, focus_override, focus_delay)


if __name__ == '__main__':
    main()
'''
