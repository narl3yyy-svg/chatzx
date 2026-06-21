# chatxz

Decentralized peer-to-peer messaging over the [Reticulum Network Stack](https://reticulum.network/). No accounts, no central servers — your identity is a local cryptographic keypair.

Send text, emoji, files and folders of any size, inline images, and voice notes. Everything is encrypted end-to-end by Reticulum.

## What It Does

chatxz is a self-hosted chat application with a modern web UI and a CLI. You run a local server that speaks Reticulum on your network (WiFi, LAN, packet radio, LoRa, or the internet). Peers connect directly using identity hashes — no signup, no cloud relay.

The web interface handles day-to-day use: connect to friends, send messages, transfer large files with live progress, manage contacts, and configure storage. The CLI is for scripting, headless use, and quick one-off sends.

## Features

### Messaging
- **End-to-end encrypted** text, emoji, and system messages via Reticulum Links
- **Delivery receipts** — sending, sent, delivered, read indicators
- **Copy button** — hover any text/emoji message to copy
- **Delete individual messages** — hover and click 🗑 to remove a message from history
- **Chat history** — persisted locally with configurable retention (1d / 1w / 1m / 6m / 12m / never / on restart / on close)
- **Clear all history** — one-click wipe from Settings
- **Offline queue** — messages queued when peer is disconnected, sent on reconnect
- **Long text** — messages exceeding link MTU are sent as file resources and reconstructed on receive

### Files & Media
- **Unlimited file sizes** — large files use direct HTTP transfer (fast, resumable progress); RNS Resources as fallback
- **Real-time transfer speed** — live MB/s shown in the bottom dock during send and receive
- **Cancel transfers** — stop an in-progress send or receive from the bottom dock
- **Folder upload** — entire directories compressed to zip and sent
- **Drag & drop** and **clipboard paste** for files and screenshots
- **Inline image preview** — click to enlarge, save button
- **Voice notes** — record from browser mic (MediaRecorder) and send
- **Configurable received-files directory** — choose save location with native folder picker (Linux)

### Network & Peers
- **LAN discovery** — auto-discovers peers broadcasting on the same network
- **Manual announce** — you choose when to broadcast presence
- **Contacts** — save peers with display names; right-click sidebar for actions
- **Connection status** — WebSocket and Reticulum link indicators in the bottom dock

### System & Settings
- **Modern dark UI** — glass panels, bottom status dock, card-based settings
- **Average CPU temperature** — single reading averaged across all cores (hwmon → thermal zones → sensors)
- **CPU usage** — live percentage in the bottom dock
- **Regenerate identity** — create a new keypair from Settings
- **Restart server** — restart from the GUI
- **Off-grid capable** — works over LoRa, packet radio, WiFi, or the internet

### Platforms
| Platform | Status |
|----------|--------|
| Arch Linux | Supported |
| Ubuntu / Debian | Supported |
| Android (APK) | Supported (WebView + embedded Python) |
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
# Web UI (development, verbose RNS logging)
./run.sh web --share --verbose

# Web UI (production, LAN accessible)
chatzx-web --share

# CLI
./run.sh cli
# or
chatzx
```

Open **http://localhost:8742** (or your LAN IP with `--share`).

## Web Interface Guide

### Connecting to a Peer

1. Share your **identity hash** from the sidebar (click to copy).
2. Enter your friend's hash in the Connect panel.
3. Click a contact or discovered peer to connect — green **Link: active** when ready.
4. Click **Announce** to broadcast on LAN; discovered peers appear in the sidebar.
5. Right-click any contact for Connect, Save Contact, or Delete.

### Sending Files

- **Single / multiple files** — 📎 button, or drag & drop onto the page
- **Folder** — 📁 button (compressed to zip)
- **Progress** — bottom dock shows filename, percentage, size, and **live transfer speed**
- **Cancel** — Cancel button appears during active transfers

### Message Actions

- **Copy** — hover a text/emoji message, click 📋
- **Delete** — hover any message, click 🗑 (removes from local history)
- **Receipts** — 🕐 sending · ✓ sent · ✓✓ delivered · 👁 read

### Settings (⚙ in sidebar header)

| Setting | Description |
|---------|-------------|
| Display Name | Shown in LAN announces |
| History Retention | Auto-delete by time or on restart/close |
| Clear History Now | Delete all messages immediately |
| Save Received Files To | Incoming file directory (with browse button on Linux) |
| Regenerate Identity | New keypair (old key deleted, no backup) |
| Restart Server | Restart from the GUI |

### Bottom Dock

| Indicator | Meaning |
|-----------|---------|
| 🌡 | Average CPU temperature across cores |
| ⚡ | CPU usage % |
| WS dot | WebSocket to local server (green = connected) |
| Link dot | Reticulum link to peer (green = active) |
| Center bar | File transfer progress + live speed |
| Cancel | Stop active transfer |

## CLI Usage

```bash
chatzx                              # Interactive mode
chatzx --connect <hash> --send "Hi" # One-off message
chatzx --connect <hash> --file x.png
chatzx --connect <hash> --voice     # Record and send
chatzx --daemon                     # Listen only
```

Interactive commands: `/connect`, `/send`, `/file`, `/voice`, `/play`, `/contacts`, `/add`, `/myid`, `/help`, `/quit`

## Architecture

```
Web UI (browser)  ←WebSocket/HTTP→  Local Server (aiohttp)
                                         ↓
                                   MessagingBackend
                                         ↓
                              Reticulum (RNS) — encrypted links
                                         ↓
                                    Remote Peer
```

### File Transfer Strategy

1. **Direct HTTP** (primary for files/images) — sender offers a token over RNS; receiver downloads via `http://peer:port/api/direct-transfer/{token}`. Handles large files without RNS segment timeouts.
2. **RNS Resources** (fallback) — reliable transfer for smaller payloads and when direct HTTP is unavailable.
3. Progress and speed are broadcast over WebSocket to all connected browser tabs.

### Data Storage

```
~/.config/chatxz/
  config              # RNS configuration (auto-generated)
  settings.json       # Display name, retention, received_dir
  history.json        # Chat messages (with msg_id for delete)
  queue.json          # Offline message queue
  identities/identity # Ed25519/X25519 keypair
  contacts/           # Saved contacts (hash → name)
  received/           # Default incoming files
  sent/               # Copies of sent files
```

## Android APK

Download `chatxz.apk` from [Releases](https://github.com/narl3yyy-svg/chatzx/releases).

The APK bundles Python 3.13, RNS, aiohttp, and the chatxz web UI in a WebView. Built automatically on version tags via GitHub Actions.

```bash
cd android && ./gradlew assembleDebug
# Output: android/app/build/outputs/apk/debug/app-debug.apk
```

Requires Android SDK 34, JDK 17, Gradle 8.x. CPU: arm64-v8a.

## Development

```bash
./run.sh web --share --verbose   # Verbose RNS logging
```

### Project Layout

```
chatxz/
  app.py              # CLI entry point
  core/
    messaging.py      # RNS links, file transfer, queue
    identity.py       # Keypair management
    discovery.py      # LAN peer discovery
    voice.py          # Voice record/playback
  web/
    server.py         # aiohttp server + API + WebSocket
    static/index.html # Single-file web UI
  utils/
    helpers.py        # Paths, format_size, format_speed
    system.py         # CPU temp & usage metrics
```

## License

MIT