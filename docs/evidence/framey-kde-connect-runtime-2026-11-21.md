# Framey KDE Connect 0.1.0-1 User Runtime and Clean Unload Evidence (2026-11-21)

- **Plugin**: `74` — KDE Connect `0.1.0-1` (`kdeconnect`, legacy API)
- **Pinned archive SHA-256**: `5f7decb4de48290f8eec15937486c9611aec0d25256515c97f98cf7ba30c79a4` (`tests/fixtures/framey-official/74.zip`)
- **Decky runtime**: published `v3.3.0` (`x86_64`, loader SHA-256 `e80d03e641d1766a33a817cb27ab8d2e0a4633685e5d27e4de948a6687526181`) in an isolated UID `1100` container (`--network=none`)
- **Raw archive**: [framey-kde-connect-runtime-2026-11-21.json.gz](framey-kde-connect-runtime-2026-11-21.json.gz) (`68/68` `sourceSha256` entries matched)

## What changed for exact KDE Connect 0.1.0-1
1. **`kde-connect-user-runtime`**:
   - Removes the unused `from click import get_app_dir` import and the unused, never-closed module-level `std_out_file`/`std_err_file` log handles in `kdeconnect/main.py`.
   - Replaces the non-executable (`0o644`) `kdeconnect/run_kde_connect.sh` (which hard-coded `sudo -u deck`, `HOME=/home/deck`, `XDG_RUNTIME_DIR=/run/user/1000`, and `dbus-launch`) with an executable (`0o700`) unprivileged script that preserves the active user's `HOME`, `XDG_RUNTIME_DIR` (falling back to `/run/user/$(id -u)`), `SHELL`, and `DBUS_SESSION_BUS_ADDRESS`, locates `kdeconnectd` via `PATH` or standard libexec directories, and uses `exec "$kdeconnectd_bin" --replace`.
   - Tracks only the plugin's own spawned daemon in `Plugin._proc`, drops the stray `"&"` argument and unread stdout/stderr pipes, replaces blind `pkill kdeconnectd` with bounded termination/reaping of `Plugin._proc`, removes the 10-second blind boot delay in `kdeconnect_runner`, and adds `Plugin._unload()` to cancel `_runner_task` and terminate/reap `Plugin._proc` cleanly.
2. **`kde-connect-ipc-eof`**:
   - Installs the shared Decky 3.3.0 child-IPC EOF repair (`py_modules/framey_decky_ipc.py`) so closing the parent IPC socket does not stall plugin shutdown until Decky's 5-second `SIGKILL`.
3. **Preservation**:
   - All other files in the published 0.1.0-1 archive (`dist/index.js`, `plugin.json`, `package.json`, `LICENSE`, `README.md`) remain byte-for-byte unchanged.

## Verification results
### Adapted positive probe (`--case 74`)
- Private `~/.local/bin/xprop` and `~/.local/bin/kdeconnectd` stubs ran under UID `1100` (`HOME=/home/tester`, `XDG_RUNTIME_DIR=/run/user/1100`, `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1100/bus`, `--network=none`).
- Initial `is_enabled()` returned `False` with zero daemon launches.
- Enabling with Steam UI focused (`769`) launched stub `kdeconnectd` (PID `24`) with `args=['--replace']`, `DISPLAY=':0'`, `HOME='/home/tester'`, `XDG_RUNTIME_DIR='/run/user/1100'`, `DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/1100/bus'`, `uid=1100`.
- Switching focus to a game (`12345`) cleanly stopped PID `24` via `SIGTERM` and launched PID `27` on `DISPLAY=':1'`.
- Calling `set_enabled(enabled=False)` cleanly stopped PID `27` via `SIGTERM`.
- Re-enabling launched PID `30`, then stopping Decky after an IPC peer EOF disconnect ran post-EOF `is_enabled() -> True`, executed `_unload`, stopped PID `30` via `SIGTERM`, and finished in `0.265 s` with `0` forced `SIGKILL`s and `0` surviving descendants.
- Restarting Decky reported `is_enabled() -> False` and completed a second strict parent-only stop in `0.236 s` with `0` forced `SIGKILL`s.

### Negative control 1 (`--reproduce-kde-connect-runtime-regression`)
- Restoring the unmodified `main.py` and `run_kde_connect.sh` (`0o644`) reproduced `PermissionError: [Errno 13] Permission denied` on `run_kde_connect.sh` with zero daemon launches and logged `Could not find "_unload" in KDE Connect (v0.1.0-1)'s main.py` on shutdown.

### Negative control 2 (`--reproduce-kde-connect-ipc-regression`)
- Running `kde-connect-user-runtime` without `kde-connect-ipc-eof` reproduced `1` forced `SIGKILL` in `7.280 s` after IPC peer EOF, skipping `_unload` and leaving stub `kdeconnectd` PID `30` orphaned under PID `1`.

## Unverified scope
- Live KDE Connect daemon installation, phone/device pairing, clipboard/file transfers, real Gamescope X11/D-Bus on ARM64 Steam Frame, and worn-headset VR UI were not tested.
- Compatibility status remains `partial`.
