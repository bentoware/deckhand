# Deckhand 0.1.1

The first release built by the new CI pipeline — and the first one that works everywhere. If you installed 0.1.0, update: it fixes a launch bug that affected fresh `.ankiaddon` installs.

## New

- **Claude Desktop extension (`Deckhand.mcpb`)** — the fastest way to connect. Download it below (or drag it straight out of *Deckhand → Management → Connect* inside Anki), drop it into Claude Desktop, click Install. The endpoint is pre-filled; if you've enabled Deckhand's access token, the extension carries it automatically.
- **Onboarding on demand** — *Deckhand → Onboarding* replays the first-run welcome anytime.
- **All-platform package** — `deckhand.ankiaddon` now bundles the companion server for macOS (Apple Silicon **and** Intel), Windows x86_64, and Linux x86_64.

## Fixed

- **Bundled server failed to launch from `.ankiaddon` installs** on macOS/Linux — Anki extracts add-on zips without permission bits; Deckhand now restores the executable bit before starting the helper. This affected the 0.1.0 package.
- **"Restart Anki for Deckhand" did nothing** when Anki took longer than two seconds to shut down; the restart now waits for a clean exit before relaunching.

## Changed

- Deckhand's settings and state now live in the standard per-user app-data location (`~/Library/Application Support/Deckhand/state` on macOS, `%APPDATA%\Deckhand\state` on Windows, `~/.local/share/deckhand/state` on Linux).
- README rewritten around what Deckhand does for you, with the concept art aboard.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools → Add-ons → Install from file…**, pick the download, restart Anki.
3. Follow the welcome dialog — or grab `Deckhand.mcpb` below and drop it into Claude Desktop.
