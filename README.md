# chatxz

Encrypted peer-to-peer chat over the [Reticulum Network Stack](https://reticulum.network/). No accounts, no cloud servers — your identity is a local keypair, and messages travel over encrypted RNS links on your LAN (Wi‑Fi, Ethernet, USB serial, or beyond).

**Current version:** 0.3.72

## Download

Pre-built binaries are on **[GitHub Releases](https://github.com/narl3yyy-svg/chatxz/releases)**.

| Platform | File | Install |
|----------|------|---------|
| **Windows 11** | `chatxz-X.Y.Z-windows-portable.zip` | Unzip → double-click `chatxz.exe` |
| **macOS** | `chatxz-X.Y.Z-macos.dmg` | Open DMG → drag `chatxz.app` to Applications |
| **Android** | `chatxz-X.Y.Z.apk` | Sideload (arm64) |
| **Linux / macOS (source)** | Git clone | `./run.sh web --share` |

Portable Windows, macOS, and Android builds are published automatically on every `v*` tag (or workflow dispatch).

---

## Windows (portable)

1. Download **`chatxz-0.3.61-windows-portable.zip`** from [Releases](https://github.com/narl3yyy-svg/chatxz/releases).
2. Unzip anywhere (e.g. `C:\Users\You\chatxz`).
3. Open the `chatxz` folder and double-click **`chatxz.exe`**.
4. Browser opens at **http://127.0.0.1:8742**. Allow Windows Firewall on **private** networks if prompted (UDP 4242, TCP 8742).

Your identity, chats, and received files live in **`chatxz-data\`** next to the exe — back up or move that whole folder to relocate.

---

## Android

1. Download the **`.apk`** from [Releases](https://github.com/narl3yyy-svg/chatxz/releases).
2. Install and open chatxz.
3. Grant notification permission when asked. Microphone is requested when you tap 🎤 to record a voice note.

Reinstalling the app creates a **new identity** — update saved contacts after reinstall.

---

## macOS (portable .dmg)

1. Download **`chatxz-X.Y.Z-macos.dmg`** from [Releases](https://github.com/narl3yyy-svg/chatxz/releases).
2. Open the DMG and drag **chatxz.app** to Applications (or run from the DMG).
3. Double-click **chatxz.app** — a Terminal window opens and your browser loads **http://127.0.0.1:8742**.
4. If Gatekeeper blocks the app: right-click → **Open** (first launch only).

Your identity, chats, and received files live in **`chatxz-data/`** next to the app (same layout as Windows portable). LAN access is enabled by default (like `./run.sh web --share`).

**From source (Arch-style on Mac):**

```bash
git clone https://github.com/narl3yyy-svg/chatxz.git
cd chatxz
bash scripts/install-macos.sh
./run.sh web --share
```

---

## Linux

```bash
git clone https://github.com/narl3yyy-svg/chatxz.git
cd chatxz
bash scripts/install-arch.sh    # Arch
# bash scripts/install-debian.sh  # Ubuntu / Debian
./run.sh web --share
```

Open **http://localhost:8742** (or `http://<your-lan-ip>:8742` with `--share`).

**Firewall:** allow UDP **4242** (RNS) and **8743** (discovery beacon) on the LAN.

---

## Using chatxz

1. **Copy your identity hash** from the sidebar (click to copy).
2. Tap **Announce** (📡) to discover peers on the LAN (manual only — no auto-broadcast).
3. **Click a peer** or paste a hash in **Connect**.
4. When **Link: Active** shows in the dock, chat, send files, images, voice notes, and folders.

| Feature | Details |
|---------|---------|
| Messaging | Per-peer threads, delivery receipts, offline queue, emoji |
| Files | Any size via encrypted RNS resources; drag & drop; live speed in dock |
| Network | LAN discovery, USB serial failover, fast reconnect, saved contacts |
| Privacy | E2E encrypted links (AES-256-CBC); HTTP :8742 is local UI only |

---

## How it works

```
Browser  ←WebSocket/HTTP→  Local server (UI only, port 8742)
                                ↓
                          Reticulum (RNS) — encrypted P2P
                                ↓
                           Remote peer
```

Chat and file payloads never leave the RNS encrypted link. Port 8742 serves only the web interface on your machine.

**Data locations**

| Platform | Config & history |
|----------|------------------|
| Linux | `~/.config/chatxz/` |
| Windows portable | `chatxz-data\` beside `chatxz.exe` |
| Android | App private storage |

---

## Development

```bash
./run.sh web --share --verbose   # RNS debug logs
./run.sh web --share --debug     # Extreme RNS + chat trace
./scripts/bump-version.sh 0.3.52 # Bump version
bash scripts/sync-android.sh     # Before Android builds
```

**Build Windows zip locally** (on Windows):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build-portable.ps1
```

**Build Android APK locally:**

```bash
cd android && ./gradlew assembleRelease
```

On first launch, choose **Normal** or **Debug** mode (Debug enables RNS verbose logs).

---

## Recent changes

- **v0.3.72** — Fix cross-talk: stop merging unrelated contacts into one alias group; route messages strictly by `chat_peer`; per-peer queue counter; prune stale queue entries
- **v0.3.71** — Fix multi-peer chat routing (messages no longer leak into wrong peer thread); keep chat history when links drop; persist all peer threads to disk; Android notification tap opens the correct chat
- **v0.3.70** — macOS portable `.dmg` + `.app` (CI build); `scripts/install-macos.sh`; source workflow `./run.sh web --share` on Mac
- **v0.3.69** — Android: back button minimizes app (keeps server running) instead of restarting; skip Normal/Debug prompt when server already up; chat history only persists for saved contacts; fix queued file messages showing filename "file"
- **v0.3.68** — Fix Windows↔Android connect: register peer identity from beacon pubkey before connect; Windows portable defaults to UDP LAN (not loopback TCP); Android resolves peer IP from discovery server-side; debug log includes startup log tail
- **v0.3.67** — Fix Android crash: restore `chatxz/core/messaging.py` (was accidentally published as `PLACEHOLDER` in v0.3.66)
- **v0.3.66** — Fix Windows↔Android LAN discovery: Android always sends broadcast beacons (not unicast-only), desktop also sends efficient unicast probes; replace full /24 scan (~253 packets) with ~25–45 targeted hosts; discovery stays off until Announce; Android Debug log capture starts before RNS init with in-app log viewer; clearer beacon counters in Network panel
- **v0.3.65** — Android: fix Debug mode startup (no LOG_EXTREME timeout, deferred log capture), fix microphone permission flow and voice recording mime fallback, network panel shows mic status
- **v0.3.64** — Fix desktop announce: no subnet probe (≈6 broadcasts not 259), ignore self-echo in beacon received count, unified announce with server debounce, peer list refresh after announce
- **v0.3.63** — Fix network reset on Android: correct platform detection, keep discovery listening after reset, zero beacon counters, announce debounce, peer list refresh
- **v0.3.62** — Fix Android stuck on “Starting chatxz…” (Chaquopy bool arg, UDP default on Android, auto-migrate broken TCP-only config, startup log)
- **v0.3.61** — Android: no auto-close chat on back, fixed notifications, 5‑min peer TTL, Normal/Debug startup picker, release APK builds. Default first install uses TCP client. Hub server/client group chat via TCP hub. Windows/Android CI builds normal (release) mode.
- **v0.3.60** — Path traversal fixes, multi-peer folder/voice/read-receipt routing, CLI connect fix, version/CI alignment
- **v0.3.59** — Fix network reset (all links), file upload chat tagging, link-active checks, UI link status per viewed peer
- **v0.3.58** — Parallel per-peer RNS links (no peer stealing); per-peer chat routing; background wake connects; Android+Windows coexistence
- **v0.3.57** — Manual announce on all platforms; ephemeral chats for non-contacts; queue retry/clear; peer switch UI; long-text and file-send fixes
- **v0.3.56** — Fix multi-peer routing, reconnect storms, file queue retries, live transfer speed
- **v0.3.55** — Windows portable: run server on main thread; UDP-only RNS config (no AutoInterface)
- **v0.3.54** — Windows portable: apply RNS Interface fix right before Reticulum starts
- **v0.3.53** — Fix Windows portable RNS `Interface` NameError (inject into Reticulum module)
- **v0.3.52** — Fix Windows portable RNS `Interface` import (PyInstaller)
- **v0.3.51** — Windows portable zip (`chatxz.exe`, no installer)
- **v0.3.50** — Android UX, queue UI, notifications, transfer speed fixes
- **v0.3.49** — File receive regression fix, transfer stability

Full history: [Releases](https://github.com/narl3yyy-svg/chatxz/releases) and git tags.

## License

MIT
