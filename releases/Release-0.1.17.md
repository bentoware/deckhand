# Deckhand 0.1.17

This release keeps the Windows WebEngine restart fix from 0.1.16, but hides the restart worker consoles it could leave on screen.

## Fixed

- **The Windows restart worker no longer opens visible terminal tabs** - Task Scheduler now launches a tiny `wscript.exe` wrapper, which starts the command worker hidden.
- **Duplicate Task Scheduler starts are ignored safely** - if Windows starts the same handoff twice, the second worker exits before launching another Anki.
- **Restart tasks clean up after themselves** - the worker deletes its scheduled task after relaunching Anki, while failed immediate handoffs still clean up from the caller.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
