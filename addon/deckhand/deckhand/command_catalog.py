from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
from re import Pattern, compile
from typing import Any


COMMAND_NAME_RE: Pattern[str] = compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
CATALOG_VERSION = "v1"

ANKI_SDK_ANKI_PATH_PLACEHOLDER = "{anki_sdk_anki_path}"
ANKI_SDK_AQT_PATH_PLACEHOLDER = "{anki_sdk_aqt_path}"


def anki_sdk_reference_paths(home: Path | None = None) -> tuple[str, str]:
    root = Path(os.environ["DECKHAND_ANKI_PROGRAM_FILES"]) if home is None and os.environ.get("DECKHAND_ANKI_PROGRAM_FILES") else (home or Path.home()) / "Library" / "Application Support" / "AnkiProgramFiles"
    site_packages = root / ".venv" / "lib" / "python3.13" / "site-packages"
    return str(site_packages / "anki"), str(site_packages / "aqt")


def anki_execute_tool_description(home: Path | None = None, *, resolve_paths: bool = True) -> str:
    anki_path, aqt_path = anki_sdk_reference_paths(home) if resolve_paths else (
        ANKI_SDK_ANKI_PATH_PLACEHOLDER,
        ANKI_SDK_AQT_PATH_PLACEHOLDER,
    )
    return (
        "Execute Python inside Anki's main process. Use this instead of general Python for Anki "
        "inspection or mutation; code has normal imports plus mw/aqt access and returns the variable "
        "named result. For local Anki SDK/source reference, inspect "
        f"{anki_path} and {aqt_path}."
    )


@dataclass(frozen=True)
class CommandSchema:
    type: str = "object"
    properties: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    additionalProperties: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CommandCatalogEntry:
    name: str
    family: str
    risk: str
    paths: tuple[str, ...]
    description: str
    status: str
    input_schema: CommandSchema = field(default_factory=CommandSchema)
    output_schema: CommandSchema = field(default_factory=CommandSchema)
    evidence: str = "timeline"
    open_world: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["paths"] = list(self.paths)
        data["input_schema"] = self.input_schema.to_dict()
        data["output_schema"] = self.output_schema.to_dict()
        return data

    @property
    def path(self) -> str:
        return ",".join(self.paths)


def _schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    additional: bool = True,
) -> CommandSchema:
    return CommandSchema(
        properties=properties or {},
        required=required or [],
        additionalProperties=additional,
    )


def _entry(
    name: str,
    risk: str,
    description: str,
    *,
    paths: tuple[str, ...] = ("safe_bridge",),
    status: str = "planned",
    input_schema: CommandSchema | None = None,
    output_schema: CommandSchema | None = None,
    evidence: str = "timeline",
    open_world: bool = False,
) -> CommandCatalogEntry:
    family = ".".join(name.split(".")[:2])
    return CommandCatalogEntry(
        name=name,
        family=family,
        risk=risk,
        paths=paths,
        description=description,
        status=status,
        input_schema=input_schema or _schema(),
        output_schema=output_schema or _schema(),
        evidence=evidence,
        open_world=open_world,
    )


NOTE_ID = {"type": "integer", "minimum": 1}
CARD_ID = {"type": "integer", "minimum": 1}
QUERY = {"type": "string", "minLength": 1}
FIELDS = {"type": "object", "additionalProperties": {"type": "string"}}
TAG = {"type": "string", "minLength": 1}
TAGS = {"type": "array", "items": TAG, "minItems": 1}
NOTE_IDS = {"type": "array", "items": NOTE_ID, "minItems": 1, "maxItems": 20}
CARD_IDS = {"type": "array", "items": CARD_ID, "minItems": 1, "maxItems": 50}
DECK_NAME = {"type": "string", "minLength": 1}
MODEL_NAME = {"type": "string", "minLength": 1}
FIELD_NAME = {"type": "string", "minLength": 1}
FILE_PATH = {"type": "string", "minLength": 1}
FOLDER_PATH = {"type": "string", "minLength": 1}
CDP_TARGET = {
    "webSocketDebuggerUrl": {"type": "string", "description": "Exact CDP page WebSocket URL from list_pages. Overrides all other target selectors."},
    "pageId": {"type": "string", "description": "Exact CDP page id from list_pages."},
    "title": {"type": "string", "description": "Case-insensitive substring of the target page title."},
    "urlContains": {"type": "string", "description": "Substring that must appear in the target page URL."},
    "preferredTarget": {"type": "string", "enum": ["main", "first", "strict"], "description": "How to choose a page when no explicit target selector is supplied. Default main prefers Anki's main webview; first chooses the first CDP page; strict fails if multiple pages are available."},
    "host": {"type": "string", "description": "Local CDP host. Defaults to 127.0.0.1 and must be localhost."},
    "port": {"type": "integer", "minimum": 1, "description": "Local CDP port. Defaults to 9222."},
    "timeoutSeconds": {"type": "number", "minimum": 0.1, "description": "Per-operation timeout in seconds."},
}

