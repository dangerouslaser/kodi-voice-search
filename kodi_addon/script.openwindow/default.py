"""
Open Window - Smart Kodi Search Addon v2.1.0
Handles search with skin-aware focus management.

For Arctic Fuse 2: Uses script.skinvariables to directly set search text
and control focus, bypassing the CustomSearchTerm -> AlarmClock -> keyboard
refocus race condition.

Supports multiple search methods:
- skin_specific: Uses skin-specific window and focus handling
- default: Uses Kodi's built-in search
- global_search: Uses script.globalsearch addon

Usage via JSON-RPC:
    Addons.ExecuteAddon with params:
    - search=<query>         : The search term
    - method=<method>        : Search method (skin_specific, default, global_search)
"""

import sys
import xbmc
import xbmcgui

# Skin configurations
SKIN_CONFIGS = {
    "skin.arctic.fuse.2": {
        "search_window": "11185",
        "use_skinvariables": True,  # Use direct script.skinvariables call
        "edit_control": 9099,       # Text input control
        "results_control": 5001,    # First widget row (movies)
    },
    "skin.estuary": {
        "search_window": "10140",
        "use_skinvariables": False,
        "search_property": None,
        "results_control": None,
    },
    "_default": {
        "search_window": None,
        "use_skinvariables": False,
        "search_property": None,
        "results_control": None,
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


def execute_af2_search(search_term):
    """Execute search for Arctic Fuse 2 using AF2's native flow.

    Uses CustomSearchTerm to trigger AF2's AlarmClock which populates
    the search containers via script.skinvariables. We wait for all
    widgets to load before moving focus to results.
    """
    xbmc.log(f'[script.openwindow] AF2 search starting: {search_term}', xbmc.LOGINFO)

    # Step 1: Set CustomSearchTerm - this triggers AF2's normal search flow
    xbmc.executebuiltin(f'SetProperty(CustomSearchTerm,{search_term},Home)')

    # Step 2: Open search window (let AF2 handle initial focus)
    xbmc.executebuiltin('ActivateWindow(11185)')

    # Step 3: Wait for window to be visible
    if not wait_for_condition('Window.IsVisible(11185)', timeout_ms=3000):
        xbmc.log('[script.openwindow] Timeout waiting for window 11185', xbmc.LOGWARNING)
        return False

    # Step 4: Wait for AF2's AlarmClock to fire and skinvariables to populate containers
    # AF2's AlarmClock fires after 1 second, then containers need time to update
    xbmc.sleep(1500)

    # Step 5: Trigger widget refresh to ensure all rows load
    xbmc.executebuiltin('SetProperty(UpdateSearchRows,True,Home)')

    # Step 6: Wait for BOTH movie (5001) and TV show (5002) widgets to have results
    max_wait = 5000
    elapsed = 0
    while elapsed < max_wait:
        movies_ready = xbmc.getCondVisibility('!Integer.IsEqual(Container(5001).NumItems,0)')
        tvshows_ready = xbmc.getCondVisibility('!Integer.IsEqual(Container(5002).NumItems,0)')
        # Also check if containers are done updating
        not_updating = xbmc.getCondVisibility('!Container(5001).IsUpdating + !Container(5002).IsUpdating')

        if (movies_ready or tvshows_ready) and not_updating:
            xbmc.log(f'[script.openwindow] Results loaded after {elapsed}ms (movies={movies_ready}, tv={tvshows_ready})', xbmc.LOGINFO)
            break
        xbmc.sleep(200)
        elapsed += 200

    # Step 7: Clear UpdateSearchRows
    xbmc.executebuiltin('ClearProperty(UpdateSearchRows,Home)')

    # Step 8: NOW move focus to results (after everything is loaded)
    xbmc.executebuiltin('SetFocus(5001)')

    xbmc.log('[script.openwindow] AF2 search complete', xbmc.LOGINFO)
    return True


def execute_skin_search(search_term):
    """Execute search using skin-specific logic."""
    config = get_skin_config()
    skin = xbmc.getSkinDir()

    # Arctic Fuse 2 gets special handling via script.skinvariables
    if skin == "skin.arctic.fuse.2" and config.get("use_skinvariables"):
        return execute_af2_search(search_term)

    # Fallback for other skins - use window activation
    window_id = config.get("search_window")
    if window_id:
        xbmc.executebuiltin(f'ActivateWindow({window_id})')
        xbmc.log(f'[script.openwindow] Activated window: {window_id}', xbmc.LOGINFO)


def execute_default_search(search_term):
    """Execute search using Kodi's built-in search."""
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

    if not search_term:
        xbmc.log('[script.openwindow] No search term provided', xbmc.LOGWARNING)
        return

    xbmc.log(f'[script.openwindow] Search: "{search_term}" method: {method}', xbmc.LOGINFO)

    if method == "global_search":
        execute_global_search(search_term)
    elif method == "default":
        execute_default_search(search_term)
    else:  # skin_specific
        execute_skin_search(search_term)


if __name__ == '__main__':
    main()
