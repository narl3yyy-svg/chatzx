# chatxz

Encrypted peer-to-peer chat over the [Reticulum Network Stack](https://reticulum.network/). No accounts, no cloud servers — your identity is a local keypair, and messages travel over encrypted RNS links on your LAN (Wi‑Fi, Ethernet, USB serial, or beyond).

**Current version:** 0.3.167

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

**Optional:** `bash scripts/install-arch.sh` (Arch) or `install-debian.sh` (Ubuntu/Debian) for system packages / voice / serial permissions.

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

- **v0.3.166** — **Discovery scope hardening:** rejects bridged LAN announces on USB, IP-less LAN ghosts, and cross-subnet beacons; dedupes same-identity peers; serial links no longer failover-loop on path-table flaps
- **v0.3.165** — **Transport-aware discovery + transfer UX:** serial badge only for real USB announces (cross-subnet LAN peers rejected); cancel propagates to receiver; file progress shows USB/LAN and receive status on both sides
- **v0.3.164** — **Serial connect fix:** restores USB routes from announce receipts when LAN rebroadcast steals path_table; registers peer identity on announce; connect primes/reinforces serial path before outbound link
- **v0.3.163** — **Serial discovery fix:** path table preferred over stale LAN rebroadcast for announce classification; out-of-scope LAN IPs reclassified as USB serial peers; discovery enabled on serial attach; scope purge skips serial entries
- **v0.3.162** — **Cross-send hardening:** verifies link remote identity before every send (blocks Ubuntu→Windows leak); beacon discovery no longer overwrites serial peers with LAN IPs; discovered USB peers connect over serial only (no polluted IP)
- **v0.3.161** — **Parallel transport sessions:** LAN link to one peer + serial link to another stay independent; send/failover locked to each peer's transport zone (fixes Arch→Ubuntu messages leaking to Windows); discovery dedupes serial/LAN duplicates
- **v0.3.160** — **Serial connect + offline queue:** path priming works during connect/failover; queued messages drain on all reconnect paths; discovery prefers in-scope LAN when both transports are up; stale contact IPs no longer force LAN to USB peers
- **v0.3.159** — **Serial discovery symmetry:** IP-less announces always tagged serial; path pinned on SerialInterface during connect; 22s+ link-establishment timeout on serial
- **v0.3.158** — **Serial inbound fix:** Arch no longer rejects Ubuntu serial links when a stale UDP path exists on the shared LAN
- **v0.3.157** — **Consolidated serial connect:** single prime/inbound/outbound attempt; discovery prefers `via=serial`; reconnect blocked during active connect
- **v0.3.156** — **Settings:** Live status is its own Network tab; serial failover waits for USB to return instead of hammering reconnect
- **v0.3.155** — **Serial stability:** inbound serial accepted before hash resolves; serial-only peers use longer reconnect cooldown
- **v0.3.154** — **Network in Settings:** status/Announce/Reset moved out of sidebar; serial transfers block failover while active
- **v0.3.153** — **Full-page settings UI** with left nav (Profile, Storage, Network, System)
- **v0.3.152** — **Serial file transfers:** window=2 confirmed chunks at 57600 baud, longer timeouts, no compression on serial; stricter serial/LAN inbound rejection; network settings UI overhaul
- **v0.3.151** — **Serial session stability:** switching peers no longer hijacks failover to the previous LAN peer; stale hash checks honor active serial links; announce rebroadcast stays on the receiving transport; bridged multi-hop LAN paths pruned
- **v0.3.150** — **Cross-talk fix:** serial and LAN transports are isolated — messages to Ubuntu cannot leak to Windows; rebroadcasted LAN peers no longer appear on serial discovery
- **v0.3.149** — **Dual LAN + serial connect:** Arch↔Windows stays on pinned LAN; Arch↔Ubuntu uses serial first (clears stale UDP paths, skips bogus LAN quick-connect)
- **v0.3.148** — **Serial startup fix:** no duplicate hot-add when RNS already loaded `/dev/ttyUSB0` from config — fixes Ubuntu `NoneType is_open` crash on startup
- **v0.3.147** — **Serial discovery fix:** serial announces carry no LAN IP; cross-subnet peers kept in Discovered (IP stripped); connect uses serial when peer IP is outside pinned scope; no spurious RNS discovery logs for rejected LAN peers
- **v0.3.146** — **Serial across subnets:** Announce bursts on USB serial when LAN is also enabled; serial peers/links bypass pinned LAN scope (Arch on 10.0.30.x ↔ Ubuntu on 10.0.5.x over serial works)
- **v0.3.145** — **Strict pinned LAN isolation:** 10.0.5.x and 10.10.10.x on the same NIC no longer chat; discovery, connect, inbound links, and messages all enforce /24 scope; RNS announces carry pinned IPv4
- **v0.3.144** — **Hub TCP subnet guard:** no dial to hub host on another subnet when LAN is pinned (10.0.5.x vs 10.0.30.x); hub **Off** fully disables saved hub TCP client
- **v0.3.143** — **Hub + pinned subnet P2P:** local UDP to Ubuntu on 10.0.5.x no longer hijacked by hub TCP (Android leak fixed); pinned IPv4 scopes discovery while hub client; LAN picker save hardened
- **v0.3.142** — **Linux multi-IP NICs:** Settings lists all IPv4 addresses on each interface (secondary `ip addr add` aliases); pin `NIC|IP` to choose subnet
- **v0.3.141** — **Hub + TCP LAN warnings:** Settings shows why TCP LAN is blocked (hub server — port 4242 reserved) or limited (hub client — local P2P only); API rejects invalid switches with the same message
- **v0.3.140** — **Hub client + TCP LAN:** Primary LAN transport can be **TCP LAN** while hub role is **client** — local P2P uses direct TCP; group chat still uses the hub TCP link only
- **v0.3.139** — Hub group chat isolated to TCP hub transport: hub server relays only to TCP-connected clients; peers with hub **off** ignore group messages (no more P2P leak to Ubuntu); removed unused `config/config.ini` and empty `chatxz/ui` package
- **v0.3.138** — Changing **Settings → Network** IPv4 drops active links and clears RNS paths (no cross-subnet chat after NIC change); strict beacon /24 filter; pinned IPv4 fixes broadcast `255.255.255.255` fallback; **Ctrl+C** clean exit on Linux, **Ctrl+Z** disabled in `run.sh`
- **v0.3.137** — **Network** sidebar panel (live status, Announce, reset) separate from **Settings** (IPv4 pin, UDP/TCP LAN, hub, serial); discovery in Auto mode scopes to your primary LAN /24 (10.0.30.x no longer mixes with 10.10.100.x); cross-subnet beacon filter; release notes on first install and updates
- **v0.3.136** — macOS/Firefox mic hints (no more Windows text on Mac); Firefox voice retry with simpler audio constraints; **Disconnect** no longer auto-reconnects after 5–10s; TCP LAN `ifac_netname` crash patch on incoming connections
- **v0.3.135** — Emoji search uses per-emoji names/aliases (`zebra`, `pizza`, `heart`, etc.) via `emoji-keywords.json`
- **v0.3.134** — LAN scope match fix: `172.17.13.x` peers visible when pinned to `172.17.121.x` (same `172.17/16`); contact IP no longer overwritten by `10.0.5.x` VPN beacons
- **v0.3.133** — Platform-aware UI hints (Windows vs macOS vs Linux): mic permission messages, folder picker, serial setup; server reports `windows`/`darwin`/`linux`/`android`; saved contact IP prefers your pinned LAN subnet
- **v0.3.132** — Windows: saved contact IP updates live when peer moves subnet (no server restart); searchable emoji picker; clearer microphone permission hints (Helium + Windows Privacy); quieter file-preview disconnect logs; macOS Apple Silicon CPU temp via ioreg/powermetrics/thermal estimate (`~` when approximate)
- **v0.3.131** — Windows: fix large-file preview/send (`WinError 87` sendfile); CPU/temp status bar on Windows + macOS; discovered peer IP updates in GUI when same identity moves subnet
- **v0.3.130** — TCP LAN fix: hot-added TCP server no longer crashes on incoming connections (`ifac_netname`); UDP preferred over TCP when both enabled; less TCP client reconnect spam
- **v0.3.129** — Windows: graceful **Ctrl+C** shutdown (no traceback, no “Terminate batch job”, in-flight transfers cancelled cleanly)
- **v0.3.128** — LAN file speed: HTTP fast path for files 2MB+ with `--share`; RNS link MTU raised to 1064 + `link_mtu_discovery` (fixes 500B links and 114 slow segments)
- **v0.3.127** — macOS/Linux: `scripts/stop-chatxz.sh` + `lsof` port cleanup (fixes errno 48 / port 4242 in use); settings display name shows instantly after restart (no 5s wait)
- **v0.3.126** — Windows: beacon broadcasts scoped to default-route NIC (fixes slowness on PCs with many virtual adapters); **v0.3.125** restart fix included
- **v0.3.125** — Windows: fix **Restart server** 500 error (`UnboundLocalError` on `sys`); restart reloads network stack in-process without closing cmd
- **v0.3.124** — Windows: fix settings save, Explorer folder picker, in-process restart (cmd stays open); faster NIC scan (ipconfig default); ↻ refresh spins
- **v0.3.123** — Windows: **Ctrl+C releases all ports** (`stop-chatxz.bat` + RNS teardown); README/docs: **cmd + `run.bat` only** (no PowerShell install/run)
- **v0.3.122** — Windows: fix `run.bat` cmd parsing (`xz.web.server` error); restart from UI uses `run.bat` on Windows
- **v0.3.121** — Windows: faster **`run.bat`** (skip deps when `.venv` ready); removed `install.bat` and all PowerShell runners; Mac fix: no AutoInterface + UDP LAN duplicate (errno 48); restart no longer crashes on port 8742
- **v0.3.120** — Windows: **`run.bat web --share`** only — runs from git clone folder, no separate install step; first run auto-fetches deps into local `.venv`
- **v0.3.119** — Windows: **`install.bat`**, **`run.bat`**, **`uninstall.bat`** only (removed PowerShell runners); pure cmd workflow with live logs
- **v0.3.118** — Windows: run from **cmd** with **`run.bat web --share`**; live logs in your cmd window; startup announce deferred for faster boot
- **v0.3.117** — Windows cmd: **`run.cmd web --share`** runs server in foreground with live logs (`python -u`); fix `.\run.ps1` in cmd opening VS Code instead of starting server; faster setup wizard save
- **v0.3.116** — Windows: **`run.cmd web --share`** from cmd (no separate install); auto-setup `.venv` on first run; Git Bash can use `./run.sh` like Linux/Mac
- **v0.3.115** — Windows/macOS desktop: **source-only** (`run.ps1` / `run.sh`); CI releases **Android APK only** (no Windows exe zip, no macOS zip)
- **v0.3.114** — Windows: setup wizard **Get started** no longer hangs (single config write, lighter announce); IPv4 picker lists **every address** on each adapter (custom aliases included), stored as `NIC|ip`; faster first-run interface scan
- **v0.3.113** — Mac/Windows: auto-repair duplicate UDP LAN interfaces on startup (`Address already in use` / errno 48); beacon broadcasts scoped to pinned/default NIC (fixes multi-subnet spam freeze); UI error banner + **Repair interfaces** button; block adding duplicate UDP/TCP LAN presets
- **v0.3.112** — Windows crash fix: dedupe duplicate UDP LAN interfaces (two on port 4242 killed RNS after ~12s); RNS init errors no longer `sys.exit` the whole server; Windows stale `chatxz.exe` cleanup via `netstat`/`taskkill`; safer Windows restart (spawn new process instead of `execv`)
- **v0.3.111** — macOS/Linux source: `run.sh` uses `python3 -m pip` (fixes `pip: command not found` on Mac when running `./run.sh web --share`)
- **v0.3.110** — Windows folder picker runs on main thread (dialog actually appears); PowerShell WinForms picker with visible window; Windows NIC list merges ipconfig + `Get-NetAdapter` (all adapters shown); `/api/interfaces?refresh=1` + ↻ rescan; simplified Network settings (step 1: NIC, step 2: transport); macOS releases ship as `.zip` only (no `.dmg`)
- **v0.3.109** — Windows portable UI fixes: visible folder picker via Win32 `SHBrowseForFolder` (PowerShell fallback no longer hidden); **Primary LAN transport** dropdown in setup + Settings; RNS transport controls moved above network status panel; scrollable status panel
- **v0.3.108** — Windows received-files folder picker: native PowerShell dialog (portable exe) with tkinter fallback; path normalization accepts `C:/` slashes and resolves folder names; macOS interface list fixes false **offline** status (ifconfig UP/active parsing)
- **v0.3.107** — **TCP LAN** transport preset: same LAN beacon discovery, Announce, pinned NIC, serial failover, and peer wake as UDP LAN — but RNS links use direct TCP (listen on 4242, dial discovered peer IPs on connect). Add via Settings → Network → **+ TCP LAN**
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

[GNU General Public License v3.0](LICENSE) (GPLv3). You may use, modify, and redistribute chatxz under the terms of GPLv3.
