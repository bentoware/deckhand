from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import typed_tools


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class AttachmentRecord:
    id: str
    original_path: str
    filename: str
    source_kind: str
    destination_kind: str
    size: int
    sha256: str
    mime: str
    created_at_ms: int
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AttachmentStore:
    def __init__(self) -> None:
        self._records: dict[str, AttachmentRecord] = {}

    def record(
        self,
        path: Path,
        *,
        source_kind: str,
        destination_kind: str,
        provenance: dict[str, Any] | None = None,
    ) -> AttachmentRecord:
        digest = sha256_file(path)
        record = AttachmentRecord(
            id=digest[:16],
            original_path=str(path),
            filename=path.name,
            source_kind=source_kind,
            destination_kind=destination_kind,
            size=path.stat().st_size,
            sha256=digest,
            mime=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            created_at_ms=int(time.time() * 1000),
            provenance=provenance or {},
        )
        self._records[record.id] = record
        return record

    def all(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self._records.values()]


def sanitize_filename(filename: str) -> str:
    base = Path(filename).name.strip().replace(" ", "_")
    base = SAFE_NAME_RE.sub("_", base)
    if base in {"", ".", ".."}:
        base = "attachment"
    if base.startswith("."):
        base = "file" + base
    return base[:120]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_file(
    mw: Any,
    store: AttachmentStore,
    path: str,
    *,
    source_kind: str = "user_input",
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(str(source))
    safe_name = sanitize_filename(source.name)
    staging = source
    if safe_name != source.name:
        staging = source.with_name(safe_name)
        shutil.copyfile(source, staging)
    filename = _media_add_file(mw, staging)
    media_path = _media_path(mw, filename)
    attachment = store.record(
        source,
        source_kind=source_kind,
        destination_kind="anki_media",
        provenance={**(provenance or {}), "ankiFilename": filename},
    )
    if staging != source:
        staging.unlink(missing_ok=True)
    return {
        "filename": filename,
        "mediaPath": str(media_path) if media_path else None,
        "attachment": attachment.to_dict(),
    }


def get(mw: Any, filename: str) -> dict[str, Any]:
    safe = sanitize_filename(filename)
    path = _media_path(mw, safe)
    exists = bool(path and path.exists())
    return {
        "filename": safe,
        "exists": exists,
        "path": str(path) if path else None,
        "size": path.stat().st_size if exists and path else None,
        "sha256": sha256_file(path) if exists and path else None,
        "mime": mimetypes.guess_type(safe)[0] or "application/octet-stream",
    }


def attach_to_field(
    mw: Any,
    note_id: int,
    field: str,
    filename: str,
    *,
    media_type: str | None = None,
) -> dict[str, Any]:
    safe = sanitize_filename(filename)
    note = mw.col.get_note(int(note_id))
    before = str(note[field])
    markup = _field_markup(safe, media_type)
    note[field] = before + markup
    typed_tools.save_note(mw, note)
    _reset_mw(mw)
    return {"noteId": int(note_id), "field": field, "filename": safe, "markup": markup}


def _field_markup(filename: str, media_type: str | None) -> str:
    mime = media_type or mimetypes.guess_type(filename)[0] or ""
    if mime.startswith("image/") or filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
        return f'<img src="{filename}">'
    if mime.startswith("audio/") or filename.lower().endswith((".mp3", ".wav", ".ogg", ".m4a")):
        return f"[sound:{filename}]"
    return f'<a href="{filename}">{filename}</a>'


def _media_add_file(mw: Any, path: Path) -> str:
    media = mw.col.media
    if hasattr(media, "add_file"):
        return str(media.add_file(str(path)))
    if hasattr(media, "write_data"):
        filename = sanitize_filename(path.name)
        media.write_data(filename, path.read_bytes())
        return filename
    raise RuntimeError("media_add_unavailable")


def _media_path(mw: Any, filename: str) -> Path | None:
    media = mw.col.media
    try:
        return Path(media.dir()) / filename
    except Exception:
        return None


def _reset_mw(mw: Any) -> None:
    reset = getattr(mw, "reset", None)
    if callable(reset):
        reset()
