from __future__ import annotations

from pathlib import Path
import platform
import sys
from typing import Any

from .command_catalog import anki_sdk_reference_paths
from . import tts


def runtime_info(mw: Any) -> dict[str, Any]:
    collection = getattr(mw, "col", None)
    profile_manager = getattr(mw, "pm", None)
    add_on_manager = getattr(mw, "addonManager", None)
    anki_path, aqt_path = anki_sdk_reference_paths()
    media_dir = None
    if collection is not None:
        try:
            media_dir = collection.media.dir()
        except Exception:
            media_dir = None
    return {
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "anki": {
            "version": _anki_version(),
            "state": str(getattr(mw, "state", "unknown")),
            "profile": _call_or_attr(profile_manager, "name"),
            "profileBase": _call_or_attr(profile_manager, "base"),
            "collectionOpen": collection is not None,
            "schedulerVersion": _scheduler_version(collection),
            "mediaDir": media_dir,
            "addonsDir": _addons_dir(add_on_manager),
        },
        "qt": _qt_info(),
        "deckhand": {
            "addonDir": str(Path(__file__).resolve().parents[1]),
            "moduleDir": str(Path(__file__).resolve().parent),
            "ttsSurface": {
                "module": "deckhand.tts",
                "importExample": "import deckhand.tts as tts",
                "usage": "result = tts.render(provider='openai', text='hello', out='/tmp/hello.mp3')",
                "providers": tts.providers(),
                "schema": tts.schema(),
            },
        },
        "sdkPaths": {
            "anki": anki_path,
            "aqt": aqt_path,
            "exists": {
                "anki": Path(anki_path).exists(),
                "aqt": Path(aqt_path).exists(),
            },
        },
    }


def _anki_version() -> str | None:
    try:
        import anki

        return str(getattr(anki, "version", None) or getattr(anki, "__version__", None))
    except Exception:
        return None


def _qt_info() -> dict[str, str | None]:
    try:
        from aqt.qt import PYQT_VERSION_STR, QT_VERSION_STR

        return {"qtVersion": str(QT_VERSION_STR), "pyqtVersion": str(PYQT_VERSION_STR)}
    except Exception:
        return {"qtVersion": None, "pyqtVersion": None}


def _scheduler_version(collection: Any) -> int | None:
    try:
        return int(collection.sched.version)
    except Exception:
        return None


def _addons_dir(add_on_manager: Any) -> str | None:
    for name in ("addonsFolder", "addons_folder"):
        value = getattr(add_on_manager, name, None)
        try:
            if callable(value):
                return str(value())
            if value:
                return str(value)
        except Exception:
            pass
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
