# Deckhand 0.1.15

This release makes the installed Deckhand version easier to confirm inside Anki.

## Changed

- **The Management dialog shows the add-on version up front** — the window title and main heading now include the current Deckhand version.
- **The Python package version is single-sourced** — `deckhand.__version__` now comes from the same `ADDON_VERSION` value as the add-on manifest and helper compatibility checks.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
