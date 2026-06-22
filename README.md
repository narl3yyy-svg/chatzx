# chatxz

Decentralized peer-to-peer messaging over the [Reticulum Network Stack](https://reticulum.network/). No accounts, no central servers — your identity is a local cryptographic keypair.

Send text, emoji, files and folders of any size, inline images, and voice notes. Everything is encrypted end-to-end by Reticulum over **RNS Links and Resources only** (no HTTP file relay between peers).

## What It Does

chatxz is a self-hosted chat application with a modern web UI and a CLI. You run a local server that speaks Reticulum on your network (WiFi, LAN, packet radio, LoRa, or the internet). Peers connect directly using identity hashes — no signup, no cloud relay.

The web interface handles day-to-day use: per-peer chats, send messages, transfer large files with live progress, manage contacts, and configure storage. The CLI is for scripting, headless use, and quick one-off sends.

**HTTP (port 8742) is only the local web UI** — it serves the browser interface and previews files saved on your machine. Peer-to-peer chat and file transfer always travel over encrypted RNS (UDP 4242).

## Features

### Messaging
- **End-to-end encrypted** text, emoji, and system messages via Reticulum Links
- **Per-peer chats** — each contact has its own conversation thread and history
- **Clear left/right alignment** — your messages on the right, received on the left
- **Delivery receipts** — sending, sent, delivered, read indicators
- **Copy button** — hover any text/emoji message to copy
- **Delete individual messages** — hover and click 🗑 to remove a message from history
- **Chat history** — persisted locally per peer with configurable retention
- **Offline queue** — messages queued when peer is disconnected, sent on reconnect
- **Long text** — messages exceeding link MTU are sent as RNS file resources

### Files & Media
- **Unlimited file sizes** — all transfers use RNS Resources (segmented, encrypted)
- **Real-time transfer speed** — live MB/s in the bottom dock during send/receive
- **Cancel transfers** — stops the RNS resource and clears the progress bar
- **Folder upload** — entire directories compressed to zip and sent
- **Drag & drop** and **clipboard paste** for files and screenshots
- **Inline image preview** on both sender and receiver (click to enlarge)
- **Inline video playback** — mp4, webm, mkv, mov, and other common formats play in chat
- **Voice notes** — record from browser mic and send
- **Configurable received-files directory** — custom save location with native folder picker

### Network & Peers
- **LAN discovery** — auto-discovers peers via RNS announces + UDP beacon (8743)
- **Manual announce** — broadcast presence when you choose
- **Contacts** — save peers with display names; click to open their chat
- **Incoming connections** — when a peer connects to you, the UI updates automatically
- **Connection status** — WebSocket and Reticulum link indicators in the bottom dock

### Platforms
| Platform | Status |
|----------|--------|
| Arch Linux | Supported |
| Ubuntu / Debian | Supported |
| Android (APK) | Supported (WebView + embedded Python) |

## Quick Start (Linux)

```bash
git clone https://github.com/narl3yyy-svg/chatzx.git
cd chatxz
bash scripts/install-arch.sh   # or scripts/install-debian.sh
./run.sh web --share --verbose
```

Open **http://localhost:8742** (or your LAN IP with `--share`).

## Web Interface Guide

### Per-peer chats

1. Click a **contact** or **discovered peer** in the sidebar to open their chat.
2. History for that peer loads automatically.
3. If not connected yet, the app connects in the background (or use **Connect** panel).
4. **Your messages** appear on the **right**; **received** messages on the **left**.
5. When a peer connects **to you** (incoming link), the chat opens automatically.

### Connecting

1. Share your **identity hash** from the sidebar (click to copy).
2. Peers must **Announce** (📡) so RNS learns their identity.
3. Click a discovered peer or paste a hash in **Connect**.
4. Green **Link: Active** in the bottom dock when the encrypted RNS link is up.

### Sending files

- **📎** attach files, **📁** send folders (zipped), drag & drop, or paste screenshots
- Progress bar shows filename, %, size, and speed
- **Cancel** stops the RNS transfer

### Settings

| Setting | Description |
|---------|-------------|
| Display Name | Shown in LAN announces |
| History Retention | Auto-delete by time or on restart/close |
| Save Received Files To | Incoming file directory |
| Regenerate Identity | New keypair (peers must reconnect) |
| Restart Server | Restart from the GUI |

## Debugging & Logging

Use these flags when diagnosing issues:

```bash
# Normal — RNS notice-level logs
./run.sh web --share

# Verbose — RNS debug (shows per-segment resource prep for large files)
./run.sh web --share --verbose

# Debug — extreme RNS logging + chatxz send/recv trace lines
./run.sh web --share --debug
```

**Additional visibility:**

1. Edit `~/.config/chatxz/config` and set `loglevel = 7` under `[logging]` for maximum RNS detail (extreme).
2. Check `http://localhost:8742/api/network-status` — RNS interfaces, link state, discovered peers.
3. Check `http://localhost:8742/api/debug` — beacon counters, active peer, message count.
4. Browser devtools console shows WebSocket events (`[ws] Message type: ...`).

**Firewall (Linux desktop):** allow UDP **4242** (RNS chat) and **8743** (discovery beacon):

```bash
sudo ufw allow 4242/udp
sudo ufw allow 8743/udp
```

HTTP **8742** is only needed for the local web UI on each machine.

## Architecture

```
Web UI (browser)  ←WebSocket/HTTP→  Local Server (aiohttp, UI only)
                                         ↓
                                   MessagingBackend
                                         ↓
                              Reticulum (RNS) — encrypted links + resources
                                         ↓
                                    Remote Peer
```

### File Transfer

All peer-to-peer files (images, folders, voice, large zips) use **RNS Resources** over the encrypted link. Received files are saved locally and served back to the browser via `/api/file/...` for preview only.

### Data Storage

```
~/.config/chatxz/
  config              # RNS configuration
  settings.json       # Display name, retention, received_dir
  history.json        # Chat messages (per-peer via chat_peer field)
  queue.json          # Offline message queue
  identities/identity # Ed25519/X25519 keypair
  contacts/           # Saved contacts
  received/           # Default incoming files
  sent/               # Copies of sent files
```

## Android APK

Download from [Releases](https://github.com/narl3yyy-svg/chatzx/releases). Push a `v*` tag to trigger the GitHub Actions APK build.

```bash
bash scripts/sync-android.sh
cd android && ./gradlew assembleDebug
```

## CLI Usage

```bash
chatxz --connect <hash> --send "Hi"
chatxz --connect <hash> --file x.png
chatxz --daemon
```

## Development

```bash
./run.sh web --share --verbose   # RNS debug
./run.sh web --share --debug     # RNS extreme + chat trace
```

## Changelog (recent)

### v0.3.21
- Inline video player in chat (mp4, webm, mkv, mov, avi, etc.)
- Older video messages sent as `file` still play inline via extension detection

### v0.3.20
- Per-peer messenger UI with correct sent/received alignment (`outgoing` flag)
- Incoming RNS links now update the web UI automatically (fixes Ubuntu→Arch)
- Image preview on receiver via fixed file URLs and custom `received_dir`
- Transfer cancel actually stops RNS resources; progress bar throttling
- Pure RNS file transfer docs (removed HTTP LAN relay references)
- `--debug` flag for maximum runtime visibility

### v0.3.19
- Message filtering and session dedup fixes

## License

MIT