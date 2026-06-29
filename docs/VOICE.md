# Voice calls in chatxz

chatxz provides **duplex Opus voice calls** over encrypted Reticulum (RNS) links — the same transport used for chat messages.

## Quick start

1. Connect to a peer (LAN or USB serial).
2. Tap **📞** in the chat header.
3. The other side accepts the incoming call.
4. Speak — audio flows in both directions until someone hangs up.

Voice **notes** (🎤) are separate one-shot recordings and do not use this pipeline.

## Codec and timing

| Parameter | Value |
|-----------|-------|
| Codec | Opus (VOIP mode) |
| Sample rate | 48 kHz mono |
| Frame size | 20 ms (960 samples) |
| Bitrate | ~32 kbps |
| MIME type | `audio/opus;rate=48000;frame=20` |

μ-law and PCM are **not** used on the call path.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Signaling (voice_call.py)                                  │
│  INVITE → ACCEPT → ACTIVE → CALL_AUDIO frames → END         │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
  Desktop native        Browser WebCodecs      Android Java
  VoiceCallAudio        (fallback)             CallAudioEngine
  PyAudio + libopus     AudioEncoder/Decoder   AudioRecord +
  VoiceJitterBuffer                           MediaCodec Opus
        │                     │                     │
        └─────────────────────┴─────────────────────┘
                              │
                    RNS encrypted link
                    (__call_audio + seq + b64)
```

### Modules

| Module | Role |
|--------|------|
| `voice_call.py` | Call state machine, signaling packet types, MTU-safe frame splitting |
| `opus_native.py` | ctypes bindings to system libopus (encode/decode) |
| `voice_jitter_buffer.py` | Adaptive playout buffer with PLC |
| `call_audio_engine.py` | Desktop `VoiceCallAudio` — PyAudio capture/playback |
| `android_call_audio.py` | Python ↔ Java bridge on Android |
| `messaging.py` | Routes `CALL_*` packets and assigns audio sequence numbers |

### Jitter buffer

The receive path buffers 2–12 frames (40–240 ms) before playout. Delay adapts to inter-arrival jitter. Out-of-order packets are reordered by sequence number before playout. Missing frames use packet-loss concealment (attenuated repeat of the last good frame — similar in spirit to Discord’s concealment, without their full NetEQ stack).

## Platform setup

### Linux (Arch / Ubuntu)

```bash
# Arch
sudo pacman -S opus portaudio python-pyaudio

# Ubuntu / Debian
sudo apt install libopus0 portaudio19-dev python3-pyaudio

./run.sh web --share
```

Native audio starts automatically when libopus and PyAudio are available. Otherwise the browser WebCodecs Opus fallback is used.

### Windows

```cmd
run.bat web --share
```

Install PyAudio via pip (run.bat does this). Install [libopus](https://opus-codec.org/) if native audio is unavailable.

### macOS

```bash
brew install opus portaudio
./run.sh web --share
```

### Android

Rebuild/install the APK (API 29+). On call accept, `CallAudioEngine.java` captures and plays Opus natively — the WebView mic is not used for calls. Tap **🔈/🔊** on the call dashboard for speakerphone.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| No audio either direction | Server logs for `[call] Opus out` / `[call] Audio in` |
| `mic peak 0` on Linux | v0.8.4+ skips Pulse `.monitor` defaults; check `[call-audio] PulseAudio default source` is `alsa_input…` not `alsa_output…monitor` |
| Ctrl+C ignored during call | Update to v0.8.5+; self-pipe SIGINT + forced exit if loop hangs |
| Dashboard shows 0 audio sent/received | v0.8.4+ pushes `call_stats` over WebSocket; confirm WS connected |
| `Native unavailable` on desktop | `libopus` installed? `./run.sh install` for PyAudio |
| Garbled audio | Confirm logs show `Opus` not `pcmulaw` (old client); update both peers to v0.8.3+ |
| Jitter stuck at 0–20 ms | Update to v0.8.0+; buffer should hold 40–120 ms |
| Android silent (receive) | Rebuild APK v0.8.3+ (Opus CSD + MediaCodec fix); API 29+ |
| Android silent (send) | Microphone permission; check logcat `CallAudioEngine` |

## Logs to expect (healthy call)

```
[call] Outgoing to abc123... (call-id)
[call] Accepted by abc123...
[call-audio] Voice engine started (duplex, Opus 48 kHz, 20 ms)
[call-audio] Opus out #1 (… b64, … B)
[call] Audio in #1 (…) ← abc123...
```