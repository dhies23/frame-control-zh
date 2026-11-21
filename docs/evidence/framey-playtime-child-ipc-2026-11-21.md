# PlayTime 3.2.5 child IPC EOF qualification

PlayTime (`3.2.5`, store ID `66`) was the final runtime case in
`tests/decky-runtime/cases.json` still running without the shared Decky 3.3.0
child IPC EOF shutdown repair. Under published Decky 3.3.0, closing a child
IPC peer without that repair left the plugin child spinning until the loader
forced a kill after five seconds before PlayTime's `_unload` hook could run.

Only the exact managed archive with SHA-256
`10fc3beea48991c77cb02867e919b618d4cd6d5e9c6a6d0ccd5266c13214039f`
receives the shared child-only helper (`playtime-ipc-eof`). The published
`main.py` remains the exact suffix after the three-line install prefix, with
original body SHA-256
`a29c2898e40cc51c8d6559c89fc502042ca2ceafa000f6d95722311bd90d98dd`. Every
other original archive file is preserved. Missing helper payloads refuse
updates without replacing installed files, and modified archives do not inherit
the repair. Existing copies need Update or reinstall; compatibility remains
partial.

The adjacent compressed native report (`framey-playtime-child-ipc-2026-11-21.json.gz`)
binds all 63 recorded runtime source digests, read-only SQLite snapshots and
retained loader consoles. The local AMD64 probe runs published Decky 3.3.0 in
disposable containers without network, devices or host session mounts. It
verifies private API1 session writes, paused-session refusal, two-user SQLite
isolation, an RPC readback after explicit peer EOF, restart persistence for
both users, two clean parent-only loader stops (0.264 s and 0.214 s, zero
forced kills, original `Goodnight, World!` unload hook logged on both stops)
and recorded descendant cleanup. An unrelated sibling survives both stops.
Restoring the exact published backend reproduces one forced plugin kill after
the same peer EOF (5.336 s) before the original unload hook runs. The strict
primary shutdown gate is unchanged.

Fresh hosted AMD64 and ARM64 qualification is pending. Automatic MobX
running-app tracking, live Steam library integration, managed Frame execution,
VR visibility, physical controller input, scrolling and the VR keyboard remain
unverified. Headset SSH timed out, so no worn-headset check ran.
