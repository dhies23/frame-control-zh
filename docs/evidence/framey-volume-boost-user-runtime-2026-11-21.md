# Framey Volume Boost 0.0.7 user runtime and child IPC EOF evidence (2026-11-21)

- **Plugin**: Volume Boost `0.0.7` (official Decky store ID `58`, folder `volume-boost`, archive SHA-256 `2cc576a7f60751306d690268e227df626e59d3733680ced753de4c116629a9cc`)
- **Shims**: `volume-boost-user-audio` (preserve actual user `XDG_RUNTIME_DIR`, falling back to `/run/user/<uid>` instead of hard-coded `/run/user/1000`) and `volume-boost-ipc-eof` (shared Decky 3.3.0 child IPC peer-EOF shutdown repair)
- **Loader**: published Decky `v3.3.0` (`x86_64`, `PluginLoader` SHA-256 `e80d03e641d1766a33a817cb27ab8d2e0a4633685e5d27e4de948a6687526181`)
- **Machine-readable report**: [`framey-volume-boost-user-runtime-2026-11-21.json.gz`](framey-volume-boost-user-runtime-2026-11-21.json.gz) (7,247 bytes, SHA-256 `1b4a46643e8076a672cc2ae55f465596a8bb4c4f5b4123f18c2539284f2762b9`, 64/64 `sourceSha256` entries matched)

## Command

```sh
python3 scripts/framey-test-decky-runtime.py --case 58 --out /tmp/framey-volume-boost-runtime-v2.json
```

## Results

1. **Managed install (`volume-boost-user-audio` + `volume-boost-ipc-eof`, UID 1100, no `PULSE_SERVER` override)**:
   - Private silent PipeWire/WirePlumber/PipeWire-Pulse + D-Bus at `/run/user/1100` with two stereo null sinks (`framey.private-volume-boost` as default sink with a silent `/dev/zero` playback stream, and `framey.private-unrelated`).
   - Exact archive preservation verified: only `main.py` and `py_modules/framey_decky_ipc.py` differ from `58.zip`.
   - 5 linked and separate-channel mutations (`125%`, `150%`, left `85%`, right `135%`, re-linked `100%`) passed both legacy RPC readback and independent `pactl get-sink-volume` readback; `framey.private-unrelated` remained `(100, 100)`.
   - After injecting an unexpected EOF on the plugin's private `/tmp/plugin_socket_*` Unix peer, post-EOF legacy RPC readback still returned `{'left': 100, 'right': 100}`.
   - Two parent-only `SIGTERM` stops exited cleanly in `0.247 s` and `0.247 s` (`0` forced `SIGKILL`s, `0` remaining descendants, `Plugin Volume Boost (v0.0.7) has been stopped in ...` logged, unrelated sink, sibling process and home marker untouched).
   - Restarted loader rediscovered Volume Boost and applied/read back `[115, 115]`.
2. **Negative control 1 (`--reproduce-volume-boost-user-regression`, unmodified `58.zip` on UID 1100)**:
   - Original hard-coded `os.environ['XDG_RUNTIME_DIR'] = '/run/user/1000'` broke Pulse discovery under UID 1100: `get_volume_state` returned fallback `0` for both channels, `set_volume_state(125, 'both')` returned `False`, and both null sinks stayed `(100, 100)`.
3. **Negative control 2 (`--reproduce-volume-boost-unload-regression`, `volume-boost-user-audio` without `volume-boost-ipc-eof`)**:
   - `set_volume_state(125, 'both')` succeeded on UID 1100, then after injected child-IPC peer EOF, Decky `3.3.0` stalled on shutdown and sent `Sending SIGKILL!` (`1` forced kill, `5.275 s`).

## Verification boundaries

- **Verified here**: isolated native `x86_64` Decky `3.3.0` container execution against private silent PipeWire/Pulse null sinks under UID 1100 without network, host audio sockets, Bluetooth or hardware devices.
- **Not verified here**: hosted `ARM64` runtime gate run (runs in CI), real Frame headset SSH/audio execution, audible output, Bluetooth sinks, official UI rendering in VR, physical controller input, scrolling, VR keyboard or live on-device persistence.
