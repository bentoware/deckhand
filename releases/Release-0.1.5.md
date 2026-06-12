# Deckhand 0.1.5

This hotfix restores Windows add-on startup.

## Fixed

- **Windows Anki startup no longer imports Unix-only `posix`** — Deckhand now guards `posix._exit` only on platforms where Python provides the `posix` module, so Windows can load the add-on normally.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
3. Follow the welcome dialog — or grab `Deckhand.mcpb` below and drop it into Claude Desktop.
