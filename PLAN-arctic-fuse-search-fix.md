# Plan: Smart Kodi Addon + Arctic Fuse 2 Search Fix

## Problem
When voice search is triggered, the keyboard panel (Control 9992) stays focused instead of showing search results directly. Users have to manually navigate away from the keyboard.

## Root Cause
- Our addon sets `CustomSearchTerm` and opens window 11185
- AF2's AlarmClock reads the property after 1 second and populates Control 9099 (text input)
- But Control 9992 (keyboard) retains default focus
- Previous attempts using navigation actions (`left`, `down`, `select`) failed because they're context-dependent

## Solution: Make the Kodi Addon Smarter + User Choice

Instead of HA orchestrating individual steps with timing guesses, make the addon skin-aware and handle the full search flow intelligently. Also give users the choice of search method.

### Search Method Options (User Configurable)

| Option | Description | Best For |
|--------|-------------|----------|
| **Skin-Specific** | Smart skin-aware search with focus management | Arctic Fuse 2, other supported skins |
| **Default** | Basic search using Kodi's built-in search | Unknown skins, simple setups |
| **Global Search** | Use `script.globalsearch` addon | Universal search across all content |

### Current (Dumb) Approach
```
HA: "open window 11185, set search=X, focus=5000, delay=1500ms"
Addon: blindly executes each instruction
```

### New (Smart) Approach
```
HA: "search X" (with method preference from config)
Addon:
  1. Check search method preference
  2. If skin-specific:
     - Detect skin (xbmc.getSkinDir())
     - Look up skin-specific config
     - Handle focus management
  3. If default:
     - Use Kodi's built-in search
  4. If global_search:
     - Call script.globalsearch with query
  5. Wait for window ready
  6. Focus results (if applicable)
```

## Benefits of Smart Addon
- **Synchronous execution** - `xbmc.sleep()` and condition checks are reliable
- **Skin detection** - `xbmc.getSkinDir()` returns skin name, addon adapts
- **Proper waits** - `xbmc.getCondVisibility()` checks if UI is ready
- **Retry logic** - Can try alternate controls if first fails
- **Single command** - HA just sends search query, addon handles complexity
- **Less network chatter** - One JSON-RPC call instead of multiple

## Implementation Steps

### Step 0: Add Search Method Config to HA Integration
Add to `config_flow.py`:
```python
CONF_SEARCH_METHOD = "search_method"
SEARCH_METHOD_SKIN = "skin_specific"
SEARCH_METHOD_DEFAULT = "default"
SEARCH_METHOD_GLOBAL = "global_search"

# In config flow schema:
vol.Required(CONF_SEARCH_METHOD, default=SEARCH_METHOD_SKIN): vol.In({
    SEARCH_METHOD_SKIN: "Skin-Specific (Arctic Fuse 2, etc.)",
    SEARCH_METHOD_DEFAULT: "Default Kodi Search",
    SEARCH_METHOD_GLOBAL: "Global Search Addon",
}),
```

### Step 1: Design Skin Config Structure
```python
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
        "search_window": "10140",  # Example - needs research
        "search_property": None,   # May use different mechanism
    },
    "_default": {
        "search_window": None,  # Will use default Kodi search
        "search_property": None,
    }
}

# Global Search config (separate from skin configs)
GLOBAL_SEARCH_CONFIG = {
    "addon_id": "script.globalsearch",
    "method": "Addons.ExecuteAddon",
}
```

### Step 2: Rewrite Addon with Smart Logic
```python
import xbmc
import xbmcgui
import sys

# Skin configurations
SKIN_CONFIGS = { ... }

def get_skin_config():
    """Get config for current skin, or default."""
    skin = xbmc.getSkinDir()
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
    for _ in range(retries):
        xbmc.executebuiltin(f'SetFocus({control_id})')
        xbmc.sleep(100)
        if xbmc.getCondVisibility(f'Control.HasFocus({control_id})'):
            return True
    if alt_control_id:
        xbmc.executebuiltin(f'SetFocus({alt_control_id})')
        return xbmc.getCondVisibility(f'Control.HasFocus({alt_control_id})')
    return False

def execute_search(search_term):
    """Execute search with skin-aware logic."""
    config = get_skin_config()

    # Set search property
    if config.get("search_property"):
        xbmc.executebuiltin(f'SetProperty({config["search_property"]},{search_term},Home)')

    # Open search window
    window_id = config.get("search_window")
    if window_id:
        xbmc.executebuiltin(f'ActivateWindow({window_id})')

    # Wait for window to be ready
    wait_condition = config.get("wait_for_ready")
    if wait_condition:
        wait_for_condition(wait_condition)

    # Wait for skin to process search (e.g., AF2's AlarmClock)
    search_delay = config.get("wait_for_search", 1500)
    xbmc.sleep(search_delay)

    # Focus results container
    results_control = config.get("results_control")
    alt_control = config.get("alt_results_control")
    if results_control:
        set_focus_with_retry(results_control, alt_control)

def main():
    # Parse params
    search_term = None
    for arg in sys.argv[1:]:
        if '=' in arg:
            key, value = arg.split('=', 1)
            if key == 'search':
                search_term = value

    if search_term:
        execute_search(search_term)

if __name__ == '__main__':
    main()
```

### Step 3: Simplify HA Integration
Update `_execute_search()` to just pass the search query:
```python
payload = {
    "jsonrpc": "2.0",
    "method": "Addons.ExecuteAddon",
    "params": {
        "addonid": KODI_ADDON_ID,
        "params": f"search={query}"
    },
    "id": 1
}
```

### Step 4: Update Addon Version
- Bump `KODI_ADDON_VERSION` to `2.0.0` (major change)
- Update `ADDON_XML` version and description

### Step 5: Update Actual Addon File
- Sync `kodi_addon/script.openwindow/default.py` with ADDON_PY changes

## Files to Modify

| File | Changes |
|------|---------|
| `const.py` | Complete rewrite of ADDON_PY with smart logic, skin configs |
| `__init__.py` | Simplify `_execute_search()` to just pass search query |
| `kodi_addon/script.openwindow/default.py` | Same as ADDON_PY |

## Skin Research Needed
- Arctic Fuse 2: Already know window 11185, controls 5000/5001/9099/9992
- Estuary (default): Need to research search window and controls
- Other popular skins: Add configs as needed

## Testing
1. Update addon on Kodi (via reconfigure flow)
2. Test with Arctic Fuse 2:
   - Voice: "Search Breaking Bad"
   - Verify: Results show, keyboard not focused
3. Test with different skin (if available):
   - Verify: Fallback behavior works or skin-specific config works

## Future Enhancements
- Add more skin configs over time
- Report success/failure back to HA (via JSON file or property)
- Support for "pull up" command with smart navigation
- Configuration UI in HA to customize skin settings
