# Framey Tailscale Control 0.1.3 user runtime and child IPC EOF evidence (2026-11-21)

- **Plugin**: Tailscale Control `0.1.3` (official Decky store ID `43`, folder `tailscale-control`, archive SHA-256 `06411711b27101d438339000da3d91202d682862710b02ef3ddf2462e642448a`)
- **Shims**: `tailscale-control-user-runtime` (preserve active user `XDG_RUNTIME_DIR` with `/run/user/<uid>` fallback, add active user `~/.local/bin` and `~/.nix-profile/bin` alongside `/opt/tailscale` to `PATH`, and return truthful `returncode == 0` booleans from `up()` and `down()`) and `tailscale-control-ipc-eof` (shared Decky 3.3.0 child IPC peer-EOF shutdown repair)
- **Loader**: published Decky `v3.3.0` (`x86_64`, `PluginLoader` SHA-256 `e80d03e641d1766a33a817cb27ab8d2e0a4633685e5d27e4de948a6687526181`)
- **Machine-readable report**: [`framey-tailscale-control-runtime-2026-11-21.json.gz`](framey-tailscale-control-runtime-2026-11-21.json.gz) (7,645 bytes, SHA-256 `aeea75678ec60fd701295eadeaaf4c8fabf1705ce8943d4380d5984fba2d7c26`, 65/65 `sourceSha256` entries matched)

## Command

```sh
python3 scripts/framey-test-decky-runtime.py --case 43 --out /tmp/framey-tailscale-runtime.json
```

## Results

1. **Managed install (`tailscale-control-user-runtime` + `tailscale-control-ipc-eof`, UID 1100, `--network=none`)**:
   - Private disposable stub executable installed at `~/.local/bin/tailscale` with `~/.local/bin` absent from the container's initial `PATH` (`shutil.which('tailscale', path=env['PATH']) is None`).
   - Exact archive preservation verified: only `main.py` and `py_modules/framey_decky_ipc.py` differ from `43.zip`.
   - `get_tailscale_state()`, `get_tailscale_exit_node_ip_list()` (`['100.64.0.10 (frame-private)', '100.64.0.20 (relay-private)']`), and `get_tailscale_device_status()` (`['frame-private', 'relay-private', 'phone-private']` / `['active', 'idle', 'offline']`) passed.
   - `down()` returned `True` on exit `0` and `False` on exit `1`; `up(node_ip='100.64.0.20 (relay-private)', allow_lan_access=True, custom_flags='--accept-routes', login_server='https://headscale.private.invalid')` returned `True` on exit `0` with exact CLI args and `False` on exit `1`.
   - All `9` stub invocations observed preserved `XDG_RUNTIME_DIR=/run/user/1100`.
   - After injecting an unexpected EOF on `/tmp/plugin_socket_*`, post-EOF legacy RPC readback (`get_tailscale_state()` and `get_tailscale_exit_node_ip_list()`) still succeeded.
   - Two parent-only `SIGTERM` stops exited cleanly in `0.246 s` and `0.268 s` (`0` forced `SIGKILL`s, `0` remaining descendants, `Plugin Tailscale Control (v0.1.3) has been stopped in ...` logged, sibling process and home marker untouched).
   - Restarted loader rediscovered Tailscale Control and read back live stub state and exit nodes.
2. **Negative control 1 (`--reproduce-tailscale-runtime-regression`, unmodified `43.zip` on UID 1100)**:
   - Unmodified backend failed to locate `~/.local/bin/tailscale`: `get_tailscale_state()` and `up()` returned `None`, with `0` stub invocations.
3. **Negative control 2 (`--reproduce-tailscale-unload-regression`, `tailscale-control-user-runtime` without `tailscale-control-ipc-eof`)**:
   - `get_tailscale_state()` succeeded against `~/.local/bin/tailscale`, then after injected child-IPC peer EOF, Decky `3.3.0` stalled on shutdown and sent `Sending SIGKILL!` (`1` forced kill, `5.258 s`).

## Verification boundaries

- **Verified here**: isolated native `x86_64` Decky `3.3.0` container execution under UID 1100 against a private disposable `~/.local/bin/tailscale` stub with `--network=none`.
- **Not verified here**: hosted `ARM64` runtime gate run (runs in CI), real `tailscaled` daemon or live tailnet/exit-node changes, real Frame headset SSH/RPC execution, bundled localhost QR asset, official UI rendering in VR, physical controller input, scrolling, VR keyboard or live on-device persistence.
