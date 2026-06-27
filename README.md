# chatxz

Encrypted peer-to-peer chat over the [Reticulum Network Stack](https://reticulum.network/). No accounts, no cloud servers — your identity is a local keypair, and messages travel over encrypted RNS links on your LAN (Wi‑Fi, Ethernet, USB serial, or beyond).

**Current version:** 0.4.1

## Download

**Android APK** on **[GitHub Releases](https://github.com/narl3yyy-svg/chatxz/releases)**. Desktop: clone the repo and use the platform runner below.

| Platform | Run |
|----------|-----|
| **Android** | `chatxz-X.Y.Z.apk` from Releases — sideload (arm64) |
| **Windows** | `git clone` → **cmd** → `run.bat web --share` |
| **macOS / Linux** | `git clone` → `./run.sh web --share` |

---

## Windows

**Command Prompt (cmd) only.** Removed: `run.ps1`, `install-windows.ps1`, `install-windows.cmd`, `install.bat`.

1. Install [Python 3.10+](https://www.python.org/downloads/windows/) — check **Add python.exe to PATH**
2. Install [Git](https://git-scm.com/download/win)
3. Open **cmd** in the repo folder:

```cmd
git clone https://github.com/narl3yyy-svg/chatxz.git
cd chatxz
run.bat web --share
```

Open **http://127.0.0.1:8742**. Logs stay in that cmd window.

**Voice notes:** use `http://127.0.0.1:8742` (Windows) or `http://localhost:8742` (macOS/Linux). **Firefox on Mac:** Settings → Privacy & Security → Permissions → Microphone → allow `localhost`. Also enable Firefox under **macOS → Privacy & Security → Microphone**.

**Stop:** **Ctrl+C** — server exits and **all ports close** (8742, 4242, 8743). Nothing keeps listening after `run.bat` ends.

**Restart** (Settings button): reloads network stack **in the same cmd window** — does not close `run.bat`.

**Select folder** (received files): opens Windows Explorer — pick a folder, then click **Save settings**.

**Tip:** `--debug` is very slow (especially large file transfers); use it only when troubleshooting.

**Large files (same LAN):** with `--share`, files over 2 MB use a direct HTTP LAN transfer (much faster than RNS UDP segments). Both sides need v0.3.128+ and `run.bat web --share` / `./run.sh web --share`.

| File | Purpose |
|------|---------|
| `run.bat` | Start server from this folder |
| `uninstall.bat` | Force-stop, remove `.venv`, optional data wipe |
| `scripts\stop-chatxz.bat` | Kill stray chatxz processes (used automatically) |

```cmd
run.bat web --share --debug
run.bat web --share --force
uninstall.bat
```

**Update:** `git pull` then `run.bat web --share`

**Firewall (private):** UDP 4242, 8743 — TCP 8742

**Data:** `%USERPROFILE%\.config\chatxz\`

---

## Android

1. Download the **`.apk`** from [Releases](https://github.com/narl3yyy-svg/chatxz/releases).
2. Install and open chatxz.
3. Grant notification permission when asked. Microphone is requested when you tap 🎤 to record a voice note, or tap **Microphone** in the sidebar **Network** panel.

Reinstalling the app creates a **new identity** — update saved contacts after reinstall.

---

## macOS

```bash
git clone https://github.com/narl3yyy-svg/chatxz.git
cd chatxz
./run.sh web --share
```

Open **http://localhost:8742**. Config in `~/.config/chatxz/`. Dependencies install quietly on first run only.

**Stop:** **Ctrl+C** in the terminal (not Ctrl+Z — suspend is disabled in `run.sh`). `run.sh` auto-runs `scripts/stop-chatxz.sh` on the next start to release ports **8742**, **4242**, **8743**.

If you see `address already in use` or `UDP port 4242 is already in use`:

```bash
bash scripts/stop-chatxz.sh
./run.sh web --share
```

**Optional:** `bash scripts/install-macos.sh` — voice support (pyaudio) and Homebrew packages.

---

## Linux

```bash
git clone https://github.com/narl3yyy-svg/chatxz.git
cd chatxz
./run.sh web --share
```

**Optional:** `bash scripts/install-arch.sh` (Arch) or `scripts/install-ubuntu.sh` (Ubuntu/Debian) for system packages / voice / serial permissions.

If `git pull` fails with local changes (e.g. `tests/test_platform_interfaces.py`), stash first:

```bash
git stash -u && git pull
```

Open **http://localhost:8742** (or `http://<your-lan-ip>:8742` with `--share`).

**Stop:** **Ctrl+C** — next `./run.sh web` run calls `scripts/stop-chatxz.sh` to free ports. Manual cleanup: `bash scripts/stop-chatxz.sh`

**Firewall:** allow UDP **4242** (RNS) and **8743** (discovery beacon) on the LAN.

**Before pushing changes**, run the pre-push check (101 unit tests + smoke checks):

```bash
bash scripts/check.sh
```

Hub relay behavior is covered in `tests/test_defaults_hub.py` and `tests/test_hub_tcp_relay.py`.

---

## Using chatxz

1. On first launch, complete the **setup wizard** (display name, LAN interface, optional auto-announce).
2. **Copy your identity hash** from the sidebar (click to copy).
3. Tap **Announce** (📡) to discover peers instantly, or enable **Auto-announce** in Settings → Network.
4. **Click a peer** on your selected network/interface or paste a hash in **Connect**.
5. When **Link: Active** shows in the dock, chat, send files, images, voice notes, and folders.

| Feature | Details |
|---------|---------|
| Messaging | Per-peer threads, delivery receipts, offline queue, searchable emoji picker |
| Files | Any size via encrypted RNS resources; drag & drop; live speed in dock |
| Network | LAN discovery (UDP LAN or **TCP LAN**), USB serial failover (works across pinned subnets — e.g. 10.0.5.x ↔ 10.0.30.x), pinned NIC/VPN, saved contacts |
| Privacy | E2E encrypted links (AES-256-CBC); HTTP :8742 is local UI only |

---

## TCP hub (group chat over the internet)

A **hub** turns one chatxz instance into a **TCP relay** for encrypted **group chat**. Remote friends connect to your hub over the internet on **TCP port 4242** (Reticulum `TCPServerInterface`). The hub **only relays group messages** between clients that are **explicitly connected via TCP** — not LAN/UDP discovery peers.

| Role | What it does |
|------|----------------|
| **Hub server** | Listens on `0.0.0.0:4242`; relays group chat to all TCP-connected hub clients |
| **Hub client** | Dials your hub host (public IP, DDNS, or VPN address) on port 4242 |
| **Hub off** | Normal P2P only — no group chat, no hub relay |

**Isolation by design:** peers using only LAN/UDP discovery will **not** see group messages from hub users (and hub users will not leak into local P2P threads). v0.3.139 enforces TCP-only relay paths.

### Quick setup

**On the machine that will host the hub** (home server, VPS, or Android with hub server):

1. Settings → Network → **Hub role: Server**
2. Note your public IP or hostname; forward **TCP 4242** on your router/firewall
3. Open **Group Chat** in the sidebar

**On each remote client** (Arch laptop, phone, friend's PC):

1. Settings → Network → **Hub role: Client**
2. **Hub host:** your public IP or DNS name (e.g. `203.0.113.50` or `hub.example.com`)
3. **Hub port:** `4242` (default)
4. Restart or Apply — client dials the hub over TCP
5. Use **Group Chat** — messages are E2E encrypted over RNS links through the hub

**P2P on the same machine still works:** hub clients can keep UDP LAN enabled for local 1:1 chat; group chat stays on the TCP hub path only.

### Planned: dedicated headless hub

The core relay already works (`hub_role=server` + TCP listener). Upcoming polish:

- **Headless hub mode** — run as a systemd service with no browser UI
- **Android background** — persistent notification while hub/client is active
- **Modern web UI** — dark mode, better link/hub status, search
- **System tray + auto-start** — desktop runs at login, tray icon for status

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
| Linux / macOS / Windows (source) | `~/.config/chatxz/` (or `%USERPROFILE%\.config\chatxz\`) |
| Portable / `CHATXZ_PORTABLE` | `chatxz-data/` beside app or env path |
| Android | App private storage |

---

## Development

```bash
./run.sh web --share --verbose   # Linux / macOS
./run.sh web --share --debug
```

```cmd
run.bat web --share --debug      # Windows (cmd)
uninstall.bat
```

```bash
./scripts/bump-version.sh 0.3.52 # Bump version
bash scripts/sync-android.sh     # Before Android builds
```

**Build Android APK locally:**

```bash
cd android && ./gradlew assembleRelease
```

On first launch, choose **Normal** or **Debug** mode (Debug enables RNS verbose logs).

---

## Recent changes

- **v0.4.1** — **LAN RTT + ping interval:** Discovered LAN peers show RTT ms; Settings → Network → Link ping interval (5–300s) for LAN and serial; Android beacons visible on desktop without full identity registration
- **v0.4.0** — **Serial + discovery overhaul:** USB RNS auto-announce is one packet every 30s (no bursts); discovered peers update live on scope drift and transport changes; manual Announce uses single serial packet in dual-transport mode.

Older releases: [CHANGELOG.md](CHANGELOG.md) · [GitHub Releases](https://github.com/narl3yyy-svg/chatxz/releases)

## License

[GNU General Public License v3.0](LICENSE) (GPLv3). You may use, modify, and redistribute chatxz under the terms of GPLv3.
