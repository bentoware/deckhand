"""Check GitHub releases for a newer Deckhand add-on.

Sideloaded .ankiaddon installs get no updates from Anki itself (only AnkiWeb
installs do), so this module polls the GitHub releases API at most once per
day and lets the UI prompt the user. It never installs anything: the bundled
companion binary cannot be replaced while it is running, so updating is
always download -> install -> restart Anki, driven by the user.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from . import settings
from .version import ADDON_VERSION

DEFAULT_REPO = "bentoware/deckhand"
UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000
_FETCH_TIMEOUT_SECONDS = 5.0


def is_ankiweb_install(package_root: Path | None = None) -> bool:
    """AnkiWeb installs live in a folder named by their numeric AnkiWeb ID.

    Anki's own add-on manager handles update checks and prompts for those, so
    the GitHub release check only applies to sideloaded (.ankiaddon) installs.
    """
    root = package_root or Path(__file__).resolve().parents[1]
    return root.name.isdigit()


def update_repo() -> str:
    return os.environ.get("DECKHAND_UPDATE_REPO", DEFAULT_REPO)


def releases_page_url() -> str:
    return f"https://github.com/{update_repo()}/releases"


def latest_release_api_url() -> str:
    return f"https://api.github.com/repos/{update_repo()}/releases/latest"


def parse_version(value: str) -> tuple[int, ...]:
    parts = []
    for part in str(value).lstrip("vV").split("."):
        match = re.match(r"\d+", part)
        parts.append(int(match.group()) if match else 0)
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str = ADDON_VERSION) -> bool:
    return parse_version(candidate) > parse_version(current)


def fetch_latest_release(timeout: float = _FETCH_TIMEOUT_SECONDS) -> dict[str, Any]:
    request = Request(  # noqa: S310 - fixed https GitHub API host
        latest_release_api_url(),
        headers={"User-Agent": f"deckhand-addon/{ADDON_VERSION}", "Accept": "application/vnd.github+json"},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    return {
        "tag": str(payload.get("tag_name") or ""),
        "url": str(payload.get("html_url") or releases_page_url()),
        "name": str(payload.get("name") or ""),
    }


def check_for_update(*, force: bool = False) -> dict[str, Any]:
    if not force and not settings.update_check_enabled():
        return {"checked": False, "reason": "disabled", "currentVersion": ADDON_VERSION}
    now_ms = int(time.time() * 1000)
    if not force and now_ms - settings.last_update_check_ms() < UPDATE_CHECK_INTERVAL_MS:
        return {"checked": False, "reason": "throttled", "currentVersion": ADDON_VERSION}
    settings.set_last_update_check_ms(now_ms)
    try:
        release = fetch_latest_release()
    except Exception as exc:  # noqa: BLE001 - offline/ratelimited checks must stay quiet
        return {"checked": True, "updateAvailable": False, "error": str(exc), "currentVersion": ADDON_VERSION}
    latest = release["tag"].lstrip("vV") or ""
    return {
        "checked": True,
        "updateAvailable": bool(latest) and is_newer(latest),
        "currentVersion": ADDON_VERSION,
        "latestVersion": latest,
        "url": release["url"],
    }


def start_background_check(mw: Any, logger=None) -> None:
    """Run the throttled check off the main thread; prompt only when newer."""
    if is_ankiweb_install():
        if logger:
            logger("updates.check_skipped", reason="ankiweb_install")
        return

    def worker() -> None:
        result = check_for_update()
        if logger:
            logger("updates.check_completed", result=result)
        if not result.get("updateAvailable"):
            return
        latest = str(result.get("latestVersion") or "")
        if latest and latest == settings.skipped_update_version():
            return
        try:
            mw.taskman.run_on_main(lambda: prompt_for_update(mw, result, logger=logger))
        except Exception as exc:  # noqa: BLE001 - never break startup over an update nudge
            if logger:
                logger("updates.prompt_failed", error=str(exc))

    threading.Thread(target=worker, name="deckhand-update-check", daemon=True).start()


def prompt_for_update(mw: Any, result: dict[str, Any], logger=None) -> None:
    try:
        from aqt.qt import QDesktopServices, QMessageBox, QUrl
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki/Qt
        if logger:
            logger("updates.prompt_unavailable", error=str(exc))
        return

    latest = str(result.get("latestVersion") or "")
    box = QMessageBox(mw)
    box.setWindowTitle("Deckhand update available")
    box.setText(f"Deckhand {latest} is available (you have {ADDON_VERSION}).")
    box.setInformativeText("Download the new version, install it from Anki's add-on screen, then restart Anki.")
    download = box.addButton("Open download page", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Remind me later", QMessageBox.ButtonRole.RejectRole)
    skip = box.addButton("Skip this version", QMessageBox.ButtonRole.DestructiveRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked is download:
        QDesktopServices.openUrl(QUrl(str(result.get("url") or releases_page_url())))
        if logger:
            logger("updates.download_page_opened", version=latest)
    elif clicked is skip and latest:
        settings.set_skipped_update_version(latest)
        if logger:
            logger("updates.version_skipped", version=latest)
