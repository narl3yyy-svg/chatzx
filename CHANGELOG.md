# Changelog

All notable changes to chatxz are documented here. The README lists only the latest release summary.

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