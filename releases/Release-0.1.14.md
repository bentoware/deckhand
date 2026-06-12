# Deckhand 0.1.14

This release replaces the Windows WebEngine restart worker with a simpler disk-backed command script after 0.1.13 still failed to relaunch Anki on a real Windows install.

## Fixed

- **"Restart Anki for Deckhand" no longer depends on inline PowerShell** — Windows now writes a small `anki-cdp-restart.cmd` worker into Deckhand's runtime log folder and launches it with `cmd.exe`.
- **Restart attempts create evidence before Anki closes** — Deckhand now creates `anki-cdp-restart.log` before requesting Anki shutdown, so if the log is missing the installed add-on never reached the restart path.
- **The restart log lives with the existing Deckhand logs** — look in `%LOCALAPPDATA%\Deckhand\state\logs` for `anki-cdp-restart.log` and `anki-cdp-restart.cmd`.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
