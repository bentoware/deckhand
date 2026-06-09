#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ADDON_SRC = ROOT / "addon" / "deckhand"
OUTPUT = ROOT / "crates" / "deckhand-server" / "src" / "generated" / "mcp_tool_inventory.json"
INTERNAL_ANKI_PREFIXES = ("anki_bridge_", "anki_smoke_")


def _title(name: str) -> str:
    parts = name.split("_")
    words = parts[2:] if parts[:1] == ["anki"] and len(parts) > 2 else parts
    return " ".join(word.capitalize() for word in words)


def _entry_payload(entry: Any) -> dict[str, Any]:
    input_schema = entry.input_schema.to_dict()
    read_only = entry.risk in {"read", "ui"}
    destructive = entry.risk in {"destructive", "dev_exec", "system_exec"}
    idempotent = entry.risk == "read" and entry.name.endswith(
        ("_status", "_list", "_list_pages", "_get_profile", "_registry")
    )
    return {
        "name": entry.name,
        "title": _title(entry.name),
        "status": entry.status,
        "risk": entry.risk,
        "description": entry.description,
        "input_schema": input_schema,
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
            "openWorldHint": bool(getattr(entry, "open_world", False)),
        },
    }


def _is_public_mcp_tool(entry: Any) -> bool:
    name = entry.name
    return (
        entry.status == "implemented"
        and "safe_bridge" in entry.paths
        and name.startswith("anki_")
        and not name.startswith(INTERNAL_ANKI_PREFIXES)
    )


def build_payload() -> dict[str, Any]:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ADDON_SRC))
    from deckhand.command_catalog import command_catalog, validate_command_catalog

    errors = validate_command_catalog()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        raise SystemExit(1)

    tools = [
        _entry_payload(entry)
        for entry in command_catalog()
        if _is_public_mcp_tool(entry)
    ]
    return {
        "source": "addon/deckhand/deckhand/command_catalog.py",
        "projection": "Anki namespace MCP projection for the standalone app bridge",
        "tools": tools,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Rust MCP tool inventory from the add-on command catalog.")
    parser.add_argument("--check", action="store_true", help="Fail if the checked-in generated inventory is stale.")
    args = parser.parse_args()

    payload = build_payload()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            print(f"MCP catalog is stale: regenerate with {Path(__file__).name}", file=sys.stderr)
            return 1
        print(f"MCP catalog is current: {OUTPUT}")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {len(payload['tools'])} MCP tools to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
