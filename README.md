# chatxz

Decentralized peer-to-peer messaging over the [Reticulum Network Stack](https://reticulum.network/). No accounts, no central servers — your identity is a local cryptographic keypair.

Send text, emoji, files and folders of any size, inline images, and voice notes. Everything is encrypted end-to-end by Reticulum over **RNS Links and Resources only** (no HTTP file relay between peers).

## What It Does

chatxz is a self-hosted chat application with a modern web UI and a CLI. You run a local server that speaks Reticulum on your network (WiFi, LAN, packet radio, LoRa, or the internet). Peers connect directly using identity hashes — no signup, no cloud relay.

The web interface handles day-to-day use: per-peer chats, send messages, transfer large files with live progress, manage contacts, and configure storage. The CLI is for scripting, headless use, and quick one-off sends.

**HTTP (port 8742) is only the local web UI** — it serves the browser interface and previews files saved on your machine. Peer-to-peer chat and file transfer always travel over encrypted RNS links.

### Security model

| Layer | Encrypted? | Notes |
|-------|------------|-------|
| RNS Link (chat) | **Yes** — AES-256-CBC + ECDH key exchange | All messages and link setup |
| RNS Resource (files) | **Yes** — over the encrypted link | Large files segmented inside the link |
| UDP / serial transport | No (payload is ciphertext) | Android uses directed LAN UDP unicast instead of broadcast; wire format is still RNS packets |
| HTTP `:8742` / beacon `:8743` | No | Local UI + discovery helpers only; never carries chat content between peers |

Reinstalling the Android app generates a **new identity** — saved contacts pointing at the old hash will not connect until you rediscover the peer and update the contact.

| Path | RNS? | Role |
|------|------|------|
| **AutoInterface** | Yes | LAN mesh over IPv6 link-local (desktop default) |
| **UDPInterface** (4242) | Yes | RNS UDP on your LAN subnet |
| **SerialInterface** | Yes | RNS over USB serial |
| **HTTP** `/api/request_connect` | No | Wake peer to open inbound RNS link (LAN/Android helper) |
| **UDP beacon** (8743) | No | Discovery helper only |

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
- **Dual-path failover** — USB serial + LAN (UDP / AutoInterface) run together; chat auto-reconnects on the other path when one drops (no server restart)
- **Serial hot-add** — plug USB serial mid-session on desktop; watchdog adds it to RNS when the port appears
- **Fast connect** — outgoing links succeed or fail within ~6–12s (16s during failover)
- **Reset network** — Settings → Network clears discovered peers, disconnects links, and zeros beacon counters (identity unchanged)
- **Weekly auto-reset** — beacon/discovery counters optionally reset after 7 days (toggle in Settings)
- **Contacts** — save peers with display names and LAN IP; tap/click to message (no manual Announce required when IP is saved)
- **Unread badges** — per-device unread counts on each saved contact
- **Notifications** — browser and Android notifications for messages when you are not viewing that chat
- **Incoming connections** — link status updates on all tabs; chat does not auto-open on other devices
- **Connection status** — WebSocket and Reticulum link indicators; Network panel shows active RNS path (`AutoInterfacePeer`, `SerialInterface`, `UDPInterface`)

### Platforms
| Platform | Status |
|----------|--------|
| Arch Linux | Supported |
| Ubuntu / Debian | Supported |
| Windows 11 | Supported (portable zip — no installer) |
| Android (APK) | Supported (WebView + embedded Python) |

## Quick Start (Windows 11 — portable)

1. Download **`chatxz-X.Y.Z-windows-portable.zip`** from [Releases](https://github.com/narl3yyy-svg/chatxz/releases).
2. Unzip to any folder (e.g. `C:\Users\You\chatxz`).
3. Double-click **`chatxz.exe`** inside the unzipped folder.
4. Your browser opens to **http://127.0.0.1:8742**. Allow Windows Firewall on private networks if prompted.

All data (identity, chats, received files) stays in **`chatxz-data\`** next to the exe — move or back up the whole folder to relocate.

To build the zip yourself on Windows 11:

```powershell
git clone https://github.com/narl3yyy-svg/chatxz.git
cd chatxz
powershell -ExecutionPolicy Bypass -File packaging\windows\build-portable.ps1
```

Output: `dist\chatxz-0.3.51-windows-portable.zip` — unzip it, then double-click `chatxz\chatxz.exe`.

**Automated GitHub releases:** replace `.github/workflows/build-apk.yml` with the contents of `packaging/windows/github-workflow-build-releases.yml` in the GitHub web editor (requires `workflow` scope to push from CI bots).

## Quick Start (Linux)

```bash
git clone https://github.com/narl3yyy-svg/chatxz.git
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
5. When a peer connects **to you**, the link indicator turns green; your current chat stays open (no cross-device auto-open).

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
2. Check **Settings → Network → Refresh status** or `GET /api/network-status` — RNS interfaces, `link_rns_interface`, `serial_in_rns`, `session_peer`, discovered peers.
3. During failover, watch server logs for `[connect] Failover triggered` and `[connect] Path ready on …`.
4. Reset discovery/beacon counters: `POST /api/network/reset` or **Settings → Network → Reset network**.
5. Check `GET /api/debug` — beacon counters, active peer, message count.
6. Browser devtools console shows WebSocket events (`[ws] Message type: ...`).

### Serial + LAN failover

1. **Settings → Network** — add **UDP LAN** (default) and **Serial**; pick your port (`/dev/ttyUSB0` on desktop, `/dev/bus/usb/...` on Android OTG), baud **57600**, click **Apply**.
2. Confirm **Serial in RNS: yes** on **both** ends. Android logs should show `[serial] Hot-added RNS SerialInterface` (not `UsbConstants` errors).
3. **Serial-only** (no WiFi): connect USB-TTL adapters back-to-back; chatxz skips HTTP wake and uses RNS over serial automatically when LAN is down.
4. Tap **Announce** once on each side after plugging serial so RNS learns the peer over the serial interface.
3. Connect to peer over LAN; unplug Ethernet or USB to test — logs should show `[connect] Failover triggered` without clicking Connect again.
4. **VPN disconnect ≠ LAN dead** if your physical NIC (`enp2s0`, `wlan0`) still has `10.10.x` — AutoInterface keeps working on the physical LAN.
5. Use `./run.sh web --share` on Arch so the process has `dialout` group access for serial.

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

Download from [Releases](https://github.com/narl3yyy-svg/chatxz/releases). Push a `v*` tag to trigger the GitHub Actions APK build.

The APK embeds the same Python tree as desktop (`chatxz/`) via Chaquopy. **Always sync before building:**

```bash
# 1. Bump version (updates version.properties, chatxz/_version.py, Gradle)
./scripts/bump-version.sh 0.3.39

# 2. Copy chatxz/ → android/app/src/main/python/chatxz/ (Python + web UI)
bash scripts/sync-android.sh

# 3. Build debug APK locally
cd android && chmod +x gradlew && ./gradlew assembleDebug
# Output: android/app/build/outputs/apk/debug/app-debug.apk
```

**Release APK via CI:** tag `v0.3.39` (or current `VERSION_NAME` in `version.properties`) — workflow runs `sync-android.sh` then `assembleDebug` and publishes to GitHub Releases.

**Android debug log (temporary):** each session writes `chatxz-debug-YYYYMMDD-HHMMSS.txt` to Phone Downloads (or app Downloads if scoped storage blocks public write). Path is shown in Settings → Advanced and Network status.

**Android notes:**
- Same failover, messaging, and web UI as desktop (WebView loads embedded `index.html`)
- USB serial via OTG: grant permission, pick port in Settings → Network, Apply — **hot-adds to RNS without restart** (v0.3.45+)
- Wi-Fi multicast lock held while running; directed UDP unicast to known peer IPs (link traffic stays encrypted inside RNS)
- Connect protocol: waking peer waits inbound; woken peer opens outbound RNS link (`POST /api/request_connect`)
- Session auto-resume when reopening the app UI if a chat session peer was active

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

### v0.3.51
- **Windows portable** — zip release with `chatxz.exe` (no installer); unzip and run; data in `chatxz-data/` beside the exe

### v0.3.50
- **Emoji picker** — stays open while selecting; closes on Send or click outside
- **Offline queue UI** — queued messages appear in chat immediately (⏳) and drain reliably with target peer
- **Android composer** — `+` opens camera; emoji/mic/files/folders in collapsible attach strip while typing
- **Android background** — foreground service keeps RNS alive; WebView no longer reloads on every app reopen
- **Notifications** — Python pushes Android notifications when not viewing that chat (incl. background/closed)
- **Mic access** — auto-retry recording after permission grant on Android
- **Sidebar** — hamburger closes Settings and shows contact list
- **Transfer speed** — skip compression on APK/zip/media; pause LAN announces during active transfers
- **Link stability** — stricter receipt-timeout failover (30s, 2+ pending) reduces false reconnects

### v0.3.49
- **File transfer fix** — remove broken receive-busy flag that rejected all incoming resources (v0.3.48 regression)
- **Transfer stability** — block link handoff and failover during active send/receive; migrate pending receives on path switch
- **Upload queue** — server queues file uploads while another transfer is in progress instead of failing silently
- **Reconnect** — clear orphaned pending receives before failover; longer failover cooldown to reduce reconnect spam

### v0.3.48
- **Serial connect** — quick outbound when serial RNS path is known; triple announce burst after USB hot-add
- **Faster reconnect** — parallel HTTP wake+announce, tighter connect timeouts (6s quick outbound)
- **Transfer stability** — ACCEPT_APP resource gating: one incoming file at a time (prevents overlapping resource ads)
- **Socket hygiene** — fix unclosed socket warnings on Android startup

### v0.3.47
- **Android serial fix** — complete usb4a `UsbConstants` shim (fixes hot-add `UsbConstants` error in logs)
- **Serial-only connect** — when LAN is down but USB serial is up, skip HTTP wake and prime serial RNS path
- **Faster connect** — shorter timeouts, quick outbound when path known, 2s HTTP wake timeout
- **Image zoom** — pinch-zoom on Android, wheel/drag/double-tap on desktop in chat image viewer
- **File transfers** — serialize sends (one at a time); auto-compress large non-media files

### v0.3.46
- **Messaging UX** — tap saved contact to connect and chat; unread badges per contact; notifications (Web + Android)
- **Multi-device** — link established no longer auto-opens chat on other browsers/devices; only updates connected status
- **Chat scroll** — history opens scrolled to bottom (no visible scroll-from-top)
- **Contacts** — JSON storage with saved LAN IP/port; connect uses stored IP without manual Announce
- **Android serial fix** — skip broken RNS Android SerialInterface in config; hot-add via usbserial4a pyserial patch
- **Serial watchdog** — runs on Android too; runtime serial attached after RNS start
- **Beacon rate** — Android periodic announce/beacon interval 45s (reduces excessive announce blocking)

### v0.3.45
- **Failover stability** — no reconnect during active file transfers; stale-link threshold 90s (was 8s); gentler path rebuild on reconnect
- **Session resume** — reopening WebView/UI auto-reconnects to saved session peer via wake protocol
- **Android serial** — hot-add on Apply (no app restart); prevents double RNS init crash on recreate
- **Temperature API** — no PermissionError spam on Android when hwmon is unavailable

### v0.3.44
- **Connect race fix** — initiator waits for peer outbound link (28s); responder outbounds immediately (no dual-outbound deadlock)
- **Directed UDP** — link packets sent to known peer IPs on all platforms; Android subnet scan only when no peers known

### v0.3.43
- **Android UDP EPERM fix** — unicast fan-out replaces blocked broadcast; `NEARBY_WIFI_DEVICES`; multicast lock in Application

### v0.3.42
- **Connect race fix** — wait for inbound link before outbound (avoids Android↔desktop dual-outbound deadlock)
- **Inbound detection** — outbound connect loop recognizes peer-initiated links (fixes missed established sessions)
- **Timeouts** — desktop connect raised to 20s (RNS establishment is 12s; 10s was too short per Arch logs)
- **Reverse wake** — responder skips wake-back ping-pong; dedupes rapid `/api/request_connect` bursts

### v0.3.41
- **Android RNS connect** — periodic beacon + auto RNS announce; UDP path priming before link; wake peer with `/api/announce`; longer connect timeouts
- **Beacon identity** — register identity hash correctly from LAN beacon pubkey (fixes "no known identity" on connect)
- **Connect IP resolution** — server and UI resolve peer IP from discovery when not passed manually (required for Android reverse connect)

### v0.3.40
- **APK build fix** — `UsbSerialHelper` uses `ChatxzApplication` context instead of invalid `Platform.getApplication()` Java API

### v0.3.39
- **Android debug log** — stdout/stderr saved to Downloads (`chatxz-debug-YYYYMMDD-HHMMSS.txt`)
- **Android connect** — wake peer via reverse-connect before outbound link; 30s reverse wait
- **Identity display** — hash shown without `:` separators in sidebar and settings
- **Settings order** — Network section moved between Storage and Advanced

### v0.3.38
- **Android bundle synced** with desktop (failover, serial teardown, UI fixes, network panel)
- **README** updated: RNS transport table, serial+LAN failover guide, APK build workflow, changelog through v0.3.37

### v0.3.37
- **Serial→LAN failover fix:** remove unplugged serial from RNS transport (stops reconnect spam and `mode` AttributeError on hot-added interfaces)
- **Hot-add serial** finalized like Reticulum config load (`mode`, announce caps); prune dead serial before announces
- **Ubuntu send UI:** keep `linkPeer` during reconnect; register identity/destination aliases on connect and `link_established`
- **Network panel:** shows `link_rns_interface`, `serial_in_rns`, `session_peer`, failover help text

### v0.3.36
- **Session peer preserved** across unexpected link drops — failover loop reconnects without manual Connect
- **Receipt timeout** and **UDP fallback** when AutoInterface carrier lost
- **Serial hot-add** watchdog when USB plugged in mid-session
- **Serial failover is RNS-only** (no HTTP reverse-connect on serial path)

### v0.3.35
- Interface health checks AutoInterface `timed_out_interfaces`; longer failover connect timeout (22s)
- `lan_mesh_has_peer()` for real LAN mesh detection (not just `lan_ip()`)

### v0.3.34
- `reconnect_active_peer()`, link failover loop (every 3s), path scrubbing on dead interfaces

### v0.3.33
- Peer identity aliasing, link handoff without clearing chat history on reconnect

### v0.3.32
- Cancel transfer, history cleanup, Android USB serial permission flow

### v0.3.31
- **Rename:** GitHub repo and all references `chatzx` → `chatxz`
- **Manual-only** RNS announce and beacon (no periodic/auto announce on any platform)
- **Android↔desktop connect:** reverse-connect via `POST /api/request_connect` when outbound RNS link fails
- **RNS interface presets:** add/delete UDP, TCP, and Serial interfaces in Settings → Network
- **Folder zip progress:** live zipping status with file count in the transfer dock
- **Incoming files:** receive status list in the bottom dock for all active downloads
- **Version** shown next to the chatxz title in the sidebar header

### v0.3.30
- Android↔desktop connect: beacon carries identity pubkey (no RNS announce required to learn peer)
- Unicast RNS announces to peer IP + subnet (UDP 4242 broadcast often blocked on Wi-Fi)
- Connect wakes peer via HTTP `/api/announce` and `request_path` while waiting for identity

### v0.3.29
- Single version source: `version.properties` drives Gradle + `APP_VERSION` (via `chatxz/_version.py`)
- Release bump: `./scripts/bump-version.sh 0.3.29` then tag `v0.3.29`

### v0.3.28
- Bump Android APK build to match server version (0.3.28)
- Network panel connect hint updated for longer announce/link wait

### v0.3.27
- Remove duplicate Clear history button from chat header (keep peer actions menu only)
- Connect waits up to 18s for peer RNS announce; 10s link timeout for slow devices (Android)
- Pass peer LAN IP when connecting from discovered list to use current beacon hash

### v0.3.26
- Clear chat history is per-peer (chat header + peer actions), removed from Settings
- Android: RNS auto-announce every 15s (beacon alone cannot establish encrypted links)
- Beacon payload includes identity hash; connect resolves identity → message destination hash
- Clearer connect errors when peer has not completed an RNS announce

### v0.3.25
- Fix Ubuntu→Arch (incoming) messages not showing in web UI — correct peer hash via discovery + link cache
- Outgoing connects call RNS `identify()` so receivers learn the remote identity
- UI shows incoming messages for the active linked peer even if chat_peer was mis-tagged

### v0.3.24
- Fast connect: single ~4s attempt, no 60s retry loop
- **Reset network** button + `POST /api/network/reset` — clears peers, disconnects link, zeros beacon counters
- Optional weekly auto-reset of discovery/beacon stats (Settings → Network)
- Incoming link peer hash resolved via discovery when RNS returns local identity

### v0.3.23
- Fix peer destination hash (full_hash) so incoming links identify the correct peer
- Do not drop active chat when a new connect attempt fails
- Link status polls from server; clearer Network settings labels for Link active / Inactive

### v0.3.22
- Receiver-side inline video playback (URL encoding, metadata race fix, Accept-Ranges)
- Android LAN discovery: periodic beacon, subnet unicast probes on all announces, instant peer WS push

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