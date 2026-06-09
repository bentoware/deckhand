# Deckhand Add-On Package Boundary

This package is the Anki-process adapter for Deckhand. It is designed to remain separately packageable and AGPL-compatible because it runs inside Anki.

The add-on may contain:

- Anki menu/sidebar registration.
- Anki state reads through `aqt`, `mw`, collection, reviewer, browser, and editor APIs.
- `anki_*` MCP tool advertisement and execution.
- Standard MCP `anki_*` tool execution with Anki collection/API calls.
- Minimal embedded bridge/status UI.

The add-on must not contain:

- Electron main/preload/renderer code.
- Standalone product UI.
- Embedded agent orchestration, account login, or chat runtime code.
- Non-Anki MCP management.
- Companion-site settings or durable product state.

The add-on and companion server should remain splittable into a clean public repository. Keep companion-site content, courses, marketing pages, private automation, and ad/email infrastructure outside this package.
