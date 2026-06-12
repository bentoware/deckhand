# Deckhand 0.1.8

This hotfix prevents Windows add-on updates from being blocked by the running helper executable.

## Fixed

- **Windows updates can replace Deckhand cleanly** — Deckhand now copies the bundled companion helper into a versioned runtime directory and launches that copy instead of executing `deckhand-server.exe` from Anki's `addons21` folder.
- **Runtime paths are cross-platform** — helper runtime files and logs now live under Deckhand's state root on each operating system.

## Install

1. If Anki reports `Access is denied` for `deckhand-server.exe`, close Anki and stop any running `deckhand-server.exe` process in Task Manager once.
2. Download `deckhand.ankiaddon` below.
3. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
