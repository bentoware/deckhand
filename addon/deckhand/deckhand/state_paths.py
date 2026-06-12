from __future__ import annotations

import os
import platform
from pathlib import Path


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


def default_runtime_root(home: Path | None = None, system: str | None = None) -> Path:
    home = home or Path.home()
    system = (system or platform.system()).lower()
    if system == "windows":
        # Runtime files (copied executables, pid files, logs) are machine-local;
        # keep them out of the roaming profile so they never sync across machines.
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else home / "AppData" / "Local"
        return base / "Deckhand" / "state"
    return default_state_root(home=home, system=system)


def state_root() -> Path:
    configured = os.environ.get("DECKHAND_ANKI_EXTENSION_STATE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return default_state_root()


def runtime_root() -> Path:
    configured = os.environ.get("DECKHAND_ANKI_EXTENSION_STATE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return default_runtime_root()


def work_root() -> Path:
    return state_root()
