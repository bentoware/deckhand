# Deckhand 0.1.11

This release closes the last gap in the helper upgrade path and fixes a misleading status indicator.

## Fixed

- **Upgrades now stop helpers started by pre-0.1.8 add-ons** — versions before 0.1.8 recorded the helper PID in a hardcoded `Deckhand/runtime` directory that newer add-ons never checked, so the old helper kept the port and the add-on reported "Needs attention" without being able to fix it. The PID lookup now falls back to all legacy locations, so the stale helper is stopped and replaced automatically.
- **The Connect tab status pill reflects the live connection state** — it previously always said "Ready", even while the dialog header said "Needs attention".

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
