# Changelog

All notable changes to chatxz are documented here. The README lists only the latest release summary.

## [2.0.0] — 2026-06-29

### Changed — full Rust media rewrite
- **Architecture split:** Rust `chatxz-server` on port **8742** (UI, calls, media); Python RNS backend on **8743** (Reticulum, messaging, discovery only).
- **Deleted legacy voice stack:** all Python `audio/`, `calls.py`, `media_engine.py`, `call_bridge.py`, Opus/PyAudio paths removed.
- **New Rust crates:** `chatxz-protocol` (CHXZ v2 framing), `chatxz-media` (Opus, jitter buffer, PLC), `chatxz-call` (signaling state machine).
- **Media protocol:** `CHXZ` magic replaces `CXMZ`; 480-byte MTU-safe payloads; 10 ms Opus frames @ 48 kHz.
- **Launch unchanged:** `./run.sh web` and `run.bat web` build Rust and start both processes.

### Added
- Internal RNS API (`/api/internal/rns/*`) for Rust ↔ Python media/signaling bridge.
- Android dual-process launch (Rust WebView front + Python RNS backend).
- `scripts/build-rust-android.sh` for bundling arm64 `chatxz-server` in the APK.

## [1.0.4] — 2026-06-29

### Fixed
- **Connection churn** — skip HTTP wake when already linked; 20s wake debounce; adopt inbound links before opening duplicate outbound sessions.
- **Truthful link state** — `linked_peers` only reports healthy, usable RNS links; UI syncs from server on WebSocket reconnect and visibility changes.
- **Sticky sessions** — background failover loop unchanged; UI no longer shows Connected when server has no active link.

### Changed
- **Android TCP default** — fresh installs and migrated Android settings use TCP LAN instead of UDP; failover prefers TCP on mobile when both transports are enabled.

## [1.0.3] — 2026-06-29

### Fixed
- **Call audio MTU** — media packets exceeded RNS link MTU (1064 B); audio now sent in 480-byte chunks, video/screen JPEG fragmented to fit.
- **Incoming calls** — in-page Accept/Decline bar replaces `confirm()` dialog; toast + notification on invite.

### Added
- MTU-safe packetization (`packetize_audio_chunks`, video fragmentation) and tests.

## [1.0.2] — 2026-06-29

### Fixed
- **Call link detection** — resolves peer aliases from `linkedPeers` / header status; server validates link before starting a call.
- **No audio** — immediate LAN audio playback (skip jitter hold); scheduled Web Audio output; media sends during outgoing ring.
- **Hangup sync** — remote hangup ends the call on both sides without signaling ping-pong.
- **WebSocket reconnect** — link status no longer flips to Inactive while `linkedPeers` is populated.

### Added
- **UDP-first media** — `_queue_media_link` prefers UDP/LAN for call packets.
- **Adaptive video** — peers exchange stats over `/ws/media` to adjust JPEG quality and frame rate.
- Call simulation tests: remote hangup, hangup ping-pong guard, immediate audio pop.

## [1.0.1] — 2026-06-29

### Fixed
- **Call buttons** — voice/video/screen actions now use the open chat peer (`viewingPeer`) instead of the unused `activePeerHash`, so calls work when messaging is connected.
- **Android CI** — restored missing `MainActivity` call helpers (`appContext`, `setCallActive`, `vibrateIncomingCall`, `stopCallVibrate`, `evaluateJavascript`).

### Added
- Unit tests for v1 call manager and media engine (`tests/test_calls_v1.py`).

## [1.0.0] — 2026-06-29

### Added
- **Voice, video, and screen sharing calls** over RNS — no WebRTC, no external voice servers.
- **Rust media engine** (`chatxz-media`): Opus codec, adaptive jitter buffer (20–200 ms), CXMZ packet protocol.
- Call UI in chat header (📞 📹 🖥) with real-time duplex media via `/ws/media` WebSocket bridge.

### Removed
- Legacy voice notes, pyaudio, `VoiceRecorder`/`VoicePlayer`, `/api/voice`, and all old voice/calling code.

### Changed
- Major architecture rewrite toward Rust for real-time media; Python fallback when Rust extension not installed.

## [0.5.8] — 2026-06-27

### Fixed
- **Contact crash** — merging contacts with integer `port` no longer raises `'int' object has no attribute 'strip'`.
- **Delete contact** — deleting by LAN or serial hash removes the full merged contact and updates the UI immediately.
- **Stale RTT** — latency clears when the link drops or UDP ping fails (e.g. peer unplugged); header RTT only shows while actually connected.
- **Probe interval** — changing LAN/serial ping interval takes effect immediately and re-probes on the next cycle.

