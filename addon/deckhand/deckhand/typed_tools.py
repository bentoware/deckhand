from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class NoteRecord:
    id: int
    fields: dict[str, str]
    tags: list[str] = field(default_factory=list)
    deck: str = "Default"
    model: str = "Basic"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "fields": dict(self.fields),
            "tags": list(self.tags),
            "deck": self.deck,
            "model": self.model,
        }


class NoteStore(Protocol):
    def search(self, query: str, limit: int) -> list[int]: ...
    def get(self, note_id: int) -> NoteRecord: ...
    def create(self, deck: str, model: str, fields: dict[str, str], tags: list[str]) -> NoteRecord: ...
    def update_fields(self, note_id: int, fields: dict[str, str]) -> NoteRecord: ...
    def add_tag(self, note_id: int, tag: str) -> NoteRecord: ...
    def remove_tag(self, note_id: int, tag: str) -> NoteRecord: ...
    def set_tags(self, note_id: int, tags: list[str]) -> NoteRecord: ...
    def delete(self, note_ids: list[int]) -> list[int]: ...


class FakeNoteStore:
    def __init__(self, notes: list[NoteRecord] | None = None) -> None:
        self._notes = {note.id: note for note in notes or []}

    def search(self, query: str, limit: int) -> list[int]:
        needle = query.lower()
        matches = []
        for note in self._notes.values():
            haystack = " ".join([*note.fields.values(), *note.tags, note.deck, note.model]).lower()
            if not needle or needle in haystack:
                matches.append(note.id)
        return matches[: max(1, min(limit, 100))]

    def get(self, note_id: int) -> NoteRecord:
        return self._notes[note_id]

    def create(self, deck: str, model: str, fields: dict[str, str], tags: list[str]) -> NoteRecord:
        note_id = max(self._notes.keys(), default=0) + 1
        note = NoteRecord(note_id, dict(fields), sorted(set(tags)), deck, model)
        self._notes[note_id] = note
        return note

    def update_fields(self, note_id: int, fields: dict[str, str]) -> NoteRecord:
        note = self.get(note_id)
        for name, value in fields.items():
            if name not in note.fields:
                raise KeyError(f"unknown field: {name}")
            note.fields[name] = value
        return note

    def add_tag(self, note_id: int, tag: str) -> NoteRecord:
        note = self.get(note_id)
        if tag not in note.tags:
            note.tags.append(tag)
            note.tags.sort()
        return note

    def remove_tag(self, note_id: int, tag: str) -> NoteRecord:
        note = self.get(note_id)
        note.tags = [existing for existing in note.tags if existing != tag]
        return note

    def set_tags(self, note_id: int, tags: list[str]) -> NoteRecord:
        note = self.get(note_id)
        note.tags = sorted(set(tags))
        return note

    def delete(self, note_ids: list[int]) -> list[int]:
        deleted = []
        for note_id in note_ids:
            if note_id in self._notes:
                deleted.append(note_id)
                del self._notes[note_id]
        return deleted


class AnkiCollectionNoteStore:
    def __init__(self, mw: Any) -> None:
        self._mw = mw

    def search(self, query: str, limit: int) -> list[int]:
        note_ids = list(self._mw.col.find_notes(query))
        return [int(note_id) for note_id in note_ids[: max(1, min(limit, 100))]]

    def get(self, note_id: int) -> NoteRecord:
        note = self._mw.col.get_note(note_id)
        fields = {name: note[name] for name in note.keys()}
        tags = sorted(str(tag) for tag in note.tags)
        deck = _deck_name_for_note(self._mw.col, note)
        model = _model_name_for_note(note)
        return NoteRecord(id=int(note_id), fields=fields, tags=tags, deck=deck, model=model)

    def create(self, deck: str, model: str, fields: dict[str, str], tags: list[str]) -> NoteRecord:
        note_type = self._mw.col.models.by_name(model)
        if note_type is None:
            raise KeyError(f"unknown model: {model}")
        deck_id = self._mw.col.decks.id(deck)
        note = self._mw.col.new_note(note_type)
        for name, value in fields.items():
            if name not in note.keys():
                raise KeyError(f"unknown field: {name}")
            note[name] = value
        for tag in tags:
            note.add_tag(tag)
        self._mw.col.add_note(note, deck_id)
        _reset_mw(self._mw)
        return self.get(int(note.id))

    def update_fields(self, note_id: int, fields: dict[str, str]) -> NoteRecord:
        note = self._mw.col.get_note(note_id)
        for name, value in fields.items():
            if name not in note.keys():
                raise KeyError(f"unknown field: {name}")
            note[name] = value
        save_note(self._mw, note)
        _reset_mw(self._mw)
        return self.get(note_id)

    def add_tag(self, note_id: int, tag: str) -> NoteRecord:
        note = self._mw.col.get_note(note_id)
        note.add_tag(tag)
        save_note(self._mw, note)
        _reset_mw(self._mw)
        return self.get(note_id)

    def remove_tag(self, note_id: int, tag: str) -> NoteRecord:
        note = self._mw.col.get_note(note_id)
        tags = [existing for existing in note.tags if str(existing) != tag]
        _set_note_tags(note, tags)
        save_note(self._mw, note)
        _reset_mw(self._mw)
        return self.get(note_id)

    def set_tags(self, note_id: int, tags: list[str]) -> NoteRecord:
        note = self._mw.col.get_note(note_id)
        _set_note_tags(note, sorted(set(tags)))
        save_note(self._mw, note)
        _reset_mw(self._mw)
        return self.get(note_id)

    def delete(self, note_ids: list[int]) -> list[int]:
        ids = [int(note_id) for note_id in note_ids]
        if hasattr(self._mw.col, "remove_notes"):
            self._mw.col.remove_notes(ids)
        elif hasattr(self._mw.col, "rem_notes"):
            self._mw.col.rem_notes(ids)
        else:
            raise RuntimeError("note_delete_unavailable")
        _reset_mw(self._mw)
        return ids


