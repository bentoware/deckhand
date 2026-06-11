"""Build a Claude Desktop MCP Bundle (.mcpb) for Deckhand.

The MCPB spec (github.com/modelcontextprotocol/mcpb) has no remote-server
type, so the bundle ships a tiny Node stdio-to-HTTP proxy (Claude Desktop
bundles Node, so users install nothing). The bundle is generated on demand
with the current endpoint baked in as the default user config.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from . import companion
from . import settings
from .state_paths import work_root
from .version import ADDON_VERSION

BUNDLE_FILENAME = "Deckhand.mcpb"
PROXY_FILENAME = "proxy.js"


def assets_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "mcpb"


def default_bundle_path() -> Path:
    return work_root() / "dist" / BUNDLE_FILENAME


def manifest(endpoint: str | None = None, token: str | None = None) -> dict[str, Any]:
    endpoint = endpoint or f"{companion.companion_url().rstrip('/')}/mcp"
    if token is None and settings.require_mcp_token():
        token = settings.persistent_token()
    return {
        "manifest_version": "0.3",
        "name": "deckhand",
        "display_name": "Deckhand for Anki",
        "version": ADDON_VERSION,
        "description": "Connect Claude to your local Anki collection through the Deckhand add-on.",
        "author": {"name": "Bentoware", "url": "https://github.com/bentoware/deckhand"},
        "server": {
            "type": "node",
            "entry_point": PROXY_FILENAME,
            "mcp_config": {
                "command": "node",
                "args": ["${__dirname}/" + PROXY_FILENAME],
                "env": {
                    "DECKHAND_MCP_URL": "${user_config.endpoint}",
                    "DECKHAND_MCP_TOKEN": "${user_config.token}",
                },
            },
        },
        "user_config": {
            "endpoint": {
                "type": "string",
                "title": "Deckhand MCP endpoint",
                "description": "The URL shown in Anki under Deckhand, then Management, then Connect.",
                "default": endpoint,
                "required": True,
            },
            "token": {
                "type": "string",
                "title": "Access token",
                "description": "Only needed when 'Require access token' is enabled on Deckhand's Server tab.",
                "default": token or "",
                "sensitive": True,
                "required": False,
            },
        },
        "compatibility": {"platforms": ["darwin", "win32", "linux"]},
    }


def build_bundle(dest: Path | None = None, endpoint: str | None = None, token: str | None = None) -> Path:
    proxy_source = assets_dir() / PROXY_FILENAME
    if not proxy_source.is_file():
        raise FileNotFoundError(f"mcpb proxy asset missing: {proxy_source}")
    target = dest or default_bundle_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest(endpoint=endpoint, token=token), indent=2, sort_keys=True) + "\n"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", payload)
        archive.write(proxy_source, PROXY_FILENAME)
    return target
