# Kodi Voice Search for Unfolded Circle Remote 3

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/dangerouslaser/kodi-voice-search.svg)](https://github.com/dangerouslaser/kodi-voice-search/releases)
[![License](https://img.shields.io/github/license/dangerouslaser/kodi-voice-search.svg)](LICENSE)

Voice-controlled search for Kodi using the **Unfolded Circle Remote 3** and Home Assistant. Designed specifically for custom skins like Arctic Fuse 2 that use non-standard window IDs not accessible via standard JSON-RPC.

> **Note:** This integration is designed for use with the [Unfolded Circle Remote 3](https://www.unfoldedcircle.com/) and its built-in microphone. While the underlying components may work with other Home Assistant voice setups, this guide and integration are specifically tested and documented for the UC Remote 3.

## Features

- Voice search using UC Remote 3's built-in microphone
- Native skin search integration (Arctic Fuse 2, etc.)
- Powered by Home Assistant Assist with Whisper STT
- Simple setup via HACS and config flow

## How It Works

```
UC Remote 3 Mic → Home Assistant Assist (Whisper STT) → Custom Intent → Kodi JSON-RPC → script.openwindow → Skin Search Window
```

When you press and hold the microphone button on your UC Remote 3 and speak a search query, the audio is sent to Home Assistant's Assist pipeline. Whisper transcribes your speech, matches it to a custom intent, and triggers a JSON-RPC call to Kodi via a small helper addon that can open custom skin windows.

Since Kodi's JSON-RPC API doesn't support opening custom skin windows directly (only built-in window names), this integration includes a small Kodi addon (`script.openwindow`) that bridges this gap.

## Requirements

- **Unfolded Circle Remote 3** (firmware 2.8.1+ for voice support)
- Home Assistant 2024.1.0 or newer
- Kodi with JSON-RPC enabled
- A compatible skin (tested with Arctic Fuse 2)
- Whisper STT and Assist Pipeline (see prerequisites below)

## Installation

### Part 1: Voice Assistant Prerequisites

Before installing this integration, you need a working voice assistant in Home Assistant. The integration will check for these prerequisites and guide you if they're missing.

#### Install Wyoming Whisper (Speech-to-Text)

1. Go to **Settings → Add-ons → Add-on Store**
2. Search for "Whisper" and install **Wyoming Whisper**
3. Start the add-on and wait for it to download the model
4. Go to **Settings → Integrations**
5. Click **Add Integration** and search for "Wyoming Protocol"
6. Enter the Whisper add-on address (usually `homeassistant.local:10300`)

#### Create an Assist Pipeline

1. Go to **Settings → Voice Assistants**
2. Click **Add Assistant**
3. Configure:
   - **Name**: "Kodi Voice" (or any name)
   - **Language**: English
   - **Speech-to-Text**: Select your Whisper instance
   - **Text-to-Speech**: Optional (for voice responses)
4. Click **Create**

### Part 2: Home Assistant Integration (HACS)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add this repository URL: `https://github.com/dangerouslaser/kodi-voice-search`
4. Select category: **Integration**
5. Click **Add**
6. Search for "Kodi Voice Search" and install it
7. Restart Home Assistant

### Part 3: Configure the Integration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "Kodi Voice Search"
3. The integration will check for voice assistant prerequisites
4. Enter your Kodi details:
   - **IP Address**: Your Kodi device IP
   - **Port**: 8080 (default)
   - **Username**: kodi (default)
   - **Password**: kodi (default)
   - **Window ID**: 11185 (for Arctic Fuse 2 search)

### Part 4: Kodi Addon Installation

The integration requires a small helper addon (`script.openwindow`) on your Kodi device.

#### Option A: Auto-Install via SSH (Recommended)

If the addon is not detected, the integration will offer to install it automatically:

1. Select **"Auto-install via SSH"** when prompted
2. Enter your SSH credentials:
   - **Username**: `root` (default for CoreELEC/LibreELEC)
   - **Password**: Leave empty if none is set
   - **Port**: `22` (default)
3. The integration will automatically:
   - Install the addon files via SSH
   - Restart Kodi
   - Wait for Kodi to come back online
   - Enable the addon
   - Verify everything is working

No manual steps required!

> **Note:** Auto-install works with CoreELEC and LibreELEC. For other Kodi installations, use manual installation.

#### Option B: Manual Installation

If auto-install doesn't work or you prefer manual installation, SSH into your Kodi device and run:

```bash
mkdir -p /storage/.kodi/addons/script.openwindow

cat > /storage/.kodi/addons/script.openwindow/addon.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<addon id="script.openwindow" name="Open Window" version="1.0.0" provider-name="kodi-voice-search">
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
</addon>
EOF

cat > /storage/.kodi/addons/script.openwindow/default.py << 'EOF'
import sys
import xbmc

window_id = None
search_term = None

all_params = '&'.join(sys.argv[1:]).split('&')

for arg in all_params:
    if '=' in arg:
        key, value = arg.split('=', 1)
        if key == 'window':
            window_id = value
        elif key == 'search':
            search_term = value

if search_term:
    xbmc.executebuiltin(f'SetProperty(CustomSearchTerm,{search_term},Home)')

if window_id:
    xbmc.executebuiltin(f'ActivateWindow({window_id})')
EOF
```

Then restart Kodi:

```bash
systemctl restart kodi
```

#### Option C: Copy Files

1. Download the `kodi_addon/script.openwindow` folder from this repository
2. Copy it to your Kodi addons directory:
   - CoreELEC/LibreELEC: `/storage/.kodi/addons/`
   - Windows: `%APPDATA%\Kodi\addons\`
   - Linux: `~/.kodi/addons/`
   - macOS: `~/Library/Application Support/Kodi/addons/`
3. Restart Kodi

## Usage

### Voice Commands

**Search** - Opens the skin's search window with your query:
- "Search Breaking Bad on Kodi"
- "Find Stranger Things"
- "Kodi search The Office"
- "Look for Game of Thrones on Kodi"

**Pull Up** - Navigates directly to a show or movie in your library:
- "Pull up Jeopardy"
- "Open The Office on Kodi"
- "Show me Breaking Bad"
- "Go to Stranger Things"

The "pull up" command is smart:
- **One match found** → Opens the show/movie page directly
- **Multiple matches** → Shows search results to choose from
- **No matches** → Reports "not found in library"

### Service Calls

You can also call the services directly:

```yaml
# Search
service: kodi_voice_search.search
data:
  query: "Breaking Bad"

# Pull Up (navigate to content)
service: kodi_voice_search.pull_up
data:
  query: "Jeopardy"
  media_type: "tv"  # Optional: all, tv, or movie
```

### Automation Example

```yaml
automation:
  - alias: "Kodi Voice Search"
    trigger:
      - platform: conversation
        command:
          - "(search|find) {query} [on kodi]"
    action:
      - service: kodi_voice_search.search
        data:
          query: "{{ trigger.slots.query }}"
```

## Finding Your Skin's Search Window ID

Different skins use different window IDs. To find yours:

1. Open the search window manually in Kodi
2. Run this command:

```bash
curl -X POST \
  -u kodi:kodi \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "GUI.GetProperties", "params": {"properties": ["currentwindow"]}, "id": 1}' \
  http://<KODI_IP>:8080/jsonrpc
```

3. Note the `id` value in the response
4. Use this ID in the integration configuration

### Known Window IDs

| Skin | Search Window ID |
|------|------------------|
| Arctic Fuse 2 | 11185 |
| Arctic Fuse 2 (Discover) | 11105 |

## Multiple Kodi Devices

If you have multiple Kodi devices in different rooms, you can configure each one to respond to a specific Voice Assistant pipeline.

### How It Works

1. **Create multiple Voice Assistant pipelines** in Home Assistant (Settings → Voice Assistants)
   - Example: "Living Room Voice", "Bedroom Voice"

2. **Add each Kodi device** as a separate integration instance
   - Go to Settings → Devices & Services → Add Integration → Kodi Voice Search
   - During setup, select which Voice Assistant pipeline should route to this Kodi

3. **Configure your UC Remote 3 activities** to use different pipelines
   - Each activity can be assigned a different Voice Assistant profile
   - Voice commands will route to the Kodi matched to that pipeline

### Routing Logic

- If a voice command comes from a pipeline that matches a configured Kodi, it routes there
- If no match is found, it routes to a Kodi with no pipeline assigned (default)
- If no default exists, it routes to the first configured Kodi

### Example Setup

| Kodi Device | Voice Assistant Pipeline |
|-------------|-------------------------|
| Living Room Kodi | Living Room Voice |
| Bedroom Kodi | Bedroom Voice |
| Media Server | None (default) |

### Upgrading from v1.x

Existing configurations are automatically migrated. Your current Kodi will become the default target (no pipeline assigned). You can reconfigure to assign a specific pipeline if needed.

## Troubleshooting

### "Addon not found" error

Restart Kodi to load the newly installed addon:

```bash
systemctl restart kodi
```

### Cannot connect to Kodi

1. Ensure Kodi has remote control enabled:
   - **Settings → Services → Control → Allow remote control via HTTP**
2. Check the port (default 8080)
3. Verify credentials (default kodi/kodi)

### Voice commands not recognized

1. Restart Home Assistant after installing the integration
2. Test STT is working in **Developer Tools → Actions → assist_pipeline.run**
3. If you want custom voice patterns, create your own `custom_sentences/en/kodi_search.yaml`

### Search window doesn't open

1. Verify the window ID is correct for your skin
2. Check Kodi logs for errors:
   ```bash
   tail -f /storage/.kodi/temp/kodi.log | grep openwindow
   ```

## Alternative: TMDbHelper Search

If you prefer TMDbHelper's search instead of native skin search, you can use this REST command instead:

```yaml
rest_command:
  kodi_tmdb_search:
    url: "http://<KODI_IP>:8080/jsonrpc"
    method: post
    username: "kodi"
    password: "kodi"
    headers:
      content-type: "application/json"
    payload: >
      {"jsonrpc": "2.0", "method": "Addons.ExecuteAddon", 
       "params": {"addonid": "plugin.video.themoviedb.helper", 
                  "params": {"info": "search", "tmdb_type": "multi", "query": "{{ text }}"}}, "id": 1}
```

## Part 5: Unfolded Circle Remote 3 Setup

This is the primary use case for this integration. The UC Remote 3's built-in microphone sends voice commands to Home Assistant.

### Prerequisites

- UC Remote 3 with firmware **2.8.1 or newer** (voice support was added in this version)
- Home Assistant integration configured on the remote

### Home Assistant Integration

1. Open UC Remote web configurator (`http://<remote-ip>`)
2. Go to **Integrations → Add Integration → Home Assistant**
3. Configure:
   - **WebSocket URL:** `ws://<HA_IP>:8123/api/websocket`
   - **Access Token:** Create a long-lived token in HA (Profile → Security → Create Token)

### Add Voice Assistant Entity

1. In UC configurator, go to the Home Assistant integration
2. Click **Add Entity**
3. Find and add: **Voice Assistant (assist)**
4. Select the voice pipeline you created earlier (e.g., "Kodi Voice")

### Configure Voice Button

1. Go to **Remotes & Docks → Remote → Buttons**
2. Find the microphone button
3. Assign it to the Voice Assistant entity
4. Set action to **Push and hold** (recommended for voice input)

### Testing

1. Press and hold the microphone button on your UC Remote 3
2. Say "Search Breaking Bad on Kodi"
3. Release the button
4. The Arctic Fuse 2 search window should open with your query

## Other Voice Devices

While this integration is designed and tested specifically for the Unfolded Circle Remote 3, the underlying Home Assistant Assist pipeline should theoretically work with other voice input methods such as:

- ESPHome voice satellites
- Home Assistant mobile app voice input
- Other Wyoming protocol compatible devices

However, these configurations are **not officially supported or tested**. If you get it working with another device, feel free to submit a PR to update the documentation!

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - see [LICENSE](LICENSE) file.

## Credits

- [Unfolded Circle Remote 3](https://www.unfoldedcircle.com/) - The amazing remote that makes this possible
- [Arctic Fuse 2](https://github.com/jurialmunkey/skin.arctic.fuse.2) by jurialmunkey
- [TMDbHelper](https://github.com/jurialmunkey/plugin.video.themoviedb.helper) by jurialmunkey
- [Wyoming Whisper](https://github.com/rhasspy/wyoming-whisper) by Rhasspy
