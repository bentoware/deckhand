# Deckhand 0.1.0

First public release. Deckhand is an Anki add-on that runs a local MCP server so AI assistants like Claude and Codex can inspect and operate your collection — with your approval, on your machine.

## Highlights

- **One-endpoint MCP server** — a bundled local helper exposes a standard Streamable HTTP endpoint (`http://127.0.0.1:28765/mcp`). No cloud, loopback only.
- **Lean tool surface** — a curated Anki core (notes, cards, decks, models, media, export/backup) plus `anki_run_python` for full code-mode control, with a WebEngine SDK (`deckhand.web`) for reading and driving Anki's rendered UI.
- **Management dialog built for humans** — Connect (copy-paste setup recipes for Claude Desktop, Claude Code, and Codex), Status (live health pills and a connection test that tells you what to fix), Server (start/stop/restart, port, optional access token), Skills, and About tabs.
- **Agent skills included** — study workflows from [deckhand-skills](https://github.com/bentoware/deckhand-skills) (PDF→cards, language learning, leech repair, card polish, and more) installable for Claude Code and Codex in one click, with daily auto-updates that never touch skills you've edited.
- **First-run welcome** — a one-time splash that walks new users from install to connected.
- **Honest update story** — sideloaded installs check GitHub releases once a day and prompt (never silently self-update); AnkiWeb installs defer to Anki's own updater.
- **Security opt-in** — require a bearer token on the MCP endpoint from the Server tab if you want more than loopback isolation.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools → Add-ons → Install from file…** and pick the download.
3. Restart Anki, then follow the welcome dialog (or **Deckhand → Management → Connect**).

## Known limitations

- This package bundles the companion server for **macOS (Apple Silicon) only**. Other platforms need a source build for now; multi-platform packages are planned.
- Requires the MCP client to support Streamable HTTP servers.
