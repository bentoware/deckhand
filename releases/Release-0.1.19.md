# Deckhand 0.1.19

This release tightens Deckhand's MCP surface for code-mode agents and makes Claude Desktop setup verification more explicit.

## Improved

- **MCP tool responses are structured-only** - Deckhand no longer duplicates tool results as rendered text content, reducing noisy repeated JSON in MCP clients that already read structured content.
- **The Python executor now asks for `code`** - `anki_run_python` uses a clearer `code` input name in its public schema and generated tool inventory.
- **Claude Desktop verification is clearer** - setup instructions now ask Claude to "Use Deckhand and list my Anki decks," making it less likely that Claude answers without calling the add-on.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
