# Pause Games child IPC EOF qualification

The AMD64 runtime gate on #800 failed during unchanged Pause Games 1.0.3
shutdown: Decky forced a plugin kill after five seconds. All 62 recorded runtime
source digests matched main. Its TabMaster case passed. One unchanged local
Pause Games run stopped cleanly, so that initial failure's natural trigger is
unproven. This repair qualifies a controlled child IPC peer EOF separately.

Only the managed archive with SHA-256
`4aefdfb0ef25dea9c6d9fd037731c8b99ee8a1be966c2f7d5b0602112ef29f62`
receives the existing shared child-only helper. The published `main.py` remains
the exact suffix after a short install prefix, with original body SHA-256
`7d3036c4d6df838c93767d3c0ff98aac6e43507f4d5a53c5ed710305192f7150`.
Every other original archive file is preserved. Missing helper payloads refuse
updates without replacing installed files. Changed archives do not inherit the
repair. Existing copies need Update/reinstall; compatibility remains partial.

All 170 focused installer, transaction and compatibility tests passed, plus
7 runtime-coverage and shared IPC tests. The adjacent compressed native report
binds its recorded source files and retained consoles. The local AMD64 probe
uses published Decky 3.3.0 in disposable containers without network, devices or
host session mounts. It checks settings and exclusion writes/readback, an RPC
after explicit peer EOF, byte-preserving settings after restart, two clean
parent-only loader stops and recorded descendant cleanup. An unrelated sibling
survives both stops. Restoring the exact published backend reproduces one forced
plugin kill after the same peer EOF, before the original unload hook completes.
The strict primary shutdown gate is unchanged.

Fresh hosted architecture qualification is pending. No pause, resume,
terminate or kill RPC was called. Actual game process-control, automatic pause,
suspend integration, current managed Frame behavior, VR visibility, physical
controller input, scrolling and the VR keyboard remain unverified. Headset SSH
timed out, so no worn-headset check ran.
