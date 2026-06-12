# Deckhand 0.1.4

This release makes Deckhand's bundled assistant skill more intent-first, self-contained, and helpful for real study requests.

## Changed

- **Deckhand understands goals before workflows** — the bundled skill now emphasizes learner intent, reasonable defaults, and useful next actions instead of treating prompts like strict commands.
- **Card quality guidance is more flexible** — high-quality, cited cards remain the default, but rough drafts, broad coverage, and low-risk practice material now have explicit preview-first escape hatches.
- **Authority-sensitive material is clearer** — medical, legal, exam, clinical, regulatory, and course-specific cards now share the same grounding rule: no invented citations or unsourced authoritative claims.
- **Study review intent is handled better** — requests to study due cards should guide the user into Anki's normal review flow instead of running a card-quality audit.
- **Skill resources are easier to discover** — the Deckhand skill now includes a compact map of its command recipes and references so agents know what is available without eagerly loading every file.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
3. Follow the welcome dialog — or grab `Deckhand.mcpb` below and drop it into Claude Desktop.
