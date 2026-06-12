# Deckhand 0.1.6

This hotfix restores the Management dialog on Windows.

## Fixed

- **Management opens on Windows** — the Connect tab now imports `Qt` in the scope where it uses `Qt.ScrollBarPolicy.ScrollBarAlwaysOff`, preventing a `NameError` when opening Deckhand Management.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
3. Follow the welcome dialog — or grab `Deckhand.mcpb` below and drop it into Claude Desktop.
