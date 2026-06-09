from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .command_catalog import command_catalog
from .state_paths import work_root

VISIBILITY_PATH = work_root() / "tool-visibility.json"
MINIMAL_TEMPLATE_TOOLS = frozenset({"anki.execute", "anki.runtime.info"})
TEMPLATE_ALL = "all"
TEMPLATE_RUNTIME_WEBENGINE = "runtime_webengine"
TEMPLATE_NONE = "none"


def public_tool_names() -> list[str]:
    return [
        entry.name
        for entry in command_catalog()
        if entry.status == "implemented"
        and entry.name.startswith("anki.")
        and "safe_bridge" in entry.paths
        and not entry.name.startswith("anki.bridge.")
        and ".smoke." not in entry.name
    ]


def visible_tool_names(all_names: Iterable[str] | None = None) -> list[str]:
    names = sorted(set(all_names or public_tool_names()))
    settings = _read_settings()
    configured = settings.get("visibleTools")
    if not isinstance(configured, list):
        return names
    visible = {str(name) for name in configured}
    return [name for name in names if name in visible]


def is_tool_visible(name: str) -> bool:
    return name in set(visible_tool_names())


def save_visible_tool_names(names: Iterable[str]) -> dict[str, object]:
    public = set(public_tool_names())
    visible = sorted({str(name) for name in names if str(name) in public})
    payload = {"visibleTools": visible}
    VISIBILITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    VISIBILITY_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def template_tool_names(template: str, all_names: Iterable[str] | None = None) -> list[str]:
    names = sorted(set(all_names or public_tool_names()))
    if template == TEMPLATE_RUNTIME_WEBENGINE:
        return [
            name
            for name in names
            if name in MINIMAL_TEMPLATE_TOOLS or name.startswith("anki.webengine.")
        ]
    if template == TEMPLATE_NONE:
        return []
    return names


def apply_template(template: str) -> dict[str, object]:
    return save_visible_tool_names(template_tool_names(template))


def _read_settings() -> dict[str, object]:
    try:
        payload = json.loads(Path(VISIBILITY_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
