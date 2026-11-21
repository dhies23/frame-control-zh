# Framey DeckPass 1.1.1 User Runtime, CLI Safety and Clean Unload Evidence (2026-11-21)

- **Plugin**: `121` — DeckPass `1.1.1` (`DeckPass`, API 1)
- **Pinned archive SHA-256**: `4be32fd39ce406e2276b21537c98afc00a8ed1b82a23ff0e01af552352fa48f8` (`tests/fixtures/framey-official/121.zip`)
- **Decky runtime**: published `v3.3.0` (`x86_64`, loader SHA-256 `e80d03e641d1766a33a817cb27ab8d2e0a4633685e5d27e4de948a6687526181`) in an isolated UID `1100` container (`--network=none`)
- **Raw archive**: [framey-deckpass-runtime-2026-11-21.json.gz](framey-deckpass-runtime-2026-11-21.json.gz) (`69/69` `sourceSha256` entries matched)

## What changed for exact DeckPass 1.1.1
1. **`deckpass-user-runtime`**:
   - Preserves `os.environ` (`HOME`, `PATH`, `XDG_RUNTIME_DIR`, `XDG_DATA_HOME`, `DBUS_SESSION_BUS_ADDRESS`) while clearing `LD_LIBRARY_PATH` in `KeypassCli._get_env_variables()` and `KeypassFlatpak.install()` so `--user` Flatpak discovery works under an unprivileged Steam Frame user.
   - Initializes `KeypassCli.process = None` so closing or unloading DeckPass while idle does not raise `AttributeError: 'KeypassCli' object has no attribute 'process'`.
   - Rejects empty security tokens in `PasswordManager._validate_security_token()` instead of allowing `hmac.compare_digest("", "")`.
   - Escapes backslashes before quotes and rejects `\n`, `\r`, and `\x00` in `utils.escape_cli_input()`, `KeypassCli._send()`, and `KeypassCli.create_database()` to block interactive `keepassxc-cli` command injection.
   - Unpacks `stdout, _stderr = await result.communicate()` in `KeypassCli.create_database()` instead of calling `.decode()` on a tuple.
   - Adds `KeypassCli.aclose()` / `PasswordManager.aclose()` and awaits it in `Plugin.close_password_manager()` (and `_unload()`) so active `keepassxc-cli` subprocesses are terminated and reaped cleanly.
2. **`deckpass-ipc-eof`**:
   - Installs the shared Decky 3.3.0 child-IPC EOF repair (`py_modules/framey_decky_ipc.py`) so closing the parent IPC socket does not stall plugin shutdown until Decky's 5-second `SIGKILL`.
3. **Preservation**:
   - All other files in the published 1.1.1 archive (`dist/*`, `plugin.json`, `package.json`, `LICENSE`, `README.md`, `py_modules/.keep`) remain byte-for-byte unchanged.

## Verification results
### Adapted positive probe (`--case 121`)
- Private `~/.local/bin/flatpak` stub ran under UID `1100` (`HOME=/home/tester`, `XDG_RUNTIME_DIR=/run/user/1100`, `--network=none`).
- `check_setup_state()` returned `[True, None, '/home/tester']` with preserved user `HOME`/`XDG_RUNTIME_DIR` and `LD_LIBRARY_PATH=''`.
- Empty security token `get_entries('')` was rejected before opening any vault.
- `create_database()` failed cleanly on the stub error path (without tuple `.decode()` crash) and succeeded on `/home/tester/private-vault.kdbx`; `set_setting('databasePath', ...)` persisted across restart.
- `open_password_manager()` launched stub `keepassxc-cli` (PID `33`, `uid=1100`, `HOME=/home/tester`, `XDG_RUNTIME_DIR=/run/user/1100`, `LD_LIBRARY_PATH=''`), round-tripped `create_entry`, `get_entries`, `get_entry_details`, `generate_random_password`, `edit_entry`, and rejected newline injection (`Frame Renamed\nrm x`) while cleanly sending `SIGTERM` to PID `33`.
- Re-opening (PID `35`), running `remove_entry`, leaving the vault open across an IPC peer EOF disconnect + post-EOF `check_setup_state()` RPC, and stopping Decky ran `_unload()`, sent `SIGTERM` to PID `35`, and finished in `0.248 s` with `0` forced `SIGKILL`s and `0` surviving descendants.
- Restarting Decky retained `databasePath` (`/home/tester/private-vault.kdbx`) and completed an idle stop in `0.265 s` with `0` forced `SIGKILL`s and no `Failed to close password manager!` error.

### Negative control 1 (`--reproduce-deckpass-runtime-regression`)
- Restoring the unmodified DeckPass 1.1.1 Python files reproduced `check_setup_state()` failing (`Failed to check setup state!`) because `_get_env_variables()` wiped `PATH`/`HOME`, and reproduced `close_password_manager()` logging `Failed to close password manager!` while idle due to missing `KeypassCli.process`.

### Negative control 2 (`--reproduce-deckpass-ipc-regression`)
- Running `deckpass-user-runtime` without `deckpass-ipc-eof` reproduced `1` forced `SIGKILL` in `5.258 s` after IPC peer EOF, skipping `_unload` (`unloadedHookRan: False`) without sending `SIGTERM` to the active vault child.

## Unverified scope
- Real KeePassXC Flatpak installation, live `.kdbx` vaults on ARM64 Steam Frame, clipboard integration, and worn-headset VR UI were not tested.
- Compatibility status remains `partial`.
