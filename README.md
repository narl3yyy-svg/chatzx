# chatxz

Decentralized instant messaging powered by the [Reticulum Network Stack](https://reticulum.network/).

Send text, emoji, files of any size, images/screenshots (viewable inline), and voice notes — all encrypted by default, no servers needed.

## Features

- **End-to-end encrypted** messaging via Reticulum
- **Unlimited file sizes** — send any file over RNS Resources
- **Screenshot preview** — images display inline in supported terminals (Kitty, iTerm2, Sixel, chafa)
- **Voice notes** — record from mic and send (requires pyaudio)
- **Emoji** — full Unicode support
- **Off-grid capable** — works over LoRa, packet radio, WiFi, or the internet
- **No accounts, no servers** — your identity is your key

## Platform Support

| Platform | Status |
|----------|--------|
| Arch Linux | Supported (install script) |
| Ubuntu | Supported (install script) |
| Debian | Supported (install script) |
| macOS | Planned |
| Windows | Planned |
| iPhone | Planned |
| Android | Planned |

## Quick Start (Linux)

### Arch Linux
```bash
bash scripts/install-arch.sh
```

### Ubuntu / Debian
```bash
bash scripts/install-debian.sh
```

### Manual
```bash
pip install rns
pip install .
```

## Usage

```bash
# Start interactive mode
chatxz

# Send a one-off message
chatxz --connect <peer_hash> --send "Hello!"

# Send a file
chatxz --connect <peer_hash> --file screenshot.png

# Record and send voice
chatxz --connect <peer_hash> --voice

# Listen daemon
chatxz --daemon
```

### Interactive Commands

```
/connect <hash>   - Connect to a peer
/send <text>      - Send a message
/file <path>      - Send a file
/voice            - Record and send voice
/play <path>      - Play a voice note
/contacts         - List contacts
/add <hash:name>  - Add a contact
/myid             - Show your identity
/help             - Show help
/quit             - Exit
```

Your identity hash is shown on startup — share it with friends so they can connect to you.

## Architecture

chatxz uses [Reticulum](https://reticulum.network/) (RNS) for all networking:
- **Identities** — Ed25519/X25519 key pairs, stored locally
- **Destinations** — announced on the network as `chatxz.messages`
- **Links** — encrypted bi-directional channels between peers
- **Resources** — reliable transfer of arbitrary-size data (files, images, voice)

All data is encrypted end-to-end by Reticulum. No plaintext is ever sent.

## License

MIT
