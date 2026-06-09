from __future__ import annotations

import csv
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from . import structure_tools
from . import typed_tools


def export_notes(
    mw: Any,
    *,
    query: str,
    filePath: str | None = None,
    format: str = "json",
    limit: int = 100,
    overwrite: bool = False,
) -> dict[str, Any]:
    bounded = max(1, min(limit, 1000))
    store = typed_tools.AnkiCollectionNoteStore(mw)
    note_ids = store.search(query, bounded)
    fmt = format.lower()
    if fmt not in {"csv", "json"}:
        raise ValueError("unsupported_export_format")
    target = _required_file_path(filePath, overwrite=overwrite)
    if fmt == "csv" and _native_note_csv_export_available(mw):
        _export_note_csv_native(mw, target, note_ids)
        count = len(note_ids)
    else:
        notes = [store.get(note_id).to_dict() for note_id in note_ids]
        count = len(notes)
        if fmt == "csv":
            _write_notes_csv(target, notes)
        else:
            target.write_text(json.dumps({"query": query, "notes": notes}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "query": query,
        "format": fmt,
        "count": count,
        "noteIds": note_ids,
        "artifact": _artifact_metadata(target, kind="anki_note_export", mime_type="text/csv" if fmt == "csv" else "application/json"),
    }


def deck_snapshot(mw: Any, *, filePath: str | None = None, overwrite: bool = False) -> dict[str, Any]:
    decks = structure_tools.deck_list(mw)
    models = structure_tools.model_list(mw)
    stats = []
    for deck in decks["decks"]:
        try:
            stats.append(structure_tools.deck_get_stats(mw, int(deck["id"])))
        except Exception as exc:
            stats.append({"deck": deck, "error": str(exc)})
    snapshot = {
        "createdAtMs": int(time.time() * 1000),
        "decks": decks["decks"],
        "deckCount": decks["count"],
        "models": models["models"],
        "modelCount": models["count"],
        "stats": stats,
    }
    target = _required_file_path(filePath, overwrite=overwrite)
    target.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "summary": {
            "deckCount": snapshot["deckCount"],
            "modelCount": snapshot["modelCount"],
            "statCount": len(stats),
            "createdAtMs": snapshot["createdAtMs"],
        },
        "artifact": _artifact_metadata(target, kind="anki_deck_snapshot", mime_type="application/json"),
    }


def backup_create(
    mw: Any,
    *,
    folderPath: str | None = None,
    force: bool = True,
    waitForCompletion: bool = True,
) -> dict[str, Any]:
    folder = _required_folder_path(folderPath)
    before = _backup_files(folder)
    created = bool(
        mw.col._backend.create_backup(
            backup_folder=str(folder),
            force=bool(force),
            wait_for_completion=bool(waitForCompletion),
        )
    )
    if waitForCompletion and hasattr(mw.col._backend, "await_backup_completion"):
        mw.col._backend.await_backup_completion()
    after = _backup_files(folder)
    candidate = _newest_file([path for path in after if path not in before] or after)
    result: dict[str, Any] = {
        "ok": True,
        "created": created,
        "folderPath": str(folder),
        "mediaIncluded": False,
    }
    if candidate:
        result["backupPath"] = str(candidate)
        result["artifact"] = _artifact_metadata(candidate, kind="anki_native_backup", mime_type="application/octet-stream")
    return result


