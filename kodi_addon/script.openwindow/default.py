"""
Open Window - Kodi Addon
Opens custom skin windows and sets properties via JSON-RPC.

Usage via JSON-RPC:
    Addons.ExecuteAddon with params:
    - window=<window_id>  : Opens the specified window
    - search=<query>      : Sets CustomSearchTerm property before opening window
    - property=<name>,<value> : Sets a custom property on Home window

Example:
    {"jsonrpc": "2.0", "method": "Addons.ExecuteAddon", 
     "params": {"addonid": "script.openwindow", 
                "params": "window=11185&search=Breaking Bad"}, "id": 1}
"""

import sys
import xbmc


def main():
    """Main entry point."""
    window_id = None
    search_term = None
    properties = []

    # Handle both space-separated args and &-separated params
    all_params = '&'.join(sys.argv[1:]).split('&')

    for arg in all_params:
        if '=' in arg:
            key, value = arg.split('=', 1)
            key = key.strip().lower()
            value = value.strip()
            
            if key == 'window':
                window_id = value
            elif key == 'search':
                search_term = value
            elif key == 'property':
                # Format: property=name,value
                if ',' in value:
                    prop_name, prop_value = value.split(',', 1)
                    properties.append((prop_name, prop_value))

    # Set search term property if provided
    if search_term:
        xbmc.executebuiltin(f'SetProperty(CustomSearchTerm,{search_term},Home)')
        xbmc.log(f'[script.openwindow] Set CustomSearchTerm: {search_term}', xbmc.LOGINFO)

    # Set any additional properties
    for prop_name, prop_value in properties:
        xbmc.executebuiltin(f'SetProperty({prop_name},{prop_value},Home)')
        xbmc.log(f'[script.openwindow] Set property {prop_name}: {prop_value}', xbmc.LOGINFO)

    # Activate window if provided
    if window_id:
        xbmc.executebuiltin(f'ActivateWindow({window_id})')
        xbmc.log(f'[script.openwindow] Activated window: {window_id}', xbmc.LOGINFO)


if __name__ == '__main__':
    main()
