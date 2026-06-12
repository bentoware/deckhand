# Deckhand 0.1.7

This hotfix makes the WebEngine restart path harder to miss and adds coverage for Qt import mistakes.

## Fixed

- **Management exposes the WebEngine restart action** — the Status tab now shows “Restart Anki for Deckhand” whenever WebEngine control is off, even if the dismissible setup banner is not visible.
- **Qt import regressions are checked** — the Python tests now scan Deckhand UI modules for direct Qt symbol uses without matching `aqt.qt` imports.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
3. Follow the welcome dialog — or grab `Deckhand.mcpb` below and drop it into Claude Desktop.
