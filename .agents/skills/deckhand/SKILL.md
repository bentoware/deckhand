---
name: deckhand
description: "Alias triggers: Deckhand, deckhand, Anki add-on, Anki extension, AnkiWeb, flashcards, cards, notes, decks, review queue, addon packaging, ankiaddon, live Anki runtime. Use when developing, testing, packaging, or debugging the Deckhand Anki add-on, including repository changes, Anki Desktop runtime inspection, collection-safe automation, and release readiness."
---

# Deckhand

Use this skill when a task involves the Deckhand Anki add-on codebase, live Anki runtime automation, `.ankiaddon` packaging, AnkiWeb readiness, or Codex workflows that need safe Anki collection context.

## Project Shape

Deckhand lives at:

```text
/Users/thoffman/github.com/bentoware/deckhand
```

Important paths:

```text
addon/deckhand/              Anki add-on package root
addon/deckhand/deckhand/     Python add-on module
crates/deckhand-server/      Rust companion server
scripts/build.py             canonical build/check/package runner
dist/deckhand.ankiaddon      packaged add-on output
```

## Common Commands

Run the add-on in local Anki and open the sidebar:

```sh
python3 scripts/build.py sync -- --restart-anki --open-sidebar
```

If the restart step quits Anki but macOS refuses the relaunch, run:

```sh
open -a Anki
osascript -e 'tell application "Anki" to activate' -e 'delay 1' -e 'tell application "System Events" to tell process "Anki" to click menu item "Open Sidebar" of menu "Deckhand" of menu bar 1'
```

Validate before claiming readiness:

```sh
make check
```

Package for manual testing or AnkiWeb:

```sh
make package-addon
```

## Runtime Rules

- Treat Anki as a live Qt/Python app with collection APIs, not as a SQLite/media folder editing target.
- Do not mutate notes, cards, decks, or media without explicit user approval.
- Use Anki APIs and add-on hooks for collection work.
- Run UI-affecting Anki calls on Anki's main Qt thread.
- Inspect state before assuming Browser, Reviewer, Editor, or Deck Browser screens are present.
- Keep the add-on package AGPL-compatible and avoid bundling desktop UI, private backend code, `node_modules`, or unrelated product assets.

## Useful Searches

```sh
rg -n "aqt|mw|gui_hooks|Collection|col\\.|QThread|run_on_main" addon/deckhand/deckhand
rg -n "MOCHIBAR|DECKHAND|deckhand|mochibar" .
rg -n "package_addon|ADDON_PACKAGE|FORBIDDEN|bin/" scripts tests
```

## Handoff Notes

When re-entering a Deckhand development task, first check whether Anki is already running and whether `/Users/thoffman/Library/Application Support/Anki2/addons21/deckhand` reflects the repo. If the user asks to run the add-on, prefer the sync command above, then retry `open -a Anki` and the menu AppleScript separately if macOS reports a launch error after quitting Anki.
