# Changelog

All notable changes to chatxz are documented here. The README lists only the latest release summary.

## [0.9.1] — 2026-06-28

### Fixed
- **Ctrl+C on Arch** — schedule `os._exit` before ALSA cleanup; non-blocking `stop_fast()` so PortAudio teardown cannot hang shutdown.
- **Incoming audio** — playback/decoding starts before mic probe; pending frame buffer; engine starts in background thread.
- **Post-hangup audio spam** — clear pending frames; ignore `CALL_AUDIO` during shutdown; hang up when last WebSocket disconnects.
- **Arch ALSA mic** — `amixer` unmute; prefer `default` device when PulseAudio unavailable; limit hot-swap to 2 attempts.

### Changed
- **Desktop call UI** — incoming calls show in sidebar strip (accept/decline), not fullscreen overlay; active calls stay in sidebar.

## [0.9.0] — 2026-06-28

### Changed
- **Voice system rewrite** — deleted legacy `call_audio_engine.py`, `voice_call.py`, `voice_jitter_buffer.py`, `android_call_audio.py`; all implementations now live under `chatxz/core/audio/` (`session`, `opus`, `jitter`, `devices`, `engine`, `android`).
- **Desktop engine** — dedicated capture and playback threads (not PortAudio callbacks) for reliable stop, hang-up, and Ctrl+C during active calls.

### Fixed
- **Linux microphone** — `pactl` fixes monitor-default sources; prefers Alt Analog; hot-swaps ranked devices after ~3 s silent capture.
- **Ctrl+C during calls** — stops audio, sends `CALL_END`, fast `os._exit` instead of hanging in `runner.cleanup()`.
- **Bidirectional hang-up** — engine stopped before `CALL_END`; remote receives `call_ended` WebSocket event.

## [0.8.7] — 2026-06-28

### Fixed
- **Hang up both sides** — stop native audio before sending `CALL_END`; remote `CALL_END` resets session and stops engine via WebSocket `call_ended`.
- **Post-hangup log spam** — no more per-frame `[call] No link` during audio send; auto-end call after 5 link failures.
- **Ctrl+C hang** — forced `os._exit` timer no longer cancelled in `finally` while `runner.cleanup()` blocks.

### Changed
- **`chatxz.core.audio`** — consolidated voice module exports (session, engine, jitter).

## [0.8.6] — 2026-06-28

### Fixed
- **Ctrl+C during calls (Linux)** — `signalfd` dedicated thread captures SIGINT even when PortAudio/RNS worker threads are active; fixes v0.8.5 self-pipe missing signals on Arch.
- **Hang up button** — header call button always hangs up (was opening dashboard when minimized).
- **False “no audio” warning** — dashboard respects native server `call_stats` over WebSocket.
- **Arch mic capture** — prefer `Alt Analog` ALSA input; probe top devices at call start.

## [0.8.5] — 2026-06-28

### Fixed
- **Ctrl+C during calls (Linux)** — self-pipe SIGINT delivery wakes the event loop reliably; forced `os._exit` after 0.8s if graceful stop hangs; handlers re-armed every second during active calls.
- **Linux mic capture** — prefer PyAudio `default` / `pipewire` / `alt analog` over raw `hw:0,0` ALSA nodes (fixes `mic peak 0` on some Arch setups).

## [0.8.4] — 2026-06-28

### Fixed
- **Ctrl+C during calls** — stop native call audio before teardown; re-arm SIGINT after RNS startup (Reticulum overwrote handlers).
- **Call dashboard stats** — WebSocket `call_stats` push every second during active calls; status poll merges server native counters.
- **Linux mic selection** — reject PulseAudio `.monitor` default sources; pick real `alsa_input` capture device.
- **Desktop call UX** — active call shown in sidebar strip instead of auto-opening fullscreen overlay.
- **Android call UX** — in-chat call bar (stay in chat); proximity wake lock dims screen at ear, wakes when away.

## [0.8.3] — 2026-06-28

### Fixed
- **Jitter buffer** — playout starts at lowest received seq (out-of-order safe); PLC uses attenuated frame repeat.
- **Android MediaCodec** — synchronized decode with input-seq queue; gap PLC on playback thread.
- **Browser fallback** — seq-ordered jitter before ring buffer (matches native/desktop path).

## [0.8.2] — 2026-06-28

