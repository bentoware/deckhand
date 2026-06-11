#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ADDON = ROOT / "addon" / "deckhand"
DEFAULT_ADDONS_DIR = Path.home() / "Library" / "Application Support" / "Anki2" / "addons21"
DEFAULT_ANKI_APP = "/Applications/Anki.app"
SERVER_BINARY = "deckhand-server.exe" if platform.system().lower() == "windows" else "deckhand-server"
IGNORED_DIRS = {"__pycache__", ".mypy_cache", ".pytest_cache"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
PRESERVED_TOP_LEVEL_DIRS = {"bin", "skills"}


def is_ignored(path: Path) -> bool:
    return any(part in IGNORED_DIRS for part in path.parts) or path.suffix in IGNORED_SUFFIXES


def iter_source_files(source: Path) -> list[Path]:
    return sorted(path for path in source.rglob("*") if path.is_file() and not is_ignored(path.relative_to(source)))


def file_changed(source: Path, target: Path) -> bool:
    if not target.exists():
        return True
    source_stat = source.stat()
    target_stat = target.stat()
    return source_stat.st_size != target_stat.st_size or source_stat.st_mtime_ns != target_stat.st_mtime_ns


def sync_addon(source: Path, target: Path) -> dict[str, object]:
    copied: list[str] = []
    removed: list[str] = []
    target.mkdir(parents=True, exist_ok=True)
    expected = {path.relative_to(source) for path in iter_source_files(source)}

    for relative in expected:
        source_path = source / relative
        target_path = target / relative
        if file_changed(source_path, target_path):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            copied.append(str(relative))

    for target_path in sorted(target.rglob("*"), reverse=True):
        relative = target_path.relative_to(target)
        if relative.parts[:1] and relative.parts[0] in PRESERVED_TOP_LEVEL_DIRS:
            continue
        if is_ignored(relative):
            if target_path.is_file():
                target_path.unlink()
                removed.append(str(relative))
            elif target_path.is_dir():
                shutil.rmtree(target_path)
                removed.append(str(relative))
            continue
        if target_path.is_file() and relative not in expected:
            target_path.unlink()
            removed.append(str(relative))
        elif target_path.is_dir() and not any(target_path.iterdir()):
            target_path.rmdir()

    return {"copied": copied, "removed": removed, "pythonChanged": any(path.endswith(".py") for path in copied + removed)}


def sync_skills(target: Path) -> dict[str, str]:
    """Mirror the bundled skills into the synced add-on, like packaging does."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build

    sources = build.bundled_skill_sources()
    skills_target = target / "skills"
    if skills_target.exists():
        shutil.rmtree(skills_target)
    for name, source in sorted(sources.items()):
        shutil.copytree(
            source,
            skills_target / name,
            ignore=shutil.ignore_patterns(*IGNORED_DIRS, "*.pyc", "*.pyo"),
        )
    return {name: str(source) for name, source in sources.items()}


def generate_mcp_catalog(root: Path = ROOT) -> None:
    subprocess.run([sys.executable, str(root / "scripts" / "generate_mcp_catalog.py")], check=True)


def platform_tag(system: str | None = None, machine: str | None = None) -> str:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    system = {"darwin": "macos"}.get(system, system)
    return f"{system}-{machine}"


def install_companion_binary(binary: Path, target: Path) -> dict[str, object]:
    source = binary.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(str(source))
    destination = target / "bin" / platform_tag() / SERVER_BINARY
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    return {"source": str(source), "destination": str(destination), "bytes": destination.stat().st_size}


def restart_anki(anki_app: str) -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("--restart-anki is currently implemented for macOS only.")
    subprocess.run(
        ["osascript", "-e", 'tell application "Anki" to quit'],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        running = subprocess.run(
            ["pgrep", "-f", r"aqt.run\(\)"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if running.returncode != 0:
            break
        time.sleep(0.5)
    still_running = subprocess.run(
        ["pgrep", "-f", r"aqt.run\(\)"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if still_running.returncode == 0:
        subprocess.run(["pkill", "-f", r"aqt.run\(\)"], check=False)
        for _ in range(20):
            running = subprocess.run(
                ["pgrep", "-f", r"aqt.run\(\)"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if running.returncode != 0:
                break
            time.sleep(0.5)
    app_path = Path(anki_app).expanduser()
    if app_path.exists():
        subprocess.run(["open", str(app_path)], check=True)
    else:
        subprocess.run(["open", "-a", anki_app], check=True)


def print_summary(result: dict[str, object], target: Path, companion: dict[str, object] | None = None) -> None:
    copied = list(result.get("copied") or [])
    removed = list(result.get("removed") or [])
    print(f"Synced Deckhand add-on to {target}")
    print(f"  copied: {len(copied)}")
    print(f"  removed: {len(removed)}")
    if result.get("pythonChanged"):
        print("  python changed: restart Anki to reload imported modules.")
    elif copied or removed:
        print("  static change: reopen any active Deckhand windows if they are stale.")
    else:
        print("  no file changes.")
    if companion:
        print(f"  companion: {companion['destination']}")


def snapshot(source: Path) -> dict[str, tuple[int, int]]:
    state: dict[str, tuple[int, int]] = {}
    for path in iter_source_files(source):
        stat = path.stat()
        state[str(path.relative_to(source))] = (stat.st_size, stat.st_mtime_ns)
    return state


def watch(
    source: Path,
    target: Path,
    restart_on_python: bool,
    anki_app: str,
    interval: float,
    generate_catalog: bool,
    companion_binary: Path | None,
) -> None:
    previous = snapshot(source)
    print(f"Watching {source}; Ctrl-C to stop.")
    try:
        while True:
            time.sleep(interval)
            current = snapshot(source)
            if current == previous:
                continue
            if generate_catalog:
                generate_mcp_catalog()
            result = sync_addon(source, target)
            companion = install_companion_binary(companion_binary, target) if companion_binary else None
            print_summary(result, target, companion)
            if restart_on_python and result.get("pythonChanged"):
                restart_anki(anki_app)
            previous = current
    except KeyboardInterrupt:
        print("Stopped watch.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync the repo Deckhand add-on into Anki for local development.")
    parser.add_argument("--source", default=str(SOURCE_ADDON), help="Source add-on directory.")
    parser.add_argument("--addons-dir", default=os.environ.get("ANKI_ADDONS_DIR", str(DEFAULT_ADDONS_DIR)))
    parser.add_argument("--addon-name", default="deckhand")
    parser.add_argument("--restart-anki", action="store_true", help="Restart Anki after syncing.")
    parser.add_argument("--watch", action="store_true", help="Keep syncing when source files change.")
    parser.add_argument("--restart-on-python", action="store_true", help="In --watch mode, restart Anki after Python changes.")
    parser.add_argument("--skip-mcp-catalog", action="store_true", help="Skip regenerating the Rust MCP catalog projection before sync.")
    parser.add_argument("--companion-binary", type=Path, help="Copy this built deckhand-server binary into bin/<platform>/ after sync.")
    parser.add_argument("--anki-app", default=os.environ.get("ANKI_APP_PATH", DEFAULT_ANKI_APP))
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    target = Path(args.addons_dir).expanduser().resolve() / args.addon_name
    if not source.exists():
        raise SystemExit(f"source add-on not found: {source}")

    if not args.skip_mcp_catalog:
        generate_mcp_catalog()
    result = sync_addon(source, target)
    skills = sync_skills(target)
    print(f"  skills bundled: {len(skills)}")
    companion = install_companion_binary(args.companion_binary, target) if args.companion_binary else None
    print_summary(result, target, companion)
    if args.restart_anki:
        restart_anki(args.anki_app)
    if args.watch:
        watch(source, target, args.restart_on_python, args.anki_app, args.interval, not args.skip_mcp_catalog, args.companion_binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
