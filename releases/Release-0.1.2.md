# Deckhand 0.1.2

This release makes Deckhand's safety backup tool easier for MCP clients to see and use before major Anki collection work.

## Changed

- **Backup tool is part of the fixed MCP surface** — `anki_backup_create` appears alongside `anki_run_python` and `anki_runtime_info`.
- **Deckhand keeps the public surface small** — nontrivial Anki work should go through `anki_run_python` inside Anki's runtime.
- **LLM-facing guidance calls out backups** — the MCP server instructions and backup tool description now recommend creating a backup before bulk edits, deletes, imports, template changes, scheduling changes, or other major mutations.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools → Add-ons → Install from file…**, pick the download, restart Anki.
3. Follow the welcome dialog — or grab `Deckhand.mcpb` below and drop it into Claude Desktop.