### Fixed
- **Desktop mic capture** — PulseAudio-aware input/output device scoring; logs devices; detects silent capture and falls back to browser Opus.
- **Android Opus playback** — MediaCodec decoder CSD, proper buffer dequeue loops, sequence jitter buffer; fixes silent Android receive path.
- **Call hang-up** — local state resets before signaling; Android audio focus/speakerphone cleared on end.
- **Android UX** — keyboard stays open after send; speakerphone toggle on call dashboard.

## [0.8.1] — 2026-06-28

### Changed
- **Voice code cleanup** — removed all μ-law/PCM call paths, dead browser helpers, and legacy aiortc shims.
- **Consolidated modules** — `android_call_audio` moved to `core/`; MTU helpers renamed for Opus in `voice_call.py`.
- **Documentation** — added `docs/VOICE.md` with architecture, platform setup, and troubleshooting.

## [0.8.0] — 2026-06-28

### Changed
- **Opus-only voice calls** — all platforms use Opus 48 kHz / 20 ms frames end-to-end; μ-law removed from call path.
- **Custom libopus engine** — desktop uses ctypes libopus + PyAudio callbacks (no aiortc/WebRTC).
- **Adaptive jitter buffer** — playout delay adapts to network jitter with PLC; fixes 0–20 ms buffer flicker and garbled audio.
- **Android native audio** — `CallAudioEngine` (AudioRecord + MediaCodec Opus + AudioTrack) replaces broken browser μ-law fallback.

### Fixed
- Codec mismatch garbling when browser sent μ-law and native expected Opus (or padded μ-law incorrectly to 48 kHz).
- Android `[call-audio] Native unavailable` — Java Opus engine starts on call accept.

## [0.7.7] — 2026-06-28

### Fixed
- **Call window closes instantly** — simultaneous invites (glare) resolve by call-id tie-break; loser auto-accepts, winner keeps outgoing.
- **“Peer is busy” after failed calls** — stale call state times out and clears; empty `call_id` on end/reject no longer kills unrelated calls.
- **Post-call disconnect** — link-closed handler checks all transports/aliases before resetting call UI.
- **Stuttery browser audio** — 20 ms μ-law frames with paced send and larger jitter buffer.
- **Arch native mic silent** — skip monitor/loopback default devices; browser mic fallback when native sends zero frames after 4 s.
- **Server audio fallback** — WebSocket `call_audio` accepted when native engine is running but not transmitting.

## [0.7.6] — 2026-06-28

### Fixed
- **Silent calls on all platforms** — reverted browser send to μ-law (v0.7.4 path); WebCodecs Opus encoder produced no output on desktop/Android WebView.
- **Opus receive preserved** — browsers still decode incoming Opus from native peers.
- **Native audio send** — lower RMS gate (48), allow small Opus frames, log mic peak and native out frames.
- **Ubuntu pyaudio** — `run.sh` auto-recreates `.venv` with `--system-site-packages` when apt `python3-pyaudio` exists.
- **Call UI** — microphone status pill and no-audio warning (secure-context hint on desktop).

## [0.7.5] — 2026-06-28

### Fixed
- **Codec mismatch on LAN calls** — desktop browsers (Ubuntu/Windows) use WebCodecs Opus again, matching Android and native audio; μ-law is fallback only.
- **One-way audio** — Opus decoder initializes for receive even when the browser sends μ-law, so native Opus from Arch plays correctly.
- **Disconnected after hang-up** — duplicate RNS link teardown no longer clears the connected state while the peer link stays active.
- **Ubuntu native audio** — `.venv` uses `--system-site-packages` so apt `python3-pyaudio` is visible after `pip` build fails.

## [0.7.4] — 2026-06-28

### Fixed
- **Web UI blank/broken after v0.7.3** — duplicate `let transferTrack` declaration caused a JavaScript parse error that prevented the entire page from loading.

## [0.7.3] — 2026-06-28

### Fixed
- **Silent native call playback** — Opus decode used removed PyAV `to_bytes()` API; playback now works on Arch/Linux native audio.
- **Browser call audio on desktop** — Ubuntu/Windows/Arch browsers use proven μ-law LAN frames again; Android keeps WebCodecs Opus.
- **Headless / no-mic servers** — native engine runs receive-only (no silence packet spam); RMS gate skips encoding silence.
- **Connection status UI** — superseded peer hashes register aliases; link header checks all transports.
- **Dual file transfers** — active transfer list shows each file on its own row (fixes blurred Android dock).
- **`run.bat`** — installs pyaudio/aiortc for native call audio on Windows.

