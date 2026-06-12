from __future__ import annotations

from typing import Iterable

PUBLIC_MCP_TOOLS = frozenset({"anki_backup_create", "anki_run_python", "anki_runtime_info"})


def public_tool_names() -> list[str]:
    return sorted(PUBLIC_MCP_TOOLS)


def visible_tool_names(all_names: Iterable[str] | None = None) -> list[str]:
    names = sorted(set(all_names or public_tool_names()))
    return [name for name in names if name in PUBLIC_MCP_TOOLS]


def is_tool_visible(name: str) -> bool:
    return name in PUBLIC_MCP_TOOLS


def template_tool_names(template: str, all_names: Iterable[str] | None = None) -> list[str]:
    return visible_tool_names(all_names)
