from __future__ import annotations

from typing import Any


def current_context(mw: Any) -> dict[str, Any]:
    selection = current_selection(mw)
    profile = current_profile(mw)
    return {
        "screen": _screen(mw),
        "profile": profile,
        "deck": _current_deck(mw),
        "reviewer": _reviewer_state(mw),
        "browser": _browser_state(mw),
        "editor": _editor_state(mw),
        "selection": selection,
    }


def current_selection(mw: Any) -> dict[str, Any]:
    browser = _browser(mw)
    note_ids = _call_list(browser, "selectedNotes")
    card_ids = _call_list(browser, "selectedCards")
    return {
        "noteIds": [int(note_id) for note_id in note_ids],
        "cardIds": [int(card_id) for card_id in card_ids],
        "focusedField": _focused_field(mw),
    }


def current_profile(mw: Any) -> dict[str, Any]:
    profile_manager = getattr(mw, "pm", None)
    collection = getattr(mw, "col", None)
    return {
        "name": _call_or_attr(profile_manager, "name"),
        "base": _call_or_attr(profile_manager, "base"),
        "collectionOpen": collection is not None,
        "schedulerVersion": _scheduler_version(collection),
    }


def _screen(mw: Any) -> str:
    state = _call_or_attr(mw, "state")
    if state:
        return str(state)
    if getattr(mw, "reviewer", None) and getattr(getattr(mw, "reviewer", None), "card", None):
        return "reviewer"
    if _browser(mw) is not None:
        return "browser"
    return "unknown"


def _current_deck(mw: Any) -> dict[str, Any] | None:
    collection = getattr(mw, "col", None)
    try:
        deck_id = int(collection.decks.selected())
        return {"id": deck_id, "name": str(collection.decks.name(deck_id))}
    except Exception:
        return None


def _reviewer_state(mw: Any) -> dict[str, Any] | None:
    reviewer = getattr(mw, "reviewer", None)
    card = getattr(reviewer, "card", None)
    if card is None:
        return None
    return {
        "cardId": _int_attr(card, "id"),
        "noteId": _int_attr(card, "nid"),
        "deckId": _int_attr(card, "did"),
        "queue": _int_attr(card, "queue"),
        "due": _int_attr(card, "due"),
    }


def _browser_state(mw: Any) -> dict[str, Any] | None:
    browser = _browser(mw)
    if browser is None:
        return None
    return {
        "search": _search_text(browser),
        "selectedNoteIds": _call_list(browser, "selectedNotes"),
        "selectedCardIds": _call_list(browser, "selectedCards"),
    }


def _editor_state(mw: Any) -> dict[str, Any] | None:
    editor = _editor(mw)
    if editor is None:
        return None
    note = getattr(editor, "note", None)
    return {
        "noteId": _int_attr(note, "id"),
        "focusedField": _focused_field(mw),
    }


def _browser(mw: Any) -> Any | None:
    app = getattr(mw, "app", None)
    try:
        for widget in app.topLevelWidgets():
            if widget.__class__.__name__.lower().endswith("browser"):
                return widget
    except Exception:
        pass
    return getattr(mw, "browser", None)


def _editor(mw: Any) -> Any | None:
    return getattr(mw, "editor", None)


def _focused_field(mw: Any) -> str | None:
    editor = _editor(mw)
    try:
        current_field = editor.currentField
        if isinstance(current_field, int) and getattr(editor, "note", None) is not None:
            return list(editor.note.keys())[current_field]
        if current_field is not None:
            return str(current_field)
    except Exception:
        pass
    return None


def _search_text(browser: Any) -> str | None:
    search = getattr(browser, "search", None)
    if hasattr(search, "text"):
        try:
            return str(search.text())
        except Exception:
            return None
    return _call_or_attr(browser, "searchText")


def _scheduler_version(collection: Any) -> int | None:
    try:
        return int(collection.sched.version)
    except Exception:
        return None


def _call_or_attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if callable(value):
        try:
            return value()
        except TypeError:
            return None
    return value


def _maybe_call(obj: Any, name: str, *args: Any) -> Any:
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if callable(value):
        return value(*args)
    return None


def _call_list(obj: Any, name: str) -> list[Any]:
    value = _maybe_call(obj, name)
    if value is None:
        return []
    return list(value)


def _int_attr(obj: Any, name: str) -> int | None:
    try:
        value = getattr(obj, name)
        if callable(value):
            value = value()
        return int(value)
    except Exception:
        return None