## [0.7.2] — 2026-06-28

### Fixed
- **`run.sh` on Ubuntu/Debian** — uses a project-local `.venv` instead of `pip install --user` into the PEP 668 externally-managed system Python (fixes “externally-managed-environment” on `./run.sh web`).

## [0.7.1] — 2026-06-28

### Fixed
- **Zero call audio packets** — browser no longer skips the microphone when native Opus is advertised but not actually running (missing PyAudio device, failed engine start, or deps not installed). Falls back to WebCodecs Opus or μ-law with live packet counters.
- **`run.sh`** — first-run install now includes `pyaudio` and `aiortc` for native call audio.

## [0.7.0] — 2026-06-28

### Added
- **Opus call audio (aiortc + PyAudio)** — native 48 kHz / 20 ms Opus frames on desktop with jitter buffer, PLC, and system mic/speaker via PyAudio callbacks.
- **WebCodecs Opus** in the browser when native audio is unavailable (Android / fallback).
- **Android incoming-call notification** — high-priority notification channel in addition to vibration.

### Changed
- LAN voice codec default is Opus (`audio/opus;rate=48000;frame=20`); μ-law remains for USB serial and legacy peers.

## [0.6.7] — 2026-06-28

### Fixed
- **Audio still dropped on Ubuntu** — server splits oversized call-audio frames to fit RNS MTU (fixes stale browser cache sending 640-byte μ-law blobs).
- **Call UI** — styled Call / Hang up header buttons; live call dashboard with RTT, transport, audio in/out, jitter buffer, and codec stats.

## [0.6.6] — 2026-06-28

### Fixed
- **Silent calls after v0.6.5** — 640-byte μ-law frames exceeded RNS MTU (1054 B > 1016 B budget); LAN frames reduced to 480 samples (30 ms) so packets are delivered.

## [0.6.5] — 2026-06-28

### Fixed
- **Garbled / stuttery call audio** — μ-law compression (40 ms LAN / 20 ms serial frames), gapless ring-buffer playback via ScriptProcessor, 60 ms jitter buffer, paced send (one frame per mic tick), and 16 kHz AudioContext alignment.

## [0.6.4] — 2026-06-28

### Fixed
- **Silent calls (no audio)** — LAN PCM frames (854 b64 chars) were dropped by a 720-char client cap; frames resized to 240 samples and the cap removed.
- **Capture throughput** — mic buffer is fully drained each processing tick instead of one frame per callback.

## [0.6.3] — 2026-06-27

### Fixed
- **Garbled / laggy call audio** — linear interpolation resampling replaces aliased nearest-neighbor downsampling; LAN calls use 16 kHz PCM, USB serial stays 8 kHz.
- **Playback lag** — batches 2 frames per play cycle, caps queue at 3–4 frames, and recovers from buffer underruns; explicit little-endian PCM encode/decode.

## [0.6.2] — 2026-06-27

### Added
- **Incoming call ringtone** — dual-tone ring in the web UI; vibration pattern on Android.

### Fixed
- **Android microphone busy** — single shared mic stream with retries, audio-focus handling, and proper release between calls and voice notes.
- **Call audio** — WebSocket `call_audio` frames (lower latency than HTTP), 64-sample serial / 128-sample LAN PCM frames, 100 ms playback jitter buffer.

## [0.6.1] — 2026-06-27

### Fixed
- **Contact delete** — removes related LAN and USB JSON files together; deleted peers are blocklisted so discovery no longer resurrects serial-only `835…` ghosts.
- **Contact names** — saved labels persist when peers are disconnected; discovery sync no longer overwrites custom names.
- **Voice calls** — PCM frames (8 kHz) replace WebM chunks that were dropped or unplayable; mic is released between calls and voice notes (Android “microphone busy”).

## [0.6.0] — 2026-06-27

### Added
- **Live voice calls over RNS** — duplex audio on an active encrypted link (LAN or USB serial); signaling via `__call_*` packets, audio streamed in MTU-sized frames.
- **Web UI** — 📞 button in the chat header when linked; incoming-call modal; in-call timer and hang-up bar.
- **`POST /api/call`** — invite, accept, reject, end, audio, and status actions; WebSocket events `call_incoming`, `call_accepted`, `call_audio`, etc.

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