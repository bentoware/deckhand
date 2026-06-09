#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ADDON_SRC = ROOT / "addon" / "deckhand"
ADDON_MODULE = ADDON_SRC / "deckhand"
ADDON_PY = ADDON_MODULE / "addon.py"
INVENTORY = ROOT / "crates" / "deckhand-server" / "src" / "generated" / "mcp_tool_inventory.json"

LEGACY_TOOL_NAMES = {
    "anki.bridge.registry",
    "anki.bridge.call",
    "anki.bridge.server_call",
    "anki.smoke.safe_bridge",
    "anki.media.add_bytes",
    "anki.import.preview_csv",
    "anki.import.apply_csv",
    "anki.backup.collection",
    "anki.context.get_selection",
    "anki.dev.set_addon_config",
    "anki.dev.backup_collection",
    "anki.dev.get_addon_config",
    "system.exec.run",
    "system.files.read",
    "system.files.write",
    "system.files.list",
    "system.files.attach",
    "ui.sidebar.show_status",
    "ui.sidebar.open_command_palette",
}

LEGACY_SOURCE_TOKENS = {
    "requiresApproval",
    "approval_id",
    "guardian",
    "/anki-bridge/call",
    "/anki-bridge/status",
    "/api/app/state",
    "/embed/anki",
    "preview_csv",
    "apply_csv",
    "backup_collection",
    "add_bytes",
    "set_addon_config",
    "get_addon_config",
    "_legacy_",
    "web_bridge",
    "SidebarBridgeCore",
    "deckhandBridge",
    "open-sidebar",
    "Open Sidebar",
    "browser_set_search",
    "editor_preview_current_note",
    "anki.browser.get_selection",
    "anki.browser.search",
    "anki.browser.apply_tags",
    "anki.context.get_selection",
    "anki.context.get_deck_browser",
    "anki.editor.get_fields",
    "anki.editor.get_focused_note",
    "anki.editor.set_field",
    "anki.editor.insert_media",
    "editor_get_fields",
    "note_create_draft",
    "note_update_fields_draft",
    "note_duplicate",
    "note_bulk_add_tag",
    "card_reposition",
    "deck_rename",
    "model_get_fields",
    "model_get_templates",
    "model_get_css",
    "template_render",
    "template_diff",
    "template_validate",
    "template_update_draft",
    "find_refs",
    "validate_missing",
    "validate_unused",
    "add_url",
    "anki.navigate.deck_browser",
    "anki.navigate.browser_search",
    "anki.navigate.note",
    "anki.navigate.card",
    "navigate_deck_browser",
    "navigate_browser_search",
    "navigate_note",
    "navigate_card",
    "_open_browser",
    "companion_auth_header",
    "ToolExecutionError",
    "_resolve_websocket_url",
    "Claude Desktop",
    "Cursor Settings",
    "VS Code extension",
    "deckhand.safe_bridge.v1",
    "sql_read",
    "list_hooks",
    "require_read_only_sql",
    "show_diagnostics",
    "Deckhand Diagnostics",
    "Developer Tools",
    'QMenu("Developer"',
    "deckhand_developer_menu",
    "_show_web_window",
    "QWebEngineView",
    "_fallback_store",
    "Deckhand prototype",
    "anki_lens",
    "Deckhand Lens",
    "Lens Inspector",
    "enable_lens_inspector",
    "copy_prompt_to_clipboard",
    "annotations_path",
    "latest-inspection",
    "inspect.js",
}

LEGACY_RUST_SOURCE_TOKENS = {
    'mod safe_bridge;',
    'unwrap_or("legacy")',
    '"legacy"',
}