WEBENGINE_TARGET_NOTE = " If no target is supplied, Deckhand defaults to Anki's page titled main webview; use list_pages first when the intended page is ambiguous."


COMMAND_CATALOG: tuple[CommandCatalogEntry, ...] = (
    _entry("anki.app.get_state", "read", "Return current app, reviewer, editor, browser, deck, and profile state.", status="implemented"),
    _entry("anki.context.get_current", "read", "Return current reviewer/editor/browser context.", status="implemented"),
    _entry("anki.context.get_profile", "read", "Return active profile, collection, scheduler, and add-on runtime identity.", status="implemented"),
    _entry("anki.note.search", "read", "Search notes and return note identifiers.", status="implemented", input_schema=_schema({"query": QUERY, "limit": {"type": "integer", "minimum": 1}}, ["query"])),
    _entry("anki.note.get", "read", "Read fields, tags, deck, and model metadata for one note.", status="implemented", input_schema=_schema({"noteId": NOTE_ID}, ["noteId"])),
    _entry("anki.note.create", "mutation", "Create a note.", status="implemented", input_schema=_schema({"deck": DECK_NAME, "model": MODEL_NAME, "fields": FIELDS, "tags": {"type": "array", "items": TAG}}, ["deck", "model", "fields"])),
    _entry("anki.note.update_fields", "mutation", "Update explicitly requested note fields.", status="implemented", input_schema=_schema({"noteId": NOTE_ID, "fields": FIELDS}, ["noteId", "fields"])),
    _entry("anki.note.add_tag", "mutation", "Add a tag to a note.", status="implemented", input_schema=_schema({"noteId": NOTE_ID, "tag": TAG}, ["noteId", "tag"])),
    _entry("anki.note.remove_tag", "mutation", "Remove a tag from a note.", status="implemented", input_schema=_schema({"noteId": NOTE_ID, "tag": TAG}, ["noteId", "tag"])),
    _entry("anki.note.set_tags", "mutation", "Replace a note's tags.", status="implemented", input_schema=_schema({"noteId": NOTE_ID, "tags": TAGS}, ["noteId", "tags"])),
    _entry("anki.note.delete", "destructive", "Delete one or more notes.", status="implemented", input_schema=_schema({"noteIds": NOTE_IDS, "cap": {"type": "integer", "minimum": 1, "maximum": 100}}, ["noteIds"])),
    _entry("anki.card.get", "read", "Read card metadata, state, queue, due, and linked note id.", status="implemented", input_schema=_schema({"cardId": CARD_ID}, ["cardId"])),
    _entry("anki.card.find_by_note", "read", "Return cards generated by a note.", status="implemented", input_schema=_schema({"noteId": NOTE_ID}, ["noteId"])),
    _entry("anki.card.preview", "read", "Render card front/back preview for inspection.", status="implemented", input_schema=_schema({"cardId": CARD_ID}, ["cardId"])),
    _entry("anki.card.suspend", "mutation", "Suspend cards.", status="implemented", input_schema=_schema({"cardIds": CARD_IDS}, ["cardIds"])),
    _entry("anki.card.unsuspend", "mutation", "Unsuspend cards.", status="implemented", input_schema=_schema({"cardIds": CARD_IDS}, ["cardIds"])),
    _entry("anki.card.bury", "mutation", "Bury cards until the next day.", status="implemented", input_schema=_schema({"cardIds": CARD_IDS}, ["cardIds"])),
    _entry("anki.card.unbury", "mutation", "Unbury cards.", status="implemented", input_schema=_schema({"cardIds": CARD_IDS}, ["cardIds"])),
    _entry("anki.card.set_due", "mutation", "Set due dates for cards.", status="implemented", input_schema=_schema({"cardIds": CARD_IDS, "days": {"type": "integer"}}, ["cardIds", "days"])),
    _entry("anki.deck.list", "read", "List decks and identifiers.", status="implemented"),
    _entry("anki.deck.get_stats", "read", "Return deck counts and scheduler stats.", status="implemented", input_schema=_schema({"deckId": {"type": "integer", "minimum": 1}})),
    _entry("anki.deck.create", "mutation", "Create a deck.", status="implemented", input_schema=_schema({"name": DECK_NAME}, ["name"])),
    _entry("anki.model.list", "read", "List note types and identifiers.", status="implemented"),
    _entry("anki.model.get", "read", "Inspect one note type.", status="implemented", input_schema=_schema({"modelName": MODEL_NAME, "modelId": {"type": "integer", "minimum": 1}})),
    _entry("anki.media.add_file", "mutation", "Import a local media file with attachment provenance.", status="implemented", input_schema=_schema({"path": FILE_PATH, "sourceKind": {"type": "string"}, "provenance": {"type": "object"}}, ["path"])),
    _entry("anki.media.get", "read", "Read media metadata and safe preview path.", status="implemented", input_schema=_schema({"filename": FILE_PATH}, ["filename"])),
    _entry("anki.media.attach_to_field", "mutation", "Attach media markup to a note field.", status="implemented", input_schema=_schema({"noteId": NOTE_ID, "field": FIELD_NAME, "filename": FILE_PATH, "mediaType": {"type": "string"}}, ["noteId", "field", "filename"])),
    _entry("anki.export.notes", "read", "Export notes matching an Anki search query to a required local file path. Returns artifact metadata only, not file contents.", status="implemented", input_schema=_schema({"query": QUERY, "filePath": FILE_PATH, "format": {"type": "string", "enum": ["csv", "json"]}, "limit": {"type": "integer", "minimum": 1}, "overwrite": {"type": "boolean"}}, ["query", "filePath"]), evidence="artifact"),
    _entry("anki.export.deck_snapshot", "read", "Write a Deckhand JSON audit snapshot of decks, models, and deck stats to a required local file path. Returns artifact metadata and summary only.", status="implemented", input_schema=_schema({"filePath": FILE_PATH, "overwrite": {"type": "boolean"}}, ["filePath"]), evidence="artifact"),
    _entry("anki.export.deck_package", "read", "Export an Anki deck package to a required local file path using Anki's native package export APIs. Returns artifact metadata only.", status="implemented", input_schema=_schema({"filePath": FILE_PATH, "deck": {"type": "string"}, "includeMedia": {"type": "boolean"}, "includeScheduling": {"type": "boolean"}, "overwrite": {"type": "boolean"}}, ["filePath"]), evidence="artifact"),
    _entry("anki.export.collection_package", "read", "Export a modern Anki collection package to a required local file path using Anki's native collection export API. Returns artifact metadata only.", status="implemented", input_schema=_schema({"filePath": FILE_PATH, "includeMedia": {"type": "boolean"}, "overwrite": {"type": "boolean"}}, ["filePath"]), evidence="artifact"),
    _entry("anki.backup.create", "mutation", "Create a native Anki no-media collection backup in a required local folder using Anki's backup API. Returns backup path metadata only.", status="implemented", input_schema=_schema({"folderPath": FOLDER_PATH, "force": {"type": "boolean"}, "waitForCompletion": {"type": "boolean"}}, ["folderPath"]), evidence="artifact"),
    _entry("anki.execute", "dev_exec", anki_execute_tool_description(resolve_paths=False), status="implemented", input_schema=_schema({"snippet": {"type": "string", "minLength": 1}}, ["snippet"])),
    _entry("anki.webengine.status", "read", "Check Anki's local Qt WebEngine CDP endpoint status.", status="implemented"),
    _entry("anki.webengine.list_pages", "read", "List debuggable Anki Qt WebEngine pages from the local CDP endpoint.", status="implemented"),
    _entry("anki.webengine.take_snapshot", "read", "Return a compact DOM snapshot of an Anki Qt WebEngine CDP page." + WEBENGINE_TARGET_NOTE, status="implemented", input_schema=_schema({**CDP_TARGET, "maxTextChars": {"type": "integer", "minimum": 1}, "maxElements": {"type": "integer", "minimum": 1}})),
    _entry("anki.webengine.take_screenshot", "read", "Capture an Anki Qt WebEngine CDP page screenshot to a required local file path." + WEBENGINE_TARGET_NOTE, status="implemented", input_schema=_schema({**CDP_TARGET, "filePath": {"type": "string", "minLength": 1}, "format": {"type": "string", "enum": ["png", "jpeg", "webp"]}, "quality": {"type": "integer", "minimum": 0, "maximum": 100}, "captureBeyondViewport": {"type": "boolean"}}, ["filePath"]), evidence="artifact"),
    _entry("anki.webengine.evaluate_script", "dev_exec", "Run JavaScript in an Anki Qt WebEngine CDP page; this can change UI state." + WEBENGINE_TARGET_NOTE, status="implemented", input_schema=_schema({**CDP_TARGET, "script": {"type": "string", "minLength": 1}, "awaitPromise": {"type": "boolean"}, "returnByValue": {"type": "boolean"}}, ["script"])),
    _entry("anki.webengine.click", "dev_exec", "Click in an Anki Qt WebEngine CDP page by selector, visible text, or coordinates; this changes UI state." + WEBENGINE_TARGET_NOTE, status="implemented", input_schema=_schema({**CDP_TARGET, "selector": {"type": "string"}, "text": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}, "button": {"type": "string", "enum": ["left", "middle", "right", "none"]}, "clickCount": {"type": "integer", "minimum": 1}})),
    _entry("anki.webengine.type_text", "dev_exec", "Type text into an Anki Qt WebEngine CDP page; this changes UI state." + WEBENGINE_TARGET_NOTE, status="implemented", input_schema=_schema({**CDP_TARGET, "text": {"type": "string", "minLength": 1}, "selector": {"type": "string"}, "clear": {"type": "boolean"}}, ["text"])),
    _entry("anki.webengine.press_key", "dev_exec", "Dispatch a keyboard press to an Anki Qt WebEngine CDP page; this changes UI state." + WEBENGINE_TARGET_NOTE, status="implemented", input_schema=_schema({**CDP_TARGET, "key": {"type": "string", "minLength": 1}, "code": {"type": "string"}, "modifiers": {"type": "integer", "minimum": 0}, "windowsVirtualKeyCode": {"type": "integer", "minimum": 0}}, ["key"])),
    _entry("anki.webengine.wait_for", "dev_exec", "Wait for a selector, text, title, URL substring, or JavaScript expression in an Anki Qt WebEngine CDP page; expressions can change UI state." + WEBENGINE_TARGET_NOTE, status="implemented", input_schema=_schema({**CDP_TARGET, "selector": {"type": "string"}, "text": {"type": "string"}, "expression": {"type": "string"}, "pollIntervalSeconds": {"type": "number", "minimum": 0.01}})),
    _entry("anki.webengine.list_console_messages", "read", "Observe console, log, and exception events from an Anki Qt WebEngine CDP page during a short capture window." + WEBENGINE_TARGET_NOTE, status="implemented", input_schema=_schema({**CDP_TARGET, "listenSeconds": {"type": "number", "minimum": 0}, "limit": {"type": "integer", "minimum": 1}})),
    _entry("anki.webengine.list_network_requests", "read", "Observe network events from an Anki Qt WebEngine CDP page during a short capture window." + WEBENGINE_TARGET_NOTE, status="implemented", input_schema=_schema({**CDP_TARGET, "listenSeconds": {"type": "number", "minimum": 0}, "limit": {"type": "integer", "minimum": 1}})),
    _entry("anki.webengine.send_cdp_command", "dev_exec", "Send one raw CDP command to an Anki Qt WebEngine page; this can change UI state." + WEBENGINE_TARGET_NOTE, status="implemented", input_schema=_schema({**CDP_TARGET, "method": {"type": "string", "minLength": 1}, "params": {"type": "object"}}, ["method"])),
)


def command_catalog() -> list[CommandCatalogEntry]:
    return list(COMMAND_CATALOG)


def command_catalog_payload() -> dict[str, Any]:
    return {
        "version": CATALOG_VERSION,
        "commands": [entry.to_dict() for entry in COMMAND_CATALOG],
    }


def validate_command_catalog(entries: list[CommandCatalogEntry] | None = None) -> list[str]:
    catalog = entries or command_catalog()
    errors: list[str] = []
    names: set[str] = set()
    valid_statuses = {"implemented", "planned", "spike"}

    for entry in catalog:
        if entry.name in names:
            errors.append(f"duplicate command name: {entry.name}")
        names.add(entry.name)
        if not COMMAND_NAME_RE.match(entry.name):
            errors.append(f"invalid command name: {entry.name}")
        if entry.family != ".".join(entry.name.split(".")[:2]):
            errors.append(f"family mismatch: {entry.name}")
        if not entry.risk:
            errors.append(f"missing risk: {entry.name}")
        if not entry.paths:
            errors.append(f"missing paths: {entry.name}")
        if not entry.description:
            errors.append(f"missing description: {entry.name}")
        if entry.status not in valid_statuses:
            errors.append(f"invalid status: {entry.name}")

    return errors
