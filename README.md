# Deckhand

Resourceful. Loyal. Always on watch.

Deckhand is an open source Anki add-on that lets an agent operate from inside the live Anki runtime. It can inspect the current collection, surface context, run guarded tools, and help you chart changes without pretending Anki is just a pile of files on disk.

> One tool. Infinite possibilities.

## What It Is

Deckhand lives inside Anki Desktop as a native add-on and pairs with a small Rust companion process. The add-on handles Anki-safe access to decks, notes, cards, browser/editor context, media, and UI surfaces. The companion exposes those capabilities over a local bridge so external agents can automate, inspect, and operate with explicit guardrails.

The mascot vibe is intentional: a clever deckhand below deck, keeping watch on the runtime while you steer.

## Publishing And License

Deckhand is licensed under `AGPL-3.0-or-later`.

AnkiWeb add-ons are expected to use AGPLv3 or an AGPL-compatible license because they extend Anki Desktop. Keeping this repository public, licensed, and self-contained makes the AnkiWeb publication path clearer and makes the source auditable for users.

## Repository Shape

```text
addon/deckhand/              Anki add-on package root
addon/deckhand/deckhand/     Python add-on runtime
crates/deckhand-server/      Rust companion process
scripts/                     Build, package, sync, and development helpers
tests/                       Python and build-system regression tests
```

## Build

```sh
make check
make build
make package-addon
```

`make check` verifies the generated tool inventory, runs Python unit tests, and runs Rust tests. `make package-addon` writes `dist/deckhand.ankiaddon` with the platform companion binary bundled at `bin/<platform>/deckhand-server`.

## Local Development

```sh
python3 scripts/build.py sync -- --restart-anki
```

The sync command rebuilds the local tool inventory, builds the companion in debug mode, copies the add-on into Anki's `addons21` folder, and can restart Anki for a fresh smoke test.

To debug the MCP server with the official MCP Inspector:

```sh
make inspect-mcp
```

Start Anki first so the Deckhand add-on owns the companion server, then run the Inspector against the same Streamable HTTP MCP endpoint Codex uses: `http://127.0.0.1:18765/mcp`.

Pass Inspector options after `--`:

```sh
python3 scripts/build.py inspect-mcp -- --client-port 8080
CLIENT_PORT=8080 SERVER_PORT=9000 make inspect-mcp
```

Override the inspected endpoint when needed:

```sh
python3 scripts/build.py inspect-mcp --url http://127.0.0.1:18888/mcp
DECKHAND_MCP_URL=http://127.0.0.1:18888/mcp make inspect-mcp
```

Useful overrides:

```sh
DECKHAND_SAFE_BRIDGE_URL=ws://127.0.0.1:18765/ws/anki
DECKHAND_MCP_TOOL_ALLOWLIST=anki.execute,anki.app.get_state
DECKHAND_MCP_TOOL_TIMEOUT_SECONDS=120
DECKHAND_ANKI_PROGRAM_FILES="$HOME/Library/Application Support/AnkiProgramFiles"
```

WebEngine CDP tools such as `anki.webengine.take_snapshot`, `anki.webengine.take_screenshot`, and `anki.webengine.evaluate_script` require Anki to be launched with Qt WebEngine remote debugging enabled:

```sh
QTWEBENGINE_REMOTE_DEBUGGING=9222 open -a Anki
```

Those tools only connect to local CDP targets and use standard MCP annotations; screenshots write to the caller-provided `filePath` and return file metadata.

## Local Security

The companion binds to loopback by default. When the Anki add-on starts it, the add-on generates an ephemeral `DECKHAND_COMPANION_TOKEN` and passes it to the Rust process. The internal `/ws/anki` bridge requires that token through `Authorization: Bearer <token>`, `X-Deckhand-Token`, or a WebSocket `token` query parameter.

The canonical `/mcp` endpoint and `/healthz`/`/status` remain usable on loopback so Codex and MCP Inspector can connect without a token-sharing setup. `/mcp` exposes a lean user-facing Anki core; bridge transport, smoke, recursive call helpers, template/import/export maintenance tools, and low-level dev probes stay internal or omitted. Deckhand exposes standard MCP tool annotations, and confirmation prompts for risky tools are handled by the MCP client.

## Package Boundary

The `.ankiaddon` archive must not include private backend code, `node_modules`, unrelated product assets, or generated build products outside the bundled companion binary. The build runner enforces this boundary before writing a package.

## Third Party Notices

See `THIRD_PARTY_NOTICES.md` for dependency and licensing notes. The add-on code and Rust companion are prepared for open source distribution under AGPL-compatible terms, but do one final dependency/license review before first public release.

## A Tiny Ship's Log

Deckhand does not promise magic. It promises better watchkeeping: clear standard MCP tools, visible runtime context, and fewer blind edits in the dark.
