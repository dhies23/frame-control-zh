# Games Prefix Manager 1.0.1 child IPC EOF qualification

The live official store archive for Games Prefix Manager (`1.0.1`) had the
`prefix-manager-user-paths` adaptation but lacked the shared child IPC EOF
shutdown repair that the separate `1.0.2` preflight already received. Under
published Decky 3.3.0, closing a child IPC peer without that repair left the
plugin child spinning until the loader forced a kill after five seconds.

Only the exact managed archive with SHA-256
`ef8c727943188cad04c63fae7e4e620b8080c07f35dec0c144e99c6166307473`
receives the shared child-only helper (`prefix-manager-101-ipc-eof`). The
prior managed `main.py` body remains the exact suffix after the three-line
install prefix, with body SHA-256
`5118a1aeae00b0979d501e8e5cdb6c27bf27cae1f3cf5af581f0fb44345519a1`. Every
other non-adapted archive file is preserved. Missing helper payloads refuse
updates without changing installed files, and modified archives do not inherit
the repair. Existing copies need Update or reinstall; compatibility remains
partial.

The adjacent compressed native report (`framey-prefix-manager-101-child-ipc-2026-11-21.json.gz`)
binds all 63 recorded runtime source digests and retained loader consoles. The
local AMD64 probe runs published Decky 3.3.0 in disposable containers without
network, devices or host session mounts. It verifies disposable multi-library
discovery, backup/delete, two-user and malformed/unreadable shortcut bulk-cleanup
refusals, duplicate-library/symlink/traversal refusals, an RPC after explicit
peer EOF, preserved backup bytes across restart, two clean parent-only loader
stops (0.214 s and 0.264 s, zero forced kills) and recorded descendant cleanup.
An unrelated sibling survives both stops. Restoring the prior managed 1.0.1
backend reproduces one forced plugin kill after the same peer EOF (5.275 s).
The `1.0.2` preflight positive (0.264 s / 0.264 s) and negative (5.330 s, one
forced kill) controls also pass unchanged. The strict primary shutdown gate is
unchanged.

Fresh hosted AMD64 and ARM64 qualification is pending. Real Steam prefix
changes, complete non-Steam shortcut coverage, managed Frame backend execution,
VR visibility, physical controller input, scrolling and the VR keyboard remain
unverified. Headset SSH timed out, so no worn-headset check ran.