def main() -> int:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ADDON_SRC))
    from deckhand.command_catalog import command_catalog, validate_command_catalog

    errors = validate_command_catalog()
    catalog = command_catalog()
    catalog_names = {entry.name for entry in catalog if entry.status == "implemented"}
    public_catalog_names = {
        entry.name
        for entry in catalog
        if entry.status == "implemented"
        and "safe_bridge" in entry.paths
        and entry.name.startswith("anki.")
        and not entry.name.startswith("anki.bridge.")
        and ".smoke." not in entry.name
    }
    registered_names = _registered_tool_names(ADDON_PY)
    inventory_names = _inventory_tool_names(INVENTORY)

    errors.extend(_set_errors("registered but missing from catalog", registered_names - catalog_names))
    errors.extend(_set_errors("catalog public MCP tool missing executor registration", public_catalog_names - registered_names))
    errors.extend(_set_errors("registered legacy tool", registered_names & LEGACY_TOOL_NAMES))
    errors.extend(_set_errors("catalog legacy tool", catalog_names & LEGACY_TOOL_NAMES))
    errors.extend(_set_errors("generated MCP inventory mismatch: missing", public_catalog_names - inventory_names))
    errors.extend(_set_errors("generated MCP inventory mismatch: extra", inventory_names - public_catalog_names))

    for entry in catalog:
        props = set((entry.input_schema.properties or {}).keys())
        if "approved" in props:
            errors.append(f"{entry.name}: input schema must not contain approved")
        if entry.name == "anki.export.collection_package" and "legacy" in props:
            errors.append("anki.export.collection_package: input schema must not contain legacy")

    errors.extend(_legacy_source_token_errors())
    errors.extend(_legacy_rust_source_token_errors())
    errors.extend(_legacy_fixture_errors())
    errors.extend(_python_cache_errors())

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("tool surface is clean")
    return 0


def _registered_tool_names(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "register":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.add(first.value)
    return names


def _inventory_tool_names(path: Path) -> set[str]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return {str(tool["name"]) for tool in payload.get("tools", [])}


def _set_errors(label: str, names: set[str]) -> list[str]:
    return [f"{label}: {name}" for name in sorted(names)]


def _legacy_source_token_errors() -> list[str]:
    errors: list[str] = []
    checked_paths = [
        *ADDON_MODULE.glob("*.py"),
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        ADDON_SRC / "docs" / "package-boundary.md",
    ]
    for path in checked_paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for token in sorted(LEGACY_SOURCE_TOKENS):
            if token in text:
                errors.append(f"legacy token {token!r} remains in {path.relative_to(ROOT)}")
    return errors


def _legacy_rust_source_token_errors() -> list[str]:
    errors: list[str] = []
    checked_paths = [
        *ROOT.glob("crates/deckhand-server/src/*.rs"),
    ]
    for path in checked_paths:
        text = path.read_text(encoding="utf-8")
        for token in sorted(LEGACY_RUST_SOURCE_TOKENS):
            if token in text:
                errors.append(f"legacy Rust token {token!r} remains in {path.relative_to(ROOT)}")
    return errors


def _legacy_fixture_errors() -> list[str]:
    errors: list[str] = []
    fixture_paths = [
        *ROOT.glob("crates/deckhand-server/fixtures/*.json"),
    ]
    for path in fixture_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for key in ("approval", "approved", "requiresApproval"):
            if _json_contains_key(payload, key):
                errors.append(f"legacy fixture key {key!r} remains in {path.relative_to(ROOT)}")
        namespaces = payload.get("toolNamespaces")
        if isinstance(namespaces, list) and "anki.navigate" in namespaces:
            errors.append(f"legacy fixture namespace 'anki.navigate' remains in {path.relative_to(ROOT)}")
    return errors


def _json_contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_json_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_json_contains_key(item, key) for item in value)
    return False


def _python_cache_errors() -> list[str]:
    return [
        f"python bytecode cache remains in source tree: {path.relative_to(ROOT)}"
        for path in sorted(ADDON_MODULE.rglob("__pycache__"))
    ]


if __name__ == "__main__":
    raise SystemExit(main())
