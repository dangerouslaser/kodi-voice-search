"""Constants for Kodi Voice Search integration."""

DOMAIN = "kodi_voice_search"

CONF_KODI_HOST = "kodi_host"
CONF_KODI_PORT = "kodi_port"
CONF_KODI_USERNAME = "kodi_username"
CONF_KODI_PASSWORD = "kodi_password"
CONF_WINDOW_ID = "window_id"
CONF_PIPELINE_ID = "pipeline_id"

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

SERVICE_SEARCH = "search"
SERVICE_PULL_UP = "pull_up"
ATTR_QUERY = "query"
ATTR_MEDIA_TYPE = "media_type"

# Arctic Fuse 2 search window
AF2_SEARCH_WINDOW = "11185"
AF2_DISCOVER_WINDOW = "11105"

KODI_ADDON_ID = "script.openwindow"
KODI_ADDON_PATH = "/storage/.kodi/addons/script.openwindow"

# Addon file contents
ADDON_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<addon id="script.openwindow" name="Open Window" version="1.1.0" provider-name="kodi-voice-search">
  <requires>
    <import addon="xbmc.python" version="3.0.0"/>
  </requires>
  <extension point="xbmc.python.script" library="default.py"/>
  <extension point="xbmc.addon.metadata">
    <summary lang="en">Open custom skin windows via JSON-RPC</summary>
    <description lang="en">A helper addon that allows opening custom skin windows and setting properties via JSON-RPC.</description>
    <license>MIT</license>
    <platform>all</platform>
  </extension>
</addon>'''

ADDON_PY = '''import sys
import xbmc

window_id = None
search_term = None
path = None

all_params = '&'.join(sys.argv[1:]).split('&')

for arg in all_params:
    if '=' in arg:
        key, value = arg.split('=', 1)
        if key == 'window':
            window_id = value
        elif key == 'search':
            search_term = value
        elif key == 'path':
            path = value

if search_term:
    xbmc.executebuiltin(f'SetProperty(CustomSearchTerm,{search_term},Home)')

if window_id:
    if path:
        xbmc.executebuiltin(f'ActivateWindow({window_id},{path})')
    else:
        xbmc.executebuiltin(f'ActivateWindow({window_id})')
'''
