# Deckhand 0.1.9

This hotfix makes companion ownership upgrade-aware.

## Fixed

- **Deckhand can stop its own stale helper after upgrades** — when the add-on starts a helper, it now records owner metadata with the PID, add-on version, and runtime binary path. On a later add-on version, Deckhand stops that recorded helper before starting the new one.
- **Port conflicts remain conservative** — Deckhand only stops a recorded Deckhand-owned helper from an older add-on version. If another service is answering on the configured port, Deckhand reports a port conflict instead of killing it.

## Install

1. If Anki reports `Access is denied` for `deckhand-server.exe`, close Anki and stop any running `deckhand-server.exe` process in Task Manager once.
2. Download `deckhand.ankiaddon` below.
3. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
