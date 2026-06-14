from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
from re import Pattern, compile
from typing import Any


COMMAND_NAME_RE: Pattern[str] = compile(r"^[a-zA-Z0-9_-]{1,64}$")
CATALOG_VERSION = "v1"

ANKI_SDK_ANKI_PATH_PLACEHOLDER = "{anki_sdk_anki_path}"
ANKI_SDK_AQT_PATH_PLACEHOLDER = "{anki_sdk_aqt_path}"


def anki_sdk_reference_paths(home: Path | None = None) -> tuple[str, str]:
    root = Path(os.environ["DECKHAND_ANKI_PROGRAM_FILES"]) if home is None and os.environ.get("DECKHAND_ANKI_PROGRAM_FILES") else (home or Path.home()) / "Library" / "Application Support" / "AnkiProgramFiles"
    site_packages = root / ".venv" / "lib" / "python3.13" / "site-packages"
    return str(site_packages / "anki"), str(site_packages / "aqt")


def anki_run_python_tool_description(home: Path | None = None, *, resolve_paths: bool = True) -> str:
    anki_path, aqt_path = anki_sdk_reference_paths(home) if resolve_paths else (
        ANKI_SDK_ANKI_PATH_PLACEHOLDER,
        ANKI_SDK_AQT_PATH_PLACEHOLDER,
    )
    return (
        "Run Python inside Anki's main process. Use this instead of general Python for Anki "
        "inspection or mutation; code has normal imports plus mw/aqt access and returns the variable "
        "named result. Prefer Anki APIs via mw/aqt; do not edit the collection SQLite database or media "
        "folder directly for mutations; run UI-affecting work on Anki's main Qt thread. For rendered "
        "card/browser UI inspection, use the bundled code-mode SDK instead of separate WebEngine MCP "
        "tools: import deckhand.web as web; web.status(); web.pages(); p = web.page(preferred=\"main\"); "
        "p.text(max_chars=None); p.snapshot(max_elements=200, max_tree_nodes=250, file=None); "
        "p.html(file=None); p.screenshot(file, format=\"png\"); p.eval(script); "
        "p.click(uid=..., selector=..., text=..., x=..., y=...); "
        "p.type(text, uid=..., selector=..., clear=False); p.press(key); "
        "p.wait_for(selector=..., text=..., expression=...). Examples: result = web.page().snapshot(); "
        "result = web.page().screenshot(\"/tmp/anki-card.png\"); "
        "result = web.page().html(file=\"/tmp/anki.html\"); "
        "result = web.page().eval(\"document.body.innerText\"). Discover more with: import inspect, "
        "deckhand.web as web; result = {\"doc\": inspect.getdoc(web), \"members\": [x for x in dir(web) if not x.startswith(\"_\")]}. "
        "For text-to-speech generation, use the bundled SDK and user-managed provider settings: import deckhand.tts as tts; "
        "result = tts.render(provider=\"elevenlabs\", text=\"hello\", out=\"/tmp/hello.mp3\"); inspect dynamic provider schemas with tts.schema() "
        "or anki_runtime_info.deckhand.ttsSurface. "
        "Small results are returned in a result envelope. For large fields, rendered HTML, bulk note data, "
        "logs, or diagnostics, pass resultFilePath/resultFormat or use deckhand.web file= helpers so full "
        "data is written as a local artifact instead of being omitted from the inline MCP response. For local Anki SDK/source reference, inspect "
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
    family = "_".join(name.split("_")[:2])
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

COMMAND_CATALOG: tuple[CommandCatalogEntry, ...] = (
    _entry("anki_app_get_state", "read", "Return current app, reviewer, editor, browser, deck, and profile state.", status="implemented"),
    _entry("anki_context_get_profile", "read", "Return active profile, collection, scheduler, and add-on runtime identity.", status="implemented"),
    _entry("anki_note_search", "read", "Search notes and return note identifiers.", status="implemented", input_schema=_schema({"query": QUERY, "limit": {"type": "integer", "minimum": 1}}, ["query"])),
    _entry("anki_note_get", "read", "Read fields, tags, deck, and model metadata for one note.", status="implemented", input_schema=_schema({"noteId": NOTE_ID}, ["noteId"])),
    _entry("anki_note_create", "mutation", "Create a note.", status="implemented", input_schema=_schema({"deck": DECK_NAME, "model": MODEL_NAME, "fields": FIELDS, "tags": {"type": "array", "items": TAG}}, ["deck", "model", "fields"])),
    _entry("anki_note_update_fields", "mutation", "Update explicitly requested note fields.", status="implemented", input_schema=_schema({"noteId": NOTE_ID, "fields": FIELDS}, ["noteId", "fields"])),
    _entry("anki_note_add_tag", "mutation", "Add a tag to a note.", status="implemented", input_schema=_schema({"noteId": NOTE_ID, "tag": TAG}, ["noteId", "tag"])),
    _entry("anki_note_remove_tag", "mutation", "Remove a tag from a note.", status="implemented", input_schema=_schema({"noteId": NOTE_ID, "tag": TAG}, ["noteId", "tag"])),
    _entry("anki_note_set_tags", "mutation", "Replace a note's tags.", status="implemented", input_schema=_schema({"noteId": NOTE_ID, "tags": TAGS}, ["noteId", "tags"])),
    _entry("anki_note_delete", "destructive", "Delete one or more notes.", status="implemented", input_schema=_schema({"noteIds": NOTE_IDS, "cap": {"type": "integer", "minimum": 1, "maximum": 100}}, ["noteIds"])),
    _entry("anki_card_get", "read", "Read card metadata, state, queue, due, and linked note id.", status="implemented", input_schema=_schema({"cardId": CARD_ID}, ["cardId"])),
    _entry("anki_card_find_by_note", "read", "Return cards generated by a note.", status="implemented", input_schema=_schema({"noteId": NOTE_ID}, ["noteId"])),
    _entry("anki_card_preview", "read", "Render card front/back preview for inspection.", status="implemented", input_schema=_schema({"cardId": CARD_ID}, ["cardId"])),
    _entry("anki_card_suspend", "mutation", "Suspend cards.", status="implemented", input_schema=_schema({"cardIds": CARD_IDS}, ["cardIds"])),
    _entry("anki_card_unsuspend", "mutation", "Unsuspend cards.", status="implemented", input_schema=_schema({"cardIds": CARD_IDS}, ["cardIds"])),
    _entry("anki_card_bury", "mutation", "Bury cards until the next day.", status="implemented", input_schema=_schema({"cardIds": CARD_IDS}, ["cardIds"])),
    _entry("anki_card_unbury", "mutation", "Unbury cards.", status="implemented", input_schema=_schema({"cardIds": CARD_IDS}, ["cardIds"])),
    _entry("anki_card_set_due", "mutation", "Set due dates for cards.", status="implemented", input_schema=_schema({"cardIds": CARD_IDS, "days": {"type": "integer"}}, ["cardIds", "days"])),
    _entry("anki_deck_list", "read", "List decks and identifiers.", status="implemented"),
    _entry("anki_deck_get_stats", "read", "Return deck counts and scheduler stats.", status="implemented", input_schema=_schema({"deckId": {"type": "integer", "minimum": 1}})),
    _entry("anki_deck_create", "mutation", "Create a deck.", status="implemented", input_schema=_schema({"name": DECK_NAME}, ["name"])),
    _entry("anki_model_list", "read", "List note types and identifiers.", status="implemented"),
    _entry("anki_model_get", "read", "Inspect one note type.", status="implemented", input_schema=_schema({"modelName": MODEL_NAME, "modelId": {"type": "integer", "minimum": 1}})),
    _entry("anki_media_add_file", "mutation", "Import a local media file with attachment provenance.", status="implemented", input_schema=_schema({"path": FILE_PATH, "sourceKind": {"type": "string"}, "provenance": {"type": "object"}}, ["path"])),
    _entry("anki_media_get", "read", "Read media metadata and safe preview path.", status="implemented", input_schema=_schema({"filename": FILE_PATH}, ["filename"])),
    _entry("anki_media_attach_to_field", "mutation", "Attach media markup to a note field.", status="implemented", input_schema=_schema({"noteId": NOTE_ID, "field": FIELD_NAME, "filename": FILE_PATH, "mediaType": {"type": "string"}}, ["noteId", "field", "filename"])),
    _entry("anki_export_notes", "read", "Export notes matching an Anki search query to a required local file path. Returns artifact metadata only, not file contents.", status="implemented", input_schema=_schema({"query": QUERY, "filePath": FILE_PATH, "format": {"type": "string", "enum": ["csv", "json"]}, "limit": {"type": "integer", "minimum": 1}, "overwrite": {"type": "boolean"}}, ["query", "filePath"]), evidence="artifact"),
    _entry("anki_export_deck_snapshot", "read", "Write a Deckhand JSON audit snapshot of decks, models, and deck stats to a required local file path. Returns artifact metadata and summary only.", status="implemented", input_schema=_schema({"filePath": FILE_PATH, "overwrite": {"type": "boolean"}}, ["filePath"]), evidence="artifact"),
    _entry("anki_export_deck_package", "read", "Export an Anki deck package to a required local file path using Anki's native package export APIs. Returns artifact metadata only.", status="implemented", input_schema=_schema({"filePath": FILE_PATH, "deck": {"type": "string"}, "includeMedia": {"type": "boolean"}, "includeScheduling": {"type": "boolean"}, "overwrite": {"type": "boolean"}}, ["filePath"]), evidence="artifact"),
    _entry("anki_export_collection_package", "read", "Export a modern Anki collection package to a required local file path using Anki's native collection export API. Returns artifact metadata only.", status="implemented", input_schema=_schema({"filePath": FILE_PATH, "includeMedia": {"type": "boolean"}, "overwrite": {"type": "boolean"}}, ["filePath"]), evidence="artifact"),
    _entry("anki_backup_create", "mutation", "Create a native Anki collection backup in a required local folder using Anki's backup API. This backs up the collection database only; it does not include media files. Use before major collection operations such as bulk edits, deletes, imports, template changes, or scheduling changes. If the result has created:false, inspect status/reason: Anki may have safely skipped backup creation because the collection had no changes. For media-inclusive safety before media mutations, export a deck or collection package with includeMedia:true instead.", status="implemented", input_schema=_schema({"folderPath": FOLDER_PATH, "force": {"type": "boolean"}, "waitForCompletion": {"type": "boolean"}}, ["folderPath"]), evidence="artifact"),
    _entry(
        "anki_run_python",
        "dev_exec",
        anki_run_python_tool_description(resolve_paths=False),
        status="implemented",
        input_schema=_schema(
            {
                "snippet": {"type": "string", "minLength": 1},
                "resultFilePath": {"type": "string", "minLength": 1, "description": "Optional local path for writing the full result artifact."},
                "resultFormat": {"type": "string", "enum": ["json", "text"], "description": "Artifact/measurement format. Defaults to json."},
                "inlineLimitBytes": {"type": "integer", "minimum": 0, "maximum": 64000, "description": "Maximum inline result bytes before omission. Defaults to 12000 and is capped at 64000."},
            },
            ["snippet"],
        ),
    ),
    _entry("anki_runtime_info", "read", "Return compact live Anki, Python, Qt, profile, collection, media, add-on, and local SDK path information.", status="implemented"),
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
        if entry.family != "_".join(entry.name.split("_")[:2]):
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
