from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path("/Users/thoffman/github.com/bentoware/deckhand")
DEFAULT_STATE_ROOT = Path("/Users/thoffman/github.com/bentoware/deckhand-state")


def state_root() -> Path:
    configured = os.environ.get("DECKHAND_ANKI_EXTENSION_STATE_ROOT")
    return Path(configured or DEFAULT_STATE_ROOT).expanduser().resolve()


def work_root() -> Path:
    return state_root()