def collection_store_from_anki() -> AnkiCollectionNoteStore | None:
    try:
        from aqt import mw

        if getattr(mw, "col", None) is None:
            return None
        return AnkiCollectionNoteStore(mw)
    except Exception:
        return None


def note_search(store: NoteStore, query: str, limit: int = 20) -> dict[str, object]:
    ids = store.search(query, limit)
    return {"query": query, "noteIds": ids, "count": len(ids)}


def note_get(store: NoteStore, note_id: int) -> dict[str, object]:
    return store.get(note_id).to_dict()


def note_create(
    store: NoteStore,
    deck: str,
    model: str,
    fields: dict[str, str],
    tags: list[str] | None = None,
) -> dict[str, object]:
    note = store.create(deck, model, fields, sorted(set(tags or [])))
    return {"note": note.to_dict(), "created": True}


def note_update_fields(store: NoteStore, note_id: int, fields: dict[str, str]) -> dict[str, object]:
    updated = store.update_fields(note_id, fields)
    return {"note": updated.to_dict(), "updatedFields": sorted(fields)}


def note_add_tag(store: NoteStore, note_id: int, tag: str) -> dict[str, object]:
    updated = store.add_tag(note_id, tag)
    return {"note": updated.to_dict(), "addedTag": tag}


def note_remove_tag(store: NoteStore, note_id: int, tag: str) -> dict[str, object]:
    updated = store.remove_tag(note_id, tag)
    return {"note": updated.to_dict(), "removedTag": tag}


def note_set_tags(store: NoteStore, note_id: int, tags: list[str]) -> dict[str, object]:
    desired = sorted(set(tags))
    updated = store.set_tags(note_id, desired)
    return {"note": updated.to_dict(), "tags": desired}


def note_delete(store: NoteStore, note_ids: list[int], cap: int = 20) -> dict[str, object]:
    ids = [int(note_id) for note_id in note_ids]
    if len(ids) > cap:
        raise ValueError(f"delete cap exceeded: {len(ids)} > {cap}")
    deleted = store.delete(ids)
    return {"deletedNoteIds": deleted, "count": len(deleted)}


def _deck_name_for_note(collection: Any, note: Any) -> str:
    try:
        card_ids = note.card_ids()
        if not card_ids:
            return "Unknown"
        card = collection.get_card(card_ids[0])
        return str(collection.decks.name(card.did))
    except Exception:
        return "Unknown"


def _model_name_for_note(note: Any) -> str:
    try:
        return str(note.note_type()["name"])
    except Exception:
        return "Unknown"


def save_note(mw: Any, note: Any) -> None:
    update_note = getattr(getattr(mw, "col", None), "update_note", None)
    if callable(update_note):
        update_note(note)
        return
    note_col = getattr(note, "col", None)
    update_note = getattr(note_col, "update_note", None)
    if callable(update_note):
        update_note(note)
        return
    flush = getattr(note, "flush", None)
    if callable(flush):
        flush()
        return
    raise RuntimeError("note_update_unavailable")


def _reset_mw(mw: Any) -> None:
    reset = getattr(mw, "reset", None)
    if callable(reset):
        reset()


def _set_note_tags(note: Any, tags: list[str]) -> None:
    if hasattr(note, "tags"):
        note.tags = list(tags)
    elif hasattr(note, "set_tags_from_str"):
        note.set_tags_from_str(" ".join(tags))
    else:
        raise RuntimeError("note_tags_unavailable")
