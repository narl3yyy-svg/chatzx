# chatxz

**Encrypted peer-to-peer messaging and calls** over the [Reticulum Network Stack](https://reticulum.network/).

**Version 1.0.0** — Rust application with headless RNS transport.

---

## Architecture

chatxz is a **Rust application** on port **8742**. Reticulum runs in a headless Python subprocess that exposes **no HTTP server** — only a local TCP IPC socket for RNS packet I/O.

```
./run.sh web
    └── chatxz (Rust)              port 8742  ← browser connects here
            ├── Web UI (static)
            ├── REST API (Rust)
            ├── WebSocket (Rust)
            ├── Voice / video / screen (Opus, jitter buffer, PLC)
            └── IPC ──► chatxz.rnsd (Python)  port 8744  ← RNS only
                              └── Reticulum links, packet TX/RX
```

| Layer | Language | Responsibility |
|-------|----------|----------------|
| **Application** | **Rust** | HTTP, WebSocket, calls, media, UI, API routing |
| **Transport** | Python + RNS | Encrypted links, announce, packet I/O (IPC only) |

There is **no Python web server** and **no HTTP proxy** between Rust and Python.

---

## Quick start

Requires **Python 3.10+** (Reticulum) and **Rust** ([rustup.rs](https://rustup.rs)).

```bash
git clone https://github.com/narl3yyy-svg/chatxz.git
cd chatxz
./run.sh web --share
```

Open **http://localhost:8742**

Windows: `run.bat web --share`

---

## Calls (v1.0.0)

- **Voice, video, screen share** over RNS — no WebRTC
- **Rust media engine:** Opus @ 48 kHz, 10 ms frames, jitter buffer, packet-loss concealment
- **CHXZ protocol:** MTU-safe framing for RNS links
- Open a chat, wait for **Link: Active**, then use 📞 📹 🖥

---

## Android

The APK bundles the Rust `chatxz` binary. Build with:

```bash
bash scripts/sync-android.sh
export ANDROID_NDK_HOME=~/Android/Sdk/ndk/<version>
bash scripts/build-rust-android.sh
cd android && ./gradlew assembleRelease
```

---

## Building

```bash
./run.sh install          # Python RNS deps + cargo build --release
cargo build --release -p chatxz-server   # produces target/release/chatxz
cargo test
```

A clean release build should complete with **zero warnings**.

---

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 8742 | TCP | Rust application (web UI + API) |
| 8744 | TCP | Internal RNS IPC (localhost only) |
| 4242 | UDP/TCP | Reticulum network interfaces |
| 8743 | UDP | LAN discovery beacon |

---

## License

GPL-3.0-only — see [LICENSE](LICENSE).