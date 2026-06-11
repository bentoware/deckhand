from __future__ import annotations

import os
import platform
from pathlib import Path

# Dev-checkout paths. On end-user machines these don't exist and every
# consumer guards with is_dir()/fallbacks; keep them so a dev sync keeps
# using the repo-sibling state instead of the per-user app-data dir.
PROJECT_ROOT = Path("/Users/thoffman/github.com/bentoware/deckhand")
LEGACY_DEV_STATE_ROOT = Path("/Users/thoffman/github.com/bentoware/deckhand-state")


def default_state_root(home: Path | None = None, system: str | None = None) -> Path:
    home = home or Path.home()
    system = (system or platform.system()).lower()
    if system == "darwin":
        return home / "Library" / "Application Support" / "Deckhand" / "state"
    if system == "windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "Deckhand" / "state"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else home / ".local" / "share"
    return base / "deckhand" / "state"


def state_root() -> Path:
    configured = os.environ.get("DECKHAND_ANKI_EXTENSION_STATE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if LEGACY_DEV_STATE_ROOT.is_dir():
        return LEGACY_DEV_STATE_ROOT
    return default_state_root()


def work_root() -> Path:
    return state_root()
