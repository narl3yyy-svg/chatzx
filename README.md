# chatxz

Encrypted peer-to-peer chat over the [Reticulum Network Stack](https://reticulum.network/). No accounts, no cloud servers — your identity is a local keypair, and messages travel over encrypted RNS links on your LAN (Wi‑Fi, Ethernet, USB serial, or beyond).

**Current version:** 0.3.106

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

1. Download **`chatxz-0.3.106-windows-portable.zip`** from [Releases](https://github.com/narl3yyy-svg/chatxz/releases).
2. Unzip anywhere (e.g. `C:\Users\You\chatxz`).
3. Open the `chatxz` folder and double-click **`chatxz.exe`**.
4. Browser opens at **http://127.0.0.1:8742**. Allow Windows Firewall on **private** networks if prompted (UDP 4242, TCP 8742).

Your identity, chats, and received files live in **`chatxz-data\`** next to the exe — back up or move that whole folder to relocate.

---

## Android

1. Download the **`.apk`** from [Releases](https://github.com/narl3yyy-svg/chatxz/releases).
2. Install and open chatxz.
3. Grant notification permission when asked. Microphone is requested when you tap 🎤 to record a voice note, or tap **Microphone** in Settings → Network.

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

If `git pull` fails with local changes (e.g. `tests/test_platform_interfaces.py`), stash first:

```bash
git stash -u && git pull
```

Open **http://localhost:8742** (or `http://<your-lan-ip>:8742` with `--share`).

**Firewall:** allow UDP **4242** (RNS) and **8743** (discovery beacon) on the LAN.

**Before pushing changes**, run the pre-push check:

```bash
bash scripts/check.sh
```

---

## Using chatxz

1. On first launch, complete the **setup wizard** (display name, LAN interface, optional auto-announce).
2. **Copy your identity hash** from the sidebar (click to copy).
3. Tap **Announce** (📡) to discover peers instantly, or enable **Auto-announce** in Settings → Network.
4. **Click a peer** on your selected network/interface or paste a hash in **Connect**.
5. When **Link: Active** shows in the dock, chat, send files, images, voice notes, and folders.

| Feature | Details |
|---------|---------|
| Messaging | Per-peer threads, delivery receipts, offline queue, emoji |
| Files | Any size via encrypted RNS resources; drag & drop; live speed in dock |
| Network | LAN discovery, USB serial ↔ UDP failover (no manual Announce), saved contacts |
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

- **v0.3.106** — Android VPN interfaces in picker (ConnectivityManager); discovery peers no longer flash away on startup; 5‑min discovery TTL; file transfers prefer TCP/LAN over UDP with compression off on fast paths; Android debug log export to user-chosen folder
- **v0.3.105** — First-run setup wizard (retention + received folder); Arch auto-discovery shows all subnets; Android interface retry + announce burst fixes
- **v0.3.104** — Hub/TCP audit: Arch as hub server, Ubuntu/Android as clients; TCP failover; group chat sender names; hub queue drain; settings modal stays open on save
- **v0.3.103** — Hub group chat routing fixes; TCP client/server presets; network panel improvements
- **v0.3.102** — TCP hub mode polish; interface picker fixes; discovery scope per pinned NIC
- **v0.3.101** — Hub server/client group chat via TCP; saved contacts migration; link failover tuning
- **v0.3.100** — Every `connect_to` success path drains the outbound queue and consolidates links (no missed drain on serial/wake/reverse paths)
- **v0.3.99** — Queued messages drain on explicit reconnect; one link per peer (no parallel-session split); queue sends use the active link; UDP/LAN preferred over stale serial when VPN/LAN is up; faster failover when serial unplugged or peer restarts
- **v0.3.98** — Disconnect stays disconnected (no auto-reconnect/failover/resume); passive inbound links after disconnect (messages still arrive with unread badge + desktop notification); purge stale RNS `known_destinations` on beacon register; close mismatched parallel links on Connect; discovered-peer unread badges
- **v0.3.97** — Link and message peer resolution always uses canonical connect hash (fixes wrong ID on connect/send and stale-hash reject while notifications still fire); send allowed when actively linked; `run.sh` fix for unset `VIRTUAL_ENV`
- **v0.3.95** — Canonical connect hash everywhere (sidebar, beacon, discovery, messaging); separate `connect_hash` vs `identity_hash` in API; LAN interface picker lists all NICs in setup wizard and settings; `/api/network` alias; pubkey-verified RNS peers preferred over stale beacon hashes; purge stale RNS paths on identity supersession; improved `install.sh` / `uninstall.sh` / `run.sh` (repo-local portable mode)
- **v0.3.94** — Linux fresh install defaults to UDP LAN (fixes no discovery until manual config); first-run setup wizard (name, LAN interface, auto-announce off by default); auto-announce setting in Network; instant manual announce (0.4s debounce, 1s peer broadcast); subnet-scoped discovery per selected interface; hub group no longer hijacks 1:1 links (`__hub_group__` leak fix); large hub messages use resource transfer; reorganized Settings → Network layout
- **v0.3.93** — Immediate peer supersession: one hash per LAN IP, old hash evicted on new Announce (no 30s wait); block Connect/send to stale hashes; migrate or remove saved contacts when peer identity changes; disconnect stale links
- **v0.3.92** — Live identity regeneration (no server restart); discovered peers expire in 30s and refresh when hash/IP changes; evict stale peer entries after identity swap on same host; folder picker reverted to v0.3.90 native dialogs with Select folder button kept
- **v0.3.91** — Fix RNS init on Linux when started in background thread (signal handler patch); native folder picker for received-files (zenity/kdialog/tk, not browser upload); per-interface Active toggles + AutoInterface on/off; `scripts/check.sh` pre-push test suite
- **v0.3.90** — Windows startup speed: HTTP server listens immediately (RNS init in background); fast ipconfig-based LAN scan with 45s cache (fixes slow PowerShell on multi-homed NICs); single-pass network status API
- **v0.3.89** — Windows/macOS LAN detection: enumerate NICs via PowerShell/ifconfig (fixes false "LAN disconnected" on Windows portable); prefer default-gateway subnet IP on multi-homed hosts; frozen exe restart no longer breaks when install path contains spaces; warn when TCP Client targets this machine
- **v0.3.88** — Inbound link fixes: resolve peer from link remote identity (not blind discovery guess); adopt existing inbound links before outbound connect; promote inbound sessions when peer hash was unknown; fixes split-brain startup where one side queued messages for minutes
- **v0.3.87** — Dual-path serial failover fixes: restore serial in RNS config when port accessible; detect peer LAN down (HTTP wake timeout) and switch to serial even when local LAN is up; accept incoming serial links during failover; stop premature UDP upgrade when peer path is still serial; serial retry after LAN connect failure; eager runtime serial ensure on failover/startup
- **v0.3.86** — LAN-primary failover: prefer UDP when RJ45/Wi-Fi up, serial when down; register outbound links even if poll misses ACTIVE; pick best healthy link per send; serial hot-add only (no duplicate config interface)
- **v0.3.85** — Failover link tracking: promote serial/UDP reconnect to active session; keep active links registered (fix orphan teardown); adopt background links on send; stop path-switch flapping on equal scores
- **v0.3.84** — Serial failover fix: RNS announces pinned to serial/UDP interface (no errno 101 UDP spam when RJ45 unplugged); dedupe duplicate SerialInterface on same port; suppress offline UDP/AutoInterface when physical LAN down
- **v0.3.83** — Dual-path failover: auto RNS announce before path switch; keep paths on target transport (no full wipe); faster reconnect when USB serial + LAN both configured; detach AutoInterface when ethernet down (fixes errno 101 spam on Ubuntu)
- **v0.3.82** — Fix import: `physical_lan_reachable` from `chatxz.utils.platform` (Ubuntu startup crash)
- **v0.3.81** — Failover overhaul: serial-first when RJ45/Wi-Fi down (VPN no longer masquerades as LAN); no HTTP wake to unreachable peers; RNS-only serial auto-announce; faster Ctrl+C shutdown; stop clearing live serial paths
- **v0.3.80** — LAN interface picker lists VPN tunnels (WireGuard, OpenVPN, Tailscale, tun/tap); auto mode prefers physical Ethernet/Wi-Fi over VPN
- **v0.3.79** — Serial→LAN failover: prefer UDP paths when USB unplugged; clear stale serial paths; wake peer and prime UDP during failover; queue sends when link transport is offline
- **v0.3.78** — Serial-only fixes: Android transport enabled with serial; burst RNS announces on USB; longer serial connect/identity wait; clearer serial-only UI hints
- **v0.3.77** — Settings → Network: pick which LAN NIC to use (multi-homed hosts); pins LAN IP, beacon, and UDP broadcast to that interface
- **v0.3.76** — Respect configured interfaces only: delete UDP → no LAN beacon/AutoInterface/unicast; serial-only mode for USB chat; restart after changing presets
- **v0.3.75** — LAN carrier detection (shows **disconnected** when cable/Wi-Fi unplugged); serial-only announces when LAN is down; network panel auto-refreshes every 5s; fix WebSocket client count leak on Android restarts; tap Microphone row in Network settings to request permission; Android USB grant triggers serial hot-add
- **v0.3.74** — Serial failover: prune stale LAN paths when ethernet drops; serial-first reconnect; hot-add serial on settings Apply; link-active requires healthy transport
- **v0.3.73** — CI: macOS DMG build accepts VERSION env var from workflow
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
