# Deckhand 0.1.12

This release fixes the Windows WebEngine restart path and makes helper upgrades recover more reliably when an older companion is still running.

## Fixed

- **"Restart Anki for Deckhand" now works on Windows** — the restart action validates the Anki executable, schedules a detached relaunch with `QTWEBENGINE_REMOTE_DEBUGGING`, waits for the current Anki process to exit, and asks Anki to close through Qt instead of force-killing it.
- **Restart failures are visible** — the banner, Status tab, and Developer Panel now route through one UI-aware restart helper that shows a warning when Deckhand cannot schedule the restart or cannot ask Anki to close.
- **The restart banner can recover from a stale dismissal** — if WebEngine control is still off, Deckhand clears a previous banner dismissal so the restart cue can appear again.
- **Newer add-ons can take over from older helpers** — the companion now tracks its parent Anki process, shuts down when that parent exits, and rejects bridge connections from newer add-ons so the newer add-on can restart the matching helper.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
