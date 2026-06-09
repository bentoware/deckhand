# Contributing

Thanks for helping Deckhand keep a steadier watch.

## Development Loop

```sh
make check
make build
python3 scripts/build.py sync -- --restart-anki
```

Keep changes scoped to the add-on, companion server, schemas, tests, or docs unless the repository grows new public surfaces intentionally.

## Add-On Rules Of Thumb

- Treat Anki APIs as runtime APIs, not stable file formats.
- Use Anki's collection APIs for reads and writes whenever possible.
- Route collection mutations through standard MCP tools and Anki collection APIs; risky tools should rely on MCP annotations so the client can confirm before calling them.
- Keep UI work small, readable, and compatible with Qt WebEngine constraints.
- Do not bundle unrelated product code into the `.ankiaddon` archive.

## Licensing

Deckhand is `AGPL-3.0-or-later`. Contributions are accepted under that license.
