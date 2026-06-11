"""Keep installed Deckhand skills in sync with the deckhand-skills repo.

Skills are bundled with the add-on, but the deckhand-skills repository moves
faster than add-on releases. Once a day (or on demand from the Skills tab)
this module downloads the repo's main-branch tarball and refreshes installed
skills that we manage. The rules mirror the installer: a skill the user has
edited is never touched, and skills the user never installed are not added.
"""

from __future__ import annotations

import io
import os
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from . import settings
from . import skills
from .version import ADDON_VERSION

DEFAULT_SKILLS_REPO = "bentoware/deckhand-skills"
SKILLS_SYNC_INTERVAL_MS = 24 * 60 * 60 * 1000
_FETCH_TIMEOUT_SECONDS = 15.0
_MAX_TARBALL_BYTES = 20 * 1024 * 1024


def skills_repo() -> str:
    return os.environ.get("DECKHAND_SKILLS_REPO", DEFAULT_SKILLS_REPO)


def skills_repo_branch() -> str:
    return os.environ.get("DECKHAND_SKILLS_REPO_BRANCH", "main")


def skills_tarball_url() -> str:
    return f"https://codeload.github.com/{skills_repo()}/tar.gz/refs/heads/{skills_repo_branch()}"


def fetch_remote_skills(work_dir: Path, timeout: float = _FETCH_TIMEOUT_SECONDS) -> Path:
    """Download and extract the skills repo; return its ``skills/`` directory."""
    request = Request(  # noqa: S310 - fixed https GitHub host
        skills_tarball_url(),
        headers={"User-Agent": f"deckhand-addon/{ADDON_VERSION}"},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        data = response.read(_MAX_TARBALL_BYTES + 1)
    if len(data) > _MAX_TARBALL_BYTES:
        raise ValueError("skills tarball exceeds size limit")
    return extract_skills_dir(io.BytesIO(data), work_dir)


def extract_skills_dir(stream: Any, work_dir: Path) -> Path:
    with tarfile.open(fileobj=stream, mode="r:gz") as archive:
        try:
            archive.extractall(work_dir, filter="data")
        except TypeError:  # pragma: no cover - Python < 3.12 without filters
            archive.extractall(work_dir)  # noqa: S202 - trusted fixed-repo tarball
    for child in sorted(work_dir.iterdir()):
        candidate = child / "skills" if child.is_dir() else None
        if candidate and candidate.is_dir():
            return candidate
    raise FileNotFoundError("skills directory not found in downloaded archive")


def sync_installed_skills(remote_skills_dir: Path, target_roots: list[Path] | None = None) -> list[dict[str, Any]]:
    """Refresh managed installs from the remote checkout. Never adds or edits user skills."""
    roots = target_roots if target_roots is not None else skills.managed_install_roots()
    outcomes: list[dict[str, Any]] = []
    for root in roots:
        for manifest in sorted(root.glob(f"*/{skills.MANIFEST_FILENAME}")):
            name = manifest.parent.name
            remote_skill = remote_skills_dir / name
            if not (remote_skill / skills.SKILL_FILENAME).is_file():
                continue
            outcome = skills.install_skill(remote_skill, root)
            outcome["targetRoot"] = str(root)
            outcomes.append(outcome)
    return outcomes


def check_and_sync(*, force: bool = False) -> dict[str, Any]:
    if not force and not settings.skills_auto_update_enabled():
        return {"checked": False, "reason": "disabled"}
    now_ms = int(time.time() * 1000)
    if not force and now_ms - settings.last_skills_sync_ms() < SKILLS_SYNC_INTERVAL_MS:
        return {"checked": False, "reason": "throttled"}
    roots = skills.managed_install_roots()
    if not roots:
        return {"checked": False, "reason": "no_managed_installs"}
    settings.set_last_skills_sync_ms(now_ms)
    try:
        with tempfile.TemporaryDirectory(prefix="deckhand-skills-") as temp_dir:
            remote = fetch_remote_skills(Path(temp_dir))
            outcomes = sync_installed_skills(remote, roots)
    except Exception as exc:  # noqa: BLE001 - offline syncs must stay quiet
        return {"checked": True, "error": str(exc), "outcomes": []}
    return {
        "checked": True,
        "outcomes": outcomes,
        "updated": sum(1 for outcome in outcomes if outcome["status"] == skills.STATUS_UPDATED),
        "skipped": sum(1 for outcome in outcomes if outcome["status"].startswith("skipped")),
    }


def start_background_sync(mw: Any, logger=None) -> None:
    def worker() -> None:
        result = check_and_sync()
        if logger:
            logger("skills_updates.sync_completed", result=_compact(result))
        if not result.get("updated"):
            return
        try:
            mw.taskman.run_on_main(lambda: _notify_updated(mw, int(result["updated"])))
        except Exception as exc:  # noqa: BLE001 - never break startup over a skills refresh
            if logger:
                logger("skills_updates.notify_failed", error=str(exc))

    threading.Thread(target=worker, name="deckhand-skills-sync", daemon=True).start()


def _notify_updated(mw: Any, count: int) -> None:
    try:
        from aqt.utils import tooltip

        plural = "skill" if count == 1 else "skills"
        tooltip(f"Deckhand updated {count} installed {plural}.", parent=mw)
    except Exception:  # pragma: no cover - only meaningful inside Anki/Qt
        pass


def _compact(result: dict[str, Any]) -> dict[str, Any]:
    compact = {key: value for key, value in result.items() if key != "outcomes"}
    if result.get("outcomes"):
        compact["outcomes"] = {outcome["skill"]: outcome["status"] for outcome in result["outcomes"]}
    return compact
