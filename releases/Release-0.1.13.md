# Deckhand 0.1.13

This release hardens the Windows WebEngine restart flow so Anki reliably comes back after choosing "Restart Anki for Deckhand".

## Fixed

- **Windows restarts wait for Anki to fully exit** — the detached restarter now waits longer for the original Anki process, also checks for remaining `anki.exe` instances at the same executable path, and only then relaunches with `QTWEBENGINE_REMOTE_DEBUGGING`.
- **Restart failures leave a local trail** — Windows restart attempts now write `%LOCALAPPDATA%\Deckhand\logs\anki-cdp-restart.log`, making any machine-specific launch blocker visible instead of silent.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
