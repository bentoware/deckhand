# Deckhand 0.1.3

This release adds Deckhand's Codex plugin package, tightens the public MCP tool surface, and improves onboarding for connecting AI assistants to Anki.

## Changed

- **Codex plugin package is included** — Deckhand now ships a local plugin bundle with marketplace metadata, icon assets, command workflows, and bundled skill guidance.
- **Assistant setup is clearer** — the add-on welcome flow and setup assets now include copy-ready connection steps for supported hosts.
- **Public MCP surface is intentionally small** — Deckhand exposes the core runtime, backup, and Python execution tools while keeping specialized Anki work inside Anki's runtime.
- **Bundled skills stay in sync** — plugin skill copies are generated and checked as part of the build/test flow.
- **Release packaging is more complete** — the release workflow builds companion binaries for macOS, Linux, and Windows before packaging the add-on.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
3. Follow the welcome dialog — or grab `Deckhand.mcpb` below and drop it into Claude Desktop.
