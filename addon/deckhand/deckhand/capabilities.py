from __future__ import annotations

from dataclasses import asdict, dataclass

from .command_catalog import command_catalog, command_catalog_payload


@dataclass(frozen=True)
class ToolCapability:
    name: str
    risk: str
    path: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def is_anki_bridge_tool_name(name: str) -> bool:
    return name.startswith("anki.")


def anki_bridge_capabilities() -> list[ToolCapability]:
    # "safe_bridge" is internal add-on-to-companion routing metadata, not a public MCP protocol.
    return [
        ToolCapability(
            name=entry.name,
            risk=entry.risk,
            path="safe_bridge",
            description=entry.description,
        )
        for entry in command_catalog()
        if is_anki_bridge_tool_name(entry.name)
        and entry.status == "implemented"
        and "safe_bridge" in entry.paths
    ]


def capability_payload() -> dict[str, object]:
    return {
        "paths": ["safe_bridge"],
        "catalog": command_catalog_payload(),
        "tools": [capability.to_dict() for capability in anki_bridge_capabilities()],
    }


def anki_bridge_capability_payload() -> dict[str, object]:
    return capability_payload()
