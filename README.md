# chatxz

**Encrypted peer-to-peer messaging and calls** over the [Reticulum Network Stack](https://reticulum.network/).

**Version 1.0.0** — full Rust rewrite of voice, video, screen sharing, and the web application.

---

## Architecture

chatxz is a **Rust application**. The only Python component is a headless **RNS transport daemon** (`chatxz.rnsd`) that wraps Reticulum — it is not a web server and is never started manually.

```
./run.sh web
    └── chatxz (Rust)          port 8742  ← you use this
            ├── Web UI, REST API, WebSocket
            ├── Voice / video / screen (Opus, jitter buffer, PLC)
            └── spawns → chatxz.rnsd (Python)  port 8743  ← internal only
                              └── Reticulum links, packet I/O
```

| Layer | Language | Responsibility |
|-------|----------|----------------|
| **Application** | **Rust** | HTTP, WebSocket, calls, media, UI, API |
| **Transport** | Python + RNS | Encrypted links, announce, packet TX/RX |

There is no Python web server. No hybrid proxy. One binary, one port.

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

---

## License

GPL-3.0-only — see [LICENSE](LICENSE).