# chatxz

Decentralized instant messaging powered by the [Reticulum Network Stack](https://reticulum.network/).

Send text, emoji, files and folders of any size, images/screenshots (viewable inline), and voice notes — all encrypted by default, no servers needed.

## Features

- **End-to-end encrypted** messaging via Reticulum
- **Unlimited file sizes** — send any file over RNS Resources with progress bar + MB/s
- **Folder upload** — send entire directories as compressed zip
- **Screenshot preview** — images display inline, click to enlarge, save button
- **Voice notes** — record from browser mic (MediaRecorder API) and send
- **Emoji picker** — full Unicode emoji support
- **Drag & drop** — drag files onto the page to upload
- **Clipboard paste** — paste screenshots from clipboard
- **Message queue** — messages queued when offline, auto-drained on connection
- **Delivery receipts** — sent/delivered/read indicators on messages
- **Chat history** — persisted locally, configurable retention (1d / 1w / 1m / 6m / 12m / never / on restart / on close), clear anytime
- **Contacts** — save peers with names, right-click sidebar panel
- **LAN discovery** — auto-discovers peers broadcasting on the same network
- **Manual announce only** — you decide when to broadcast your presence
- **CPU temperature** — live system temp shown in sidebar (Arch, Ubuntu, Debian)
- **Connection status** — colored indicators for WebSocket and link state
- **Change identity** — regenerate your keypair (old key is deleted)
- **Restart server** — restart from the GUI
- **Configurable received files directory** — choose where incoming files are saved
- **Off-grid capable** — works over LoRa, packet radio, WiFi, or the internet
- **Android APK** — standalone app with embedded Python, download from Releases
- **No accounts, no servers** — your identity is your key

## Platform Support

| Platform | Status |
|----------|--------|
| Arch Linux | Supported |
| Ubuntu | Supported |
| Debian | Supported |
| Android (APK) | Supported |
| macOS | Planned |
| Windows | Planned |

## Quick Start (Linux)

### Clone & Install

```bash
git clone https://github.com/narl3yyy-svg/chatzx.git
cd chatzx

# Arch Linux
bash scripts/install-arch.sh

# Ubuntu / Debian
bash scripts/install-debian.sh

# Or install manually
pip install rns aiohttp
pip install .
```

### Run

```bash
# Web UI (development mode, verbose logging)
./run.sh web --share --verbose

# Web UI (production)
chatzx-web --share

# CLI mode
./run.sh cli
# or
chatzx
```

The web interface runs at **http://localhost:8742** (or your LAN IP when using `--share`).

## Web Interface Guide

### Connecting to a Peer

1. **Share your identity hash** — shown at the top of the sidebar and on server startup (e.g. `ab12cd34...`). Give this to your friend.
2. **Get their hash** — they share theirs with you.
3. **Click "+ Add"** in the sidebar, paste their hash, optionally save as a contact.
4. **Click the contact** to connect. A green "Link: active" indicator appears.
5. **Announce** — click "Announce" to broadcast your presence on the LAN. Others will appear under "Discovered on LAN".
6. **Right-click** any contact or discovered peer to open the action panel (Connect, Save Contact, Delete).

### Sending Files

- **Single file** — click the 📎 button or drag & drop a file onto the page
- **Multiple files** — same, select multiple in the file picker
- **Folder** — click the 📁 button and select a folder (all files inside are compressed and sent as zip)
- **Progress** — a progress bar shows transfer percentage, file size, and MB/s

### Delivery Receipts

Messages show status indicators:
- 🕐 Sending
- ✓ Sent
- ✓✓ Delivered (peer received it)
- 👁 Read (peer viewed it)

### Settings

Click ⚙ in the sidebar header:

| Setting | Description |
|---------|-------------|
| Display Name | Shown in LAN announces |
| History Retention | Auto-delete by time, on restart, or on browser close |
| Clear History Now | Immediately delete all chat history |
| Save Received Files To | Directory where incoming files are saved |
| Regenerate Identity | Create a new keypair (old key is deleted) |
| Restart Server | Restart the web server from the GUI |

### Sidebar Indicators

- **WS dot** — WebSocket connection to the server (green = connected, orange = connecting, red = disconnected)
- **Link dot** — Reticulum link to peer (green = active, gray = inactive)
- **🌡** — Live CPU temperature (updates every 5 seconds)
- **Peers** — Discovered LAN peers + connected peer count

## Android APK

Download the latest `chatxz.apk` from [Releases](https://github.com/narl3yyy-svg/chatzx/releases).

The APK bundles Python 3.13, RNS, aiohttp, and the chatxz web server. On launch it starts the server and opens a WebView — works offline, no Termux needed. CPU architecture: arm64-v8a (most modern Android phones).

### Build from Source

```bash
cd android
./gradlew assembleDebug
# Output: android/app/build/outputs/apk/debug/app-debug.apk
```

Requires: Android SDK 34, JDK 17, Gradle 8.x.

## CLI Usage

```bash
# Interactive mode
chatzx

# Send a one-off message
chatzx --connect <peer_hash> --send "Hello!"

# Send a file
chatzx --connect <peer_hash> --file screenshot.png

# Record and send voice
chatzx --connect <peer_hash> --voice

# Listen daemon
chatzx --daemon
```

### Interactive Commands

```
/connect <hash>   - Connect to a peer
/send <text>      - Send a message
/file <path>      - Send a file
/voice            - Record and send voice
/play <path>      - Play a voice note
/contacts         - List contacts
/add <hash:name>  - Add a contact
/myid             - Show your identity
/help             - Show help
/quit             - Exit
```

## Architecture

chatxz uses [Reticulum](https://reticulum.network/) (RNS) for all networking:

- **Identities** — Ed25519/X25519 key pairs, stored locally in `~/.config/chatxz/identities/identity`
- **Destinations** — announced on the network as `chatxz.messages`
- **Links** — encrypted bi-directional channels between peers
- **Resources** — reliable transfer of arbitrary-size data (files, images, voice)

All data is encrypted end-to-end by Reticulum. No plaintext is ever sent.

### Data Flow

1. **Web UI** connects to the local server via WebSocket at `/ws`
2. **Server** manages Reticulum identity, links, and message routing
3. **Peer discovery** is manual — click "Announce" to broadcast on LAN
4. **File transfer** uses RNS Resources (reliable, with progress) + direct HTTP (bonus)
5. **Delivery receipts** are sent automatically when messages are received and read
6. **History** is persisted to `~/.config/chatxz/history.json`
7. **Settings** stored in `~/.config/chatxz/settings.json`
8. **Queue** stores offline messages in `~/.config/chatxz/queue.json`

## Directory Structure

```
~/.config/chatxz/
  config              # RNS configuration
  settings.json       # User settings (name, history_retention, received_dir)
  history.json        # Chat message history
  queue.json          # Queued offline messages
  identities/
    identity          # Your Reticulum keypair
  contacts/           # Saved contacts (filename = hash, content = name)
  received/           # Default received files directory
  sent/               # All sent files (permanently saved)
```

## Development

```bash
# Run with verbose RNS logging
./run.sh web --share --verbose

# Run.sh is in the repo root — it auto-installs deps and starts the server
```

## License

MIT
