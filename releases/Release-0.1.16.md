# Deckhand 0.1.16

This release fixes the Windows restart path for enabling Deckhand's local WebEngine control port.

## Fixed

- **"Restart Anki for Deckhand" now survives Anki shutdown on Windows** - the restart worker is launched through Task Scheduler before Anki closes, so it is not killed with Anki's process tree.
- **Windows restart waits no longer busy-spin** - the worker uses `ping`-based waits instead of `timeout`, which can fail immediately in console-less processes.
- **Restart logs show the full handoff** - `%LOCALAPPDATA%\Deckhand\state\logs\anki-cdp-restart.log` now records Task Scheduler startup, parent exit, relaunch, and `start returned`.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
