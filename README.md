# chatxz

**Encrypted peer-to-peer messaging** over the [Reticulum Network Stack](https://reticulum.network/). No accounts, no cloud relay for 1:1 chat — messages, files, and calls travel over AES-256 encrypted links on your LAN, USB serial, or optional TCP hub.

**Current version: 2.0.0** · [Releases](https://github.com/narl3yyy-svg/chatxz/releases) · [Changelog](CHANGELOG.md)

---

## What you get

| | |
|---|---|
| **Messaging** | Per-peer threads, delivery receipts, offline queue, emoji picker |
| **Calls** | Voice, video, and screen share over RNS — Rust media engine, no WebRTC |
| **Files** | Any size via encrypted RNS resources; large LAN files can use direct HTTP with `--share` |
| **Discovery** | UDP beacon + RNS announce on your pinned IPv4; saved contacts with custom names |
| **Transports** | **UDP LAN** or **TCP LAN** on desktop; **TCP LAN** default on Android; **USB serial** for direct cable links |
| **Privacy** | End-to-end encrypted RNS links; port 8742 is local UI only |

Open a chat, wait for **Link: Active**, then message, send files, or tap 📞 📹 🖥 in the header.

---

## Quick start

### Linux / macOS

Requires **Python 3.10+** and **Rust** ([rustup.rs](https://rustup.rs)).

```bash
git clone https://github.com/narl3yyy-svg/chatxz.git
cd chatxz
./run.sh web --share
```

Open **http://localhost:8742** (or `http://<your-lan-ip>:8742` with `--share`).

First run builds the Rust server (`chatxz-server`) and installs Python RNS dependencies.

### Windows (cmd only)

Requires **Python 3.10+** and **Rust**.

```cmd
git clone https://github.com/narl3yyy-svg/chatxz.git
cd chatxz
run.bat web --share
```

Open **http://127.0.0.1:8742**.

### Android

1. Download **`chatxz-2.0.0.apk`** (or latest) from [GitHub Releases](https://github.com/narl3yyy-svg/chatxz/releases).
2. Install, grant notification permission, complete setup (display name + LAN IPv4).
3. Tap a discovered peer or saved contact to connect.

Android runs the same architecture: **Rust primary** on port 8742 (WebView) and **Python RNS backend** on 8743. Build the APK with `scripts/build-rust-android.sh` to bundle the arm64 `chatxz-server` binary.

---

## Architecture (v2.0.0)

chatxz v2 is a **clean rewrite** of voice, video, and screen sharing in Rust. Python remains only for Reticulum networking and messaging.

```
Browser ──HTTP/WS──► Rust chatxz-server :8742
                         │  /api/call/*  /ws/media  (native)
                         │  static UI, proxy
                         ▼
                    Python backend :8743
                         │  RNS links, messaging, discovery
                         ▼
                    Remote peer (CHXZ media + __call signaling)
```

| Component | Language | Role |
|-----------|----------|------|
| `chatxz-server` | **Rust** | Web UI, call API, Opus media, jitter buffer, PLC |
| `chatxz.web.server` | Python | RNS transport, contacts, file transfer, discovery |
| `chatxz-protocol` | Rust | `CHXZ` media framing (v2), call signaling JSON |
| `chatxz-media` | Rust | Opus encode/decode, packetization |
| `chatxz-call` | Rust | Call state machine (invite/accept/hangup) |

**Media wire format:** `CHXZ` magic, 480-byte max payload per packet (RNS MTU safe). Signaling uses `__call` JSON over the RNS link.

**Launch commands unchanged:** `./run.sh web` and `run.bat web`.

---

## First-time setup

1. Choose a **display name**.
2. Pick your **LAN IPv4** from the list (required — scopes discovery and wake).
3. Tap **Announce LAN** (sidebar) or enable auto-announce in Settings.
4. Tap a peer in **Discovered** or open a saved contact — header shows **Connected** when the RNS link is live.

### Sidebar

| Action | What it does |
|--------|----------------|
| **Announce LAN** | RNS announce + UDP beacon on your pinned IPv4 |
| **Announce Serial** | RNS announce on USB (when serial is online) |
| **Discovered row** | Open chat on that transport |
| **Contact LAN/USB row** | Open chat on the saved path for that peer |

---

## Networking

### Dual identity (LAN + USB)

Each device can have **two RNS identities**:

| Transport | Identity | Connect hash | Label |
|-----------|----------|--------------|-------|
| LAN (UDP/TCP) | `identity_lan` | LAN hash | `peer · LAN` |
| USB serial | `identity_serial` | Serial hash | `peer · USB` |

- No automatic transport failover — the row you tap is the path used.
- LAN and USB can both stay linked to the same peer (separate sub-rows on one contact card).

### LAN transport

| Platform | Default | Notes |
|----------|---------|-------|
| **Desktop** | UDP LAN | Fast discovery on Wi‑Fi/Ethernet; optional TCP LAN in Settings |
| **Android** | TCP LAN | Stable on mobile; UDP optional in Settings |

**Firewall (private LAN):** UDP **4242** (RNS), **8743** (beacon), TCP **8742** (web UI), TCP **8743** (internal backend), TCP **4242** when using TCP LAN or hub.

### USB serial

Plug in a USB adapter, set device + baud in Settings → Network, Apply, restart. Use **Announce Serial** and connect via the USB row.

---

## Voice, video, and screen (v2.0.0)

1. Open a chat with **Link: Active**.
2. Tap **📞** (voice), **📹** (video), or **🖥** (screen) in the header.
3. Callee uses the in-page **Accept / Decline** bar.
4. Hang up on either side ends the call for both.

**Technical details**

- **Signaling:** `__call` JSON over RNS (invite, accept, reject, hangup, busy).
- **Media:** `CHXZ` v2 packets — Opus audio (10 ms frames), JPEG video/screen; jitter buffer + packet-loss concealment in Rust.
- **Browser path:** PCM capture in WebAudio → Rust Opus → RNS → remote Rust decode → PCM playback.

Grant microphone/camera when prompted. Use `http://localhost:8742` on desktop if the browser blocks permissions.

---

## TCP hub (optional group chat)

A **hub** relays encrypted **group chat** over the internet on **TCP 4242**. It does not mix with normal LAN/UDP discovery peers.

| Role | Behavior |
|------|----------|
| **Hub server** | Listens on `0.0.0.0:4242`; relays group messages to TCP-connected clients |
| **Hub client** | Dials your hub host (public IP, DDNS, or VPN) |
| **Hub off** | P2P only |

---

## Building from source

```bash
# Desktop
./run.sh install          # Python deps + Rust release build
cargo test                # Rust unit tests
cargo build --release -p chatxz-server

# Android APK (requires Android NDK)
bash scripts/sync-android.sh
bash scripts/build-rust-android.sh   # bundles arm64 chatxz-server
cd android && ./gradlew assembleRelease
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| **Call buttons greyed out** | Open the peer's chat first; wait for **Link: Active** |
| **No call audio** | Both sides v2.0.0+; callee must Accept; grant microphone |
| **Rust server missing** | Run `./run.sh install` or `cargo build --release -p chatxz-server` |
| **Port in use** | `bash scripts/stop-chatxz.sh` then restart |
| **Android calls fail** | Rebuild APK with `scripts/build-rust-android.sh` |

**Stop server:** `Ctrl+C` in the terminal (releases 8742, 8743, 4242).

---

## License

GPL-3.0-only — see [LICENSE](LICENSE).