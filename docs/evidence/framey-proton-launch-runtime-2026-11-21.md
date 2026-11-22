# Framey Decky Proton Launch 0.9.0 Safe Profiles, Private Logs and Clean Unload Evidence (2026-11-21)

- **Plugin**: `137` — Decky Proton Launch `0.9.0` (`decky-proton-launch`, API 1)
- **Pinned archive SHA-256**: `e7b98a7ca8817ef08584ed0828eee8702631af33daccc229075a012eca91f731` (`tests/fixtures/framey-official/137.zip`)
- **Decky runtime**: published `v3.3.0` (`x86_64`, loader SHA-256 `e80d03e641d1766a33a817cb27ab8d2e0a4633685e5d27e4de948a6687526181`) in an isolated UID `1100` container (`--network=none`)
- **Raw archive**: [framey-proton-launch-runtime-2026-11-21.json.gz](framey-proton-launch-runtime-2026-11-21.json.gz) (`70/70` `sourceSha256` entries matched)

## What changed for exact Decky Proton Launch 0.9.0
1. **`proton-launch-safe-profiles`**:
   - Routes wrapper logs in `install_script()`, `get_launch_log()`, and `get_last_launched_appid()` to private `~/.config/decky-proton-launch/proton-launch.log` (`0o700` directory, refusing symlinks) instead of shared `/tmp/proton-launch.log`.
   - Validates positive integer `app_id`, shell-identifier variable names (`^[A-Za-z_][A-Za-z0-9_]*$`), and single-line values without `\n`, `\r`, or `\x00` in `profile.py`, writing profiles with owner-only `0o600` permissions and refusing symlinks on read/write/delete.
   - Refuses to overwrite a symlink or an existing unowned `~/proton-launch` script (missing `# decky-proton-launch`), sets wrapper permissions to `0o700`, and requires numeric `SteamAppId` + non-symlink profile inside `~/proton-launch` before `source`.
2. **`proton-launch-ipc-eof`**:
   - Installs the shared Decky 3.3.0 child-IPC EOF repair (`py_modules/framey_decky_ipc.py`) so closing the parent IPC socket does not stall plugin shutdown until Decky's 5-second `SIGKILL`.
3. **Preservation**:
   - All other files in the published 0.9.0 archive (`dist/*`, `plugin.json`, `package.json`, `LICENSE`, `README.md`, and untouched `py_modules/proton_launch/*.py` modules) remain byte-for-byte unchanged.

## Verification results
### Adapted positive probe (`--case 137`)
- Under UID `1100` (`HOME=/home/tester`, `XDG_RUNTIME_DIR=/run/user/1100`, `--network=none`):
  - `ping()` returned `'pong'`.
  - Pre-existing unowned `~/proton-launch` caused `install_script()` to return `False` and stayed byte-for-byte untouched.
  - Managed `install_script()` installed owner-only `~/proton-launch` (`0o700`) with `is_script_installed() == 'current'` and zero references to `/tmp/proton-launch.log`.
  - Shell-injection key `'BAD=1; echo INJECTED #'` and multi-line value `'line1\nline2'` were rejected (`set_game_profile(...) -> False`); valid profile (`PROTON_FSR4_UPGRADE=1`, `MANGOHUD_CONFIG="fps_limit=60,a'b"`) persisted with mode `0o600` and round-tripped verbatim.
  - Executing `~/proton-launch` with `SteamAppId=440` exported both variables intact, wrote only to private `~/.config/decky-proton-launch/proton-launch.log` (`/tmp/proton-launch.log` absent), and `get_last_launched_appid()` / `get_launch_log(440)` read `440` and `APPLYING profile for appid=440`.
  - Executing `~/proton-launch` with `SteamAppId=../profiles/440` refused traversal and exported neither variable.
  - Post-EOF `ping()` returned `'pong'`; Stop #1 finished in `0.309 s` (`0` forced `SIGKILL`s, `0` surviving descendants); restart retained profile and last-launched app ID; Stop #2 finished in `0.318 s` (`0` forced `SIGKILL`s).

### Negative control 1 (`--reproduce-proton-launch-safety-regression`)
- Restoring unmodified `profile.py` and `plugin.py` reproduced silent overwrite of an unowned `~/proton-launch` script, arbitrary shell command execution (`INJECTED` printed via profile key), and writes to shared `/tmp/proton-launch.log`.

### Negative control 2 (`--reproduce-proton-launch-ipc-regression`)
- Running `proton-launch-safe-profiles` without `proton-launch-ipc-eof` reproduced `1` forced `SIGKILL` in `5.275 s` after IPC peer EOF.

## Unverified scope
- Live Steam `localconfig.vdf` / `shortcuts.vdf` edits, real Proton game launches on ARM64 Steam Frame, and worn-headset VR UI were not tested.
- Compatibility status remains `partial`.