### Added
- **Chat header details** — full peer hash and interface type (LAN / USB Serial) shown under the display name.
- **LAN ping packet size** — configurable UDP probe payload (32–1472 bytes) under Network settings.
- **Custom sidebar title** — replace “chatxz” in the header (max 18 characters) in Profile settings.
- **Emoji search** — common terms like happy, sad, and funny match relevant emojis.
- **Sidebar toggle** — robot-style `[=•]=` button instead of the hamburger menu.

## [0.5.7] — 2026-06-27

### Fixed
- **Duplicate contacts** — split LAN/USB save files and orphan JSON rows merge into one contact on load; stale duplicate files are removed from disk.
- **Saved peers in Discovered** — LAN and serial hashes already on a saved contact no longer appear in Discovered (including related names like 330s/330ss).
- **RTT in ms** — link RTT is preferred over UDP probes; serial peers without an IP get latency from the active RNS link; chat header and contact rows show live ms.
- **Android display name** — announces and beacons use the configured name or device model when settings name is empty (no more hash-only label).

### Added
- **Collapsible desktop sidebar** — toggle with ☰ on wide screens; state persists in localStorage.

## [0.5.6] — 2026-06-27

### Fixed
- **Stale contact hashes** — saved contacts auto-refresh `lan_hash` / `serial_hash` when discovery reports the current peer (by IP, identity, or related name like 330s/330ss).
- **Wrong hash on both LAN+USB rows** — contacts with a duplicated stale hash in `lan_hash` are corrected when the live LAN peer appears in Discovered.
- **Contact LAN connect** — tapping a saved contact's LAN row uses the discovered peer hash when the stored hash is outdated.

## [0.5.5] — 2026-06-27

### Fixed
- **Custom contact names** — user-saved names are never overwritten by device announce names on startup or discovery refresh (`custom_name` flag).
- **Dual-hash contact save** — saving LAN or USB merges into one contact with distinct `lan_hash` / `serial_hash`; connect uses the transport row you tapped.
- **False serial in Discovered** — LAN-only peers (e.g. GZ16) no longer appear as `(serial)` when USB is enabled on your machine; phantom serial rows are dropped on LAN beacon.
- **Own hash in contacts** — local LAN/serial hashes are filtered from Discovered and blocked when saving a contact.
- **Ip-less announce misclassification** — RNS announces without a receiving interface are rejected instead of defaulting to serial.

## [0.5.4] — 2026-06-27

### Fixed
- **Serial announce on LAN** — Announce Serial no longer shows LAN broadcast address; RNS announces go only over the configured serial port.
- **USB hot-add without restart** — Plugging in USB creates serial identity + destination at runtime and pushes discovered peers to the web UI immediately.
- **Duplicate self USB rows** — Local LAN and serial hashes are filtered from discovery (fixes seeing your own `1ae…` and `d0fdd…` as USB peers).
- **LAN identity on serial wire** — Serial announces no longer fall back to LAN destination/identity when serial endpoint was missing.
- **Session reconnect transport** — Failover reconnect respects the transport you connected on (serial session stays serial).
- **Outbound link race** — Active outbound links are no longer torn down before connect completes.
- **Beacon name flash** — Peers that briefly show as hash prefix keep a known display name when identity was seen before.

## [0.5.3] — 2026-06-27

### Fixed
- **Contacts deleted on restart** — discovery supersession no longer removes saved contacts when LAN and USB rows share a name; dual-hash contacts update `lan_hash` / `serial_hash` instead of deleting the file.
- **LAN + USB discovery eviction** — serial announces no longer remove the LAN peer row (and vice versa); both transports stay in Discovered.
- **Contact USB connect** — connect API honors `via: serial` and saved `serial_hash` instead of falling back to the LAN discovered peer.
- **USB unplug breaks peers** — contacts and links survive serial interface loss; transport-specific highlighting no longer crosses LAN/USB rows.
- **Announce Serial on refresh** — `/api/identity` includes `serial_active` so the Serial announce button shows without clicking Announce LAN first.
- **False connection failed** — UI suppresses failure toasts when a link is already established on the requested transport.

## [0.5.2] — 2026-06-27

