# Deckhand 0.1.10

This release makes the companion helper lifecycle fully Windows-safe and hardens the upgrade path introduced in 0.1.8/0.1.9.

## Fixed

- **Process checks no longer kill processes on Windows** — liveness checks previously used `os.kill(pid, 0)`, which on Windows terminates the target process instead of probing it. Deckhand now uses `OpenProcess`/`GetExitCodeProcess`, and stops processes with an explicit `TerminateProcess` call.
- **No console window flash on Windows** — the helper is launched with `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` (the previous `start_new_session` flag is POSIX-only and was silently ignored).
- **Stale helpers are detected by version, not just owner metadata** — the companion crate version is now released in lockstep with the add-on (enforced by a unit test), and the add-on treats a `/status` version mismatch as a stale helper. This closes the gap where upgrading from 0.1.8 (which wrote no owner metadata) silently reused the old helper.
- **Safer stale-helper shutdown** — before stopping a recorded PID, Deckhand verifies the companion port actually answers as a Deckhand service; a PID that cannot be verified (e.g. reused after a reboot) is never killed. If the old helper is still shutting down, Deckhand waits instead of starting a doomed replacement.
- **Runtime files stay out of the roaming profile on Windows** — copied executables, PID files, and logs now live under `%LOCALAPPDATA%` instead of `%APPDATA%` (collection-independent state stays in `%APPDATA%`). Files written by earlier versions are still found at the legacy location.
- **Old runtime binaries are pruned** — versioned helper copies from previous add-on versions are removed once they are no longer running.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
3. If Anki still reports `Access is denied` for `deckhand-server.exe` during this one upgrade, close Anki and stop any running `deckhand-server.exe` process in Task Manager once.
