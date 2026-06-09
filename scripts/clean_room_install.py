#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
ANKI_ADDON = HOME / "Library" / "Application Support" / "Anki2" / "addons21" / "deckhand"
DECKHAND_SUPPORT = HOME / "Library" / "Application Support" / "Deckhand"
DECKHAND_LOGS = HOME / "Library" / "Logs" / "Deckhand"
BACKUP_ROOT = HOME / "Library" / "Application Support" / "DeckhandCleanRoomBackups"

PROCESS_PATTERNS = [
    "deckhand-server",
]


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, check=check)


def timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def backup_path(path: Path, backup_dir: Path) -> Path:
    if path.is_absolute():
        relative = Path(*path.parts[1:])
    else:
        relative = path
    return backup_dir / relative


def move_to_backup(path: Path, backup_dir: Path, *, apply: bool) -> None:
    destination = backup_path(path, backup_dir)
    if not path.exists():
        print(f"missing: {path}")
        return
    print(f"backup: {path} -> {destination}")
    if not apply:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RuntimeError(f"backup destination already exists: {destination}")
    shutil.move(str(path), str(destination))


def matching_pids(patterns: list[str]) -> list[tuple[int, str]]:
    result = subprocess.run(["ps", "-axo", "pid=,command="], text=True, stdout=subprocess.PIPE, check=True)
    matches: list[tuple[int, str]] = []
    own_pid = os.getpid()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == own_pid:
            continue
        if any(pattern in command for pattern in patterns):
            matches.append((pid, command))
    return matches


def stop_processes(*, apply: bool) -> None:
    matches = matching_pids(PROCESS_PATTERNS)
    if not matches:
        print("processes: none matched")
        return
    for pid, command in matches:
        print(f"process: {pid} {command}")
    if not apply:
        return
    for pid, _command in matches:
        subprocess.run(["kill", str(pid)], check=False)
    time.sleep(1)
    remaining = matching_pids(PROCESS_PATTERNS)
    for pid, command in remaining:
        print(f"process still running after TERM: {pid} {command}")


def sync_addon(*, restart_anki: bool) -> None:
    command = [sys.executable, "scripts/build.py", "sync"]
    passthrough = []
    if restart_anki:
        passthrough.append("--restart-anki")
    if passthrough:
        command.extend(["--", *passthrough])
    run(command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset Deckhand-owned local dev state and reinstall the add-on from this checkout."
    )
    parser.add_argument("--apply", action="store_true", help="Actually move files and stop processes. Defaults to dry-run.")
    parser.add_argument("--no-sync", action="store_true", help="Only clean state; do not run scripts/build.py sync.")
    parser.add_argument("--restart-anki", action="store_true", help="Restart Anki after syncing.")
    args = parser.parse_args(argv)

    backup_dir = BACKUP_ROOT / timestamp()
    print("Deckhand clean-room install")
    print(f"mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"backup dir: {backup_dir}")
    print()

    stop_processes(apply=args.apply)
    print()

    for path in [ANKI_ADDON, DECKHAND_SUPPORT, DECKHAND_LOGS]:
        move_to_backup(path, backup_dir, apply=args.apply)

    if not args.apply:
        print()
        print("Dry run only. Re-run with --apply to perform this reset.")
        return 0

    if args.no_sync:
        print()
        print("Clean-room state prepared. Sync skipped by --no-sync.")
        return 0

    print()
    sync_addon(restart_anki=args.restart_anki)
    print()
    print("Clean-room Deckhand install complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