### Fixed
- **Discovered list empty in web UI** — `renderDiscovered` referenced `isSerial` before it was defined (ReferenceError), so peers visible in the server log never rendered in the sidebar.
- **LAN + USB rows merging in UI** — `peerMergeKey` now includes transport so both discovered rows stay visible.

## [0.5.1] — 2026-06-27

### Fixed
- **Separate LAN + USB connections** — discovery stores `hash:lan` and `hash:serial` rows independently; connect API accepts `via` so serial and LAN links to the same peer no longer collide.
- **Android back navigation** — swipe-back from chat returns to the contact list first; second back minimizes the app (WebView `"true"` callback parsing fixed).
- **Transport-aware UI** — linked-peer state, connect, and chat header track per-transport links (`hash:lan` / `hash:serial`).
- **Contact name flash** — saved contacts no longer briefly show the full RNS hash when display name is missing.

## [0.5.0] — 2026-06-27

### Changed
- **Dual LAN + Serial identities** — `identity_lan` and `identity_serial`; separate connect hashes; legacy `identity` auto-migrates to `identity_lan`.
- **No transport failover** — links stay on the transport you chose (LAN or USB).
- **Discovery** — LAN and USB appear as separate rows (`name · LAN` / `name · USB`).
- **Contacts** — one card per person with LAN/USB sub-rows.
- **Announce** — sidebar **Announce LAN** and **Announce Serial** buttons.
- **Settings** — mandatory LAN IPv4 (no Auto); per-transport probe and announce intervals (0–18000 s).
- **Profile** — Regenerate LAN / Regenerate Serial (moved from System).

### Removed
- Auto interface selection; combined single announce; link failover loop.

## [0.4.2] — 2026-06-27

### Fixed
- **LAN wake on contact tap** — opening a contact or discovered peer sends HTTP wake + reconnect so sleeping Android/desktop peers accept messages without manual re-announce.
- **Stale link reconnect** — connect no longer treats zombie RNS links as healthy; unhealthy links are torn down and re-established.
- **RTT on saved contacts** — contact list shows live RTT from discovery even when the stored IP is unchanged.
- **Discovered dedup** — peers already saved as contacts are hidden from Discovered.

### Changed
- **Android APK navigation** — contact list is the main screen; tap a peer to open chat; back once returns to the list, back again backgrounds the app.

## [0.4.1] — 2026-06-27

### Fixed
- **LAN RTT in Discovered** — UDP beacon pings no longer skipped while peers are actively announcing; RTT updates on a configurable interval.
- **Android on desktop** — beacon peers appear even when RNS identity registration is still pending (hash/name/IP sufficient).

### Added
- **Settings → Network → Link ping interval** (5–300s, default 30) — controls LAN UDP and USB serial liveness pings and RTT refresh.

## [0.4.0] — 2026-06-27

### Fixed
- **Serial RNS auto-announce** no longer floods USB with 3–5 packet bursts; one announce per event, periodic serial every 30s when auto-announce is on.
- **Discovered peers UI** updates when transport (`via`), IP, or RTT changes; authoritative peer broadcasts on Announce, scope change, and probe eviction.
- **Live LAN scope drift** (OS IP or pinned interface change without restart) refreshes discovery, drops stale subnet peers, and pushes WebSocket updates automatically.
- **Manual Announce** sends a single serial RNS packet in dual-transport mode instead of 4× bursts that clogged the link.

### Changed
- Connect/failover serial priming uses one announce every 3s instead of multi-packet bursts.
- UI transient empty-peer hold reduced from 120s to 15s; authoritative updates bypass the hold entirely.

### Tests
- `tests/test_serial_announce_policy.py` — serial rate limits, periodic loop, serial discovery visibility.

## [0.3.171] — 2026-06-26

- Fastest-path (RTT) selection per peer in discovered list.
- LAN scope save refreshes discovery paths.
- LAN auto-announce and peer ping every 30s; serial had no periodic auto-announce.

## [0.3.170] — 2026-06-25

- Hide serial badge when USB unplugged; beacon upgrades to LAN.
- Scope checker accepts in-scope LAN for serial-tagged peers.
- Transport matrix tests.

[0.4.0]: https://github.com/narl3yyy-svg/chatxz/compare/v0.3.171...v0.4.0
[0.3.171]: https://github.com/narl3yyy-svg/chatxz/compare/v0.3.170...v0.3.171
[0.3.170]: https://github.com/narl3yyy-svg/chatxz/compare/v0.3.169...v0.3.170