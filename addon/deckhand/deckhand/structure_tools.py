from __future__ import annotations

from typing import Any


def deck_list(mw: Any) -> dict[str, Any]:
    decks = []
    try:
        items = mw.col.decks.all_names_and_ids()
        decks = [{"id": int(item.id), "name": str(item.name)} for item in items]
    except Exception:
        decks = [{"id": int(deck["id"]), "name": str(deck["name"])} for deck in mw.col.decks.all()]
    return {"decks": decks, "count": len(decks)}


def deck_get_stats(mw: Any, deck_id: int | None = None) -> dict[str, Any]:
    selected = int(deck_id or mw.col.decks.selected())
    name = str(mw.col.decks.name(selected))
    counts = _deck_counts(mw, selected)
    result: dict[str, Any] = {"deck": {"id": selected, "name": name}, "counts": counts}
    if counts is None:
        result["countsUnavailable"] = True
    return result


def deck_create(mw: Any, name: str) -> dict[str, Any]:
    deck_id = int(mw.col.decks.id(name))
    _reset_mw(mw)
    return {"deck": {"id": deck_id, "name": name}, "created": True}


def model_list(mw: Any) -> dict[str, Any]:
    models = [{"id": int(model["id"]), "name": str(model["name"])} for model in _all_models(mw)]
    return {"models": models, "count": len(models)}


def model_get(mw: Any, model_name: str | None = None, model_id: int | None = None) -> dict[str, Any]:
    model = _model(mw, model_name, model_id)
    return _model_record(model)


def _deck_counts(mw: Any, deck_id: int) -> dict[str, int] | None:
    sched = getattr(mw.col, "sched", None)
    due_tree = getattr(sched, "deck_due_tree", None)
    if not callable(due_tree):
        return None
    try:
        node = due_tree(int(deck_id))
    except TypeError:
        try:
            node = _find_deck_due_node(due_tree(), int(deck_id))
        except Exception:
            return None
    except Exception:
        return None
    if node is None:
        return None
    try:
        return {
            "new": int(getattr(node, "new_count")),
            "learn": int(getattr(node, "learn_count")),
            "review": int(getattr(node, "review_count")),
        }
    except Exception:
        try:
            new, learn, review = getattr(node, "counts")
            return {"new": int(new), "learn": int(learn), "review": int(review)}
        except Exception:
            return None


def _find_deck_due_node(node: Any, deck_id: int) -> Any | None:
    if int(getattr(node, "deck_id", getattr(node, "id", -1))) == deck_id:
        return node
    for child in getattr(node, "children", []) or []:
        match = _find_deck_due_node(child, deck_id)
        if match is not None:
            return match
    return None


def _all_models(mw: Any) -> list[dict[str, Any]]:
    models = mw.col.models
    if hasattr(models, "all"):
        return list(models.all())
    return list(models.models.values())


def _model(mw: Any, model_name: str | None, model_id: int | None) -> dict[str, Any]:
    if model_id is not None:
        model = mw.col.models.get(int(model_id))
    elif model_name:
        model = mw.col.models.by_name(model_name)
    else:
        model = _all_models(mw)[0]
    if model is None:
        raise KeyError(model_name or model_id)
    return model


def _model_ref(model: dict[str, Any]) -> dict[str, Any]:
    return {"id": int(model["id"]), "name": str(model["name"])}


def _model_record(model: dict[str, Any]) -> dict[str, Any]:
    return {
        **_model_ref(model),
        "fields": [str(field["name"]) for field in model.get("flds", [])],
        "templateCount": len(model.get("tmpls", [])),
        "cssLength": len(str(model.get("css", ""))),
    }


def _reset_mw(mw: Any) -> None:
    reset = getattr(mw, "reset", None)
    if callable(reset):
        reset()
