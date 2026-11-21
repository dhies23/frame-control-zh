# Framey Mixer 0.1.0 child IPC EOF and PipeWire runtime evidence (2026-11-21)

- **Plugin**: Mixer `0.1.0` (official Decky store ID `144`, folder `decky-volume-mixer`, archive SHA-256 `4b3546170e5fbd5f9afad3b24555498ee51caac7cec9e4c86321b18cdd4b5106`)
- **Shim**: `mixer-ipc-eof` (shared Decky 3.3.0 child IPC peer-EOF shutdown repair)
- **Loader**: published Decky `v3.3.0` (`x86_64`, `PluginLoader` SHA-256 `e80d03e641d1766a33a817cb27ab8d2e0a4633685e5d27e4de948a6687526181`)
- **Machine-readable report**: [`framey-mixer-child-ipc-2026-11-21.json.gz`](framey-mixer-child-ipc-2026-11-21.json.gz) (6,899 bytes, SHA-256 `916480315e71b4ce2e3e10565b9a5087371d33d8186b65ece75b3ed78c5740d5`, 64/64 `sourceSha256` entries matched)

## Command

```sh
python3 scripts/framey-test-decky-runtime.py --case 144 --out /tmp/framey-mixer-runtime.json
```

## Results

1. **Managed install (`mixer-ipc-eof`, UID 1100, no `PULSE_SERVER` override)**:
   - Private silent PipeWire/WirePlumber/PipeWire-Pulse + D-Bus at `/run/user/1100` with null sink `framey.private-mixer` and two silent `/dev/zero` playback streams (`FrameySelected`, `FrameyUnrelated`).
   - Exact archive preservation verified: only `main.py` gets the `framey_decky_ipc` prefix and `py_modules/framey_decky_ipc.py` is added; every other archive member is byte-identical to `144.zip`.
   - `get_settings()` returned default `{'filters': [], 'boostLimit': 0}`; `get_playbacks()` discovered both streams at `100`.
   - `set_volume(selected, 50)` verified via independent `wpctl get-volume` (`0.50`) and `get_playbacks()` (`50`), while `FrameyUnrelated` stayed at `1.00`.
   - Invalid filter regex (`{'filters': ['['], 'boostLimit': 50}`) returned `False` without changing saved settings.
   - Saving `{'filters': ['^FrameyUnrelated$'], 'boostLimit': 25}` hid `FrameyUnrelated` from `get_playbacks()` and allowed `set_volume(selected, 125)` (verified via `wpctl get-volume` `1.25` and RPC `125`).
   - `reset_volumes()` reset `FrameySelected` to `1.00` (`100`) while leaving filtered-out `FrameyUnrelated` untouched at `0.60`.
   - After injecting an unexpected EOF on `/tmp/plugin_socket_*`, post-EOF API1 RPC calls (`get_settings()` and `get_playbacks()`) still succeeded.
   - Two parent-only `SIGTERM` stops exited cleanly in `0.265 s` and `0.257 s` (`0` forced `SIGKILL`s, `0` remaining descendants, `Plugin Mixer (v0.1.0) has been stopped in ...` logged, unrelated stream, sibling process and home marker preserved).
   - Restarted loader rediscovered Mixer, preserved `{'filters': ['^FrameyUnrelated$'], 'boostLimit': 25}` and filtering across restart, and verified `set_volume(selected, 80)` (`wpctl 0.80`, RPC `80`).
2. **Negative control (`--reproduce-mixer-unload-regression`, unmodified `144.zip`)**:
   - `get_settings()`, `get_playbacks()`, and `set_volume(selected, 50)` succeeded, then after injected child-IPC peer EOF, Decky `3.3.0` stalled on shutdown and sent `Sending SIGKILL!` (`1` forced kill, `5.259 s`).

## Verification boundaries

- **Verified here**: isolated native `x86_64` Decky `3.3.0` container execution against private silent PipeWire/WirePlumber streams under UID 1100 without network, host audio sockets, Bluetooth or hardware devices.
- **Not verified here**: hosted `ARM64` runtime gate run (runs in CI), real Frame headset SSH/audio execution, internal hardware audio nodes, audible output, Bluetooth playback, official UI rendering in VR, physical controller input, scrolling, VR keyboard or live on-device persistence.
