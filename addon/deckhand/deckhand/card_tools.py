from __future__ import annotations

from typing import Any


def card_get(mw: Any, card_id: int) -> dict[str, Any]:
    card = mw.col.get_card(int(card_id))
    return _card_record(mw, card)


def card_find_by_note(mw: Any, note_id: int) -> dict[str, Any]:
    note = mw.col.get_note(int(note_id))
    card_ids = [int(card_id) for card_id in note.card_ids()]
    return {"noteId": int(note_id), "cardIds": card_ids, "count": len(card_ids)}


def card_preview(mw: Any, card_id: int) -> dict[str, Any]:
    card = mw.col.get_card(int(card_id))
    question = _call_or_attr(card, "question") or _call_or_attr(card, "q") or ""
    answer = _call_or_attr(card, "answer") or _call_or_attr(card, "a") or ""
    return {"cardId": int(card_id), "front": str(question), "back": str(answer)}


def card_suspend(mw: Any, card_ids: list[int]) -> dict[str, Any]:
    ids = _ids(card_ids)
    before = [card_get(mw, card_id) for card_id in ids]
    _sched_call(mw, "suspend_cards", ids)
    return {"cardIds": ids, "before": before, "after": [card_get(mw, card_id) for card_id in ids]}


def card_unsuspend(mw: Any, card_ids: list[int]) -> dict[str, Any]:
    ids = _ids(card_ids)
    before = [card_get(mw, card_id) for card_id in ids]
    _sched_call(mw, "unsuspend_cards", ids)
    return {"cardIds": ids, "before": before, "after": [card_get(mw, card_id) for card_id in ids]}


def card_bury(mw: Any, card_ids: list[int]) -> dict[str, Any]:
    ids = _ids(card_ids)
    before = [card_get(mw, card_id) for card_id in ids]
    _sched_call(mw, "bury_cards", ids)
    return {"cardIds": ids, "before": before, "after": [card_get(mw, card_id) for card_id in ids]}


def card_unbury(mw: Any, card_ids: list[int]) -> dict[str, Any]:
    ids = _ids(card_ids)
    before = [card_get(mw, card_id) for card_id in ids]
    _sched_call(mw, "unbury_cards", ids)
    return {"cardIds": ids, "before": before, "after": [card_get(mw, card_id) for card_id in ids]}


def card_set_due(mw: Any, card_ids: list[int], days: int) -> dict[str, Any]:
    ids = _ids(card_ids)
    before = [card_get(mw, card_id) for card_id in ids]
    _sched_call(mw, "set_due_date", ids, str(int(days)))
    return {"cardIds": ids, "days": int(days), "before": before, "after": [card_get(mw, card_id) for card_id in ids]}


def _card_record(mw: Any, card: Any) -> dict[str, Any]:
    return {
        "cardId": _int_attr(card, "id"),
        "noteId": _int_attr(card, "nid"),
        "deckId": _int_attr(card, "did"),
        "queue": _int_attr(card, "queue"),
        "type": _int_attr(card, "type"),
        "due": _int_attr(card, "due"),
        "interval": _int_attr(card, "ivl"),
        "factor": _int_attr(card, "factor"),
        "suspended": _int_attr(card, "queue") == -1,
        "deck": _deck_name(mw, _int_attr(card, "did")),
    }


def _sched_call(mw: Any, name: str, *args: Any) -> Any:
    sched = getattr(mw.col, "sched", None)
    fn = getattr(sched, name, None)
    if callable(fn):
        result = fn(*args)
        _reset_mw(mw)
        return result
    raise RuntimeError(f"scheduler_method_unavailable: {name}")


def _ids(values: list[int]) -> list[int]:
    ids = [int(value) for value in values]
    if not ids:
        raise ValueError("cardIds required")
    if len(ids) > 50:
        raise ValueError("card cap exceeded: 50")
    return ids


def _deck_name(mw: Any, deck_id: int | None) -> str | None:
    if deck_id is None:
        return None
    try:
        return str(mw.col.decks.name(deck_id))
    except Exception:
        return None


def _call_or_attr(obj: Any, name: str) -> Any:
    value = getattr(obj, name, None)
    if callable(value):
        return value()
    return value


def _int_attr(obj: Any, name: str) -> int | None:
    try:
        value = getattr(obj, name)
        if callable(value):
            value = value()
        return int(value)
    except Exception:
        return None


def _reset_mw(mw: Any) -> None:
    reset = getattr(mw, "reset", None)
    if callable(reset):
        reset()
