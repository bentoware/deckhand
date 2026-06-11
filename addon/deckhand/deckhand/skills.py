"""Discover bundled Deckhand skills and install them for agent clients.

Both packaged builds and dev syncs carry skills at
``<addon>/skills/<name>/SKILL.md``; ``DECKHAND_BUNDLED_SKILLS_DIRS``
overrides discovery for tests and unusual setups.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

SKILL_FILENAME = "SKILL.md"
MANIFEST_FILENAME = ".deckhand-skill.json"

STATUS_INSTALLED = "installed"
STATUS_UPDATED = "updated"
STATUS_UP_TO_DATE = "up_to_date"
STATUS_SKIPPED_MODIFIED = "skipped_modified"
STATUS_SKIPPED_UNMANAGED = "skipped_unmanaged"


def bundled_skill_roots() -> list[Path]:
    configured = os.environ.get("DECKHAND_BUNDLED_SKILLS_DIRS")
    if configured:
        return [Path(part).expanduser() for part in configured.split(":") if part]
    package_root = Path(__file__).resolve().parents[1]
    candidates = [package_root / "skills"]
    return [root for root in candidates if root.is_dir()]


def bundled_skill_dirs() -> list[Path]:
    seen: dict[str, Path] = {}
    for root in bundled_skill_roots():
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / SKILL_FILENAME).is_file() and child.name not in seen:
                seen[child.name] = child
    return [seen[name] for name in sorted(seen)]


def bundled_skills() -> list[dict[str, Any]]:
    return [
        {
            "name": path.name,
            "path": str(path),
            "description": skill_description(path),
        }
        for path in bundled_skill_dirs()
    ]


def skill_description(skill_dir: Path) -> str:
    try:
        text = (skill_dir / SKILL_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return ""
    in_frontmatter = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
            continue
        if in_frontmatter and stripped.startswith("description:"):
            return stripped[len("description:") :].strip().strip('"')
    return ""


def claude_code_skills_root() -> Path:
    configured = os.environ.get("DECKHAND_CLAUDE_SKILLS_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude" / "skills"


def codex_skills_root() -> Path:
    configured = os.environ.get("DECKHAND_CODEX_SKILLS_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex" / "skills"


def install_targets() -> list[dict[str, Any]]:
    return [
        {"id": "claude_code", "label": "Claude Code", "root": claude_code_skills_root()},
        {"id": "codex", "label": "Codex", "root": codex_skills_root()},
    ]


def managed_install_roots() -> list[Path]:
    """Target roots that contain at least one skill we installed."""
    roots = []
    for target in install_targets():
        root = target["root"]
        if root.is_dir() and any(root.glob(f"*/{MANIFEST_FILENAME}")):
            roots.append(root)
    return roots


def install_skill(skill_dir: Path, target_root: Path, *, force: bool = False) -> dict[str, Any]:
    target = target_root / skill_dir.name
    digest = directory_digest(skill_dir)
    status = STATUS_INSTALLED
    if target.exists():
        manifest = _read_manifest(target)
        if manifest is None:
            if not force:
                return _result(skill_dir, target, STATUS_SKIPPED_UNMANAGED, digest)
            status = STATUS_UPDATED
        else:
            installed_digest = directory_digest(target)
            if installed_digest == digest:
                return _result(skill_dir, target, STATUS_UP_TO_DATE, digest)
            if installed_digest != manifest.get("digest") and not force:
                # The files on disk no longer match what we installed: the
                # user edited this skill, so never silently overwrite it.
                return _result(skill_dir, target, STATUS_SKIPPED_MODIFIED, digest)
            status = STATUS_UPDATED
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_dir, target, ignore=shutil.ignore_patterns(MANIFEST_FILENAME, "__pycache__"))
    _write_manifest(target, digest)
    return _result(skill_dir, target, status, digest)


def install_all(target_root: Path | None = None, *, force: bool = False) -> list[dict[str, Any]]:
    root = target_root or claude_code_skills_root()
    return [install_skill(path, root, force=force) for path in bundled_skill_dirs()]


def directory_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(path.rglob("*")):
        if not file.is_file() or file.name == MANIFEST_FILENAME or "__pycache__" in file.parts:
            continue
        digest.update(file.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(file.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def _read_manifest(target: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((target / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_manifest(target: Path, digest: str) -> None:
    payload = {
        "installedBy": "deckhand-addon",
        "digest": digest,
        "installedAtMs": int(time.time() * 1000),
    }
    (target / MANIFEST_FILENAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _result(skill_dir: Path, target: Path, status: str, digest: str) -> dict[str, Any]:
    return {
        "skill": skill_dir.name,
        "status": status,
        "path": str(target),
        "digest": digest,
    }