def export_deck_package(
    mw: Any,
    *,
    filePath: str | None = None,
    deck: str | None = None,
    includeMedia: bool = True,
    includeScheduling: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    target = _required_file_path(filePath, overwrite=overwrite)
    note_count = _export_anki_package(
        mw,
        target,
        deck=deck,
        include_media=includeMedia,
        include_scheduling=includeScheduling,
    )
    return {
        "ok": True,
        "deck": deck,
        "includeMedia": bool(includeMedia),
        "includeScheduling": bool(includeScheduling),
        "noteCount": int(note_count),
        "artifact": _artifact_metadata(target, kind="anki_deck_package", mime_type="application/octet-stream"),
    }


def export_collection_package(
    mw: Any,
    *,
    filePath: str | None = None,
    includeMedia: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    target = _required_file_path(filePath, overwrite=overwrite)
    backend = getattr(mw.col, "_backend", None)
    if backend is not None and hasattr(backend, "export_collection_package"):
        backend.export_collection_package(
            out_path=str(target),
            include_media=bool(includeMedia),
            legacy=False,
        )
    else:
        from anki.exporting import AnkiCollectionPackage21bExporter

        exporter = AnkiCollectionPackage21bExporter(mw.col)
        exporter.includeMedia = bool(includeMedia)
        exporter.exportInto(str(target))
    return {
        "ok": True,
        "includeMedia": bool(includeMedia),
        "artifact": _artifact_metadata(target, kind="anki_collection_package", mime_type="application/octet-stream"),
    }


def _write_notes_csv(path: Path, notes: list[dict[str, Any]]) -> None:
    field_names = sorted({name for note in notes for name in dict(note.get("fields", {})).keys()})
    columns = ["id", "deck", "model", "tags", *field_names]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for note in notes:
            row = {
                "id": note["id"],
                "deck": note["deck"],
                "model": note["model"],
                "tags": " ".join(note.get("tags", [])),
            }
            row.update(note.get("fields", {}))
            writer.writerow(row)


def _required_file_path(file_path: str | None, *, overwrite: bool) -> Path:
    if not file_path or not str(file_path).strip():
        raise ValueError("filePath_required")
    target = Path(str(file_path)).expanduser()
    if target.exists() and not overwrite:
        raise FileExistsError(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _required_folder_path(folder_path: str | None) -> Path:
    if not folder_path or not str(folder_path).strip():
        raise ValueError("folderPath_required")
    folder = Path(str(folder_path)).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    if not folder.is_dir():
        raise NotADirectoryError(str(folder))
    return folder


def _artifact_metadata(path: Path, *, kind: str, mime_type: str) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path),
        "mimeType": mime_type,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "kind": kind,
    }


def _backup_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return [path for path in folder.iterdir() if path.is_file()]


def _newest_file(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    return max(paths, key=lambda path: path.stat().st_mtime_ns)


def _native_note_csv_export_available(mw: Any) -> bool:
    backend = getattr(getattr(mw, "col", None), "_backend", None)
    if backend is None or not hasattr(backend, "export_note_csv"):
        return False
    try:
        from anki import import_export_pb2, notes_pb2  # noqa: F401
    except Exception:
        return False
    return True


def _export_note_csv_native(mw: Any, target: Path, note_ids: list[int]) -> int:
    from anki import import_export_pb2, notes_pb2

    limit = import_export_pb2.ExportLimit(
        note_ids=notes_pb2.NoteIds(note_ids=[int(note_id) for note_id in note_ids])
    )
    return int(
        mw.col._backend.export_note_csv(
            out_path=str(target),
            with_html=True,
            with_tags=True,
            with_deck=True,
            with_notetype=True,
            with_guid=True,
            limit=limit,
        )
    )


def _export_anki_package(
    mw: Any,
    target: Path,
    *,
    deck: str | None,
    include_media: bool,
    include_scheduling: bool,
) -> int:
    backend = getattr(mw.col, "_backend", None)
    if backend is not None and hasattr(backend, "export_anki_package"):
        try:
            from anki import generic_pb2, import_export_pb2
        except Exception:
            return int(
                backend.export_anki_package(
                    out_path=str(target),
                    options=None,
                    limit=None,
                )
            )

        options = import_export_pb2.ExportAnkiPackageOptions(
            with_scheduling=bool(include_scheduling),
            with_deck_configs=True,
            with_media=bool(include_media),
            legacy=False,
        )
        if deck:
            limit = import_export_pb2.ExportLimit(deck_id=int(mw.col.decks.id(deck)))
        else:
            limit = import_export_pb2.ExportLimit(whole_collection=generic_pb2.Empty())
        return int(backend.export_anki_package(out_path=str(target), options=options, limit=limit))

    from anki.exporting import AnkiPackageExporter

    exporter = AnkiPackageExporter(mw.col)
    exporter.includeMedia = bool(include_media)
    exporter.includeSched = bool(include_scheduling)
    if deck:
        exporter.did = int(mw.col.decks.id(deck))
    exporter.exportInto(str(target))
    return 0
