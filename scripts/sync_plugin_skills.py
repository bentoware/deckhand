#!/usr/bin/env python3
"""Mirror repo-bundled skills from skills/ into the Claude Code plugin.

plugins/deckhand/skills/ is a derived copy of skills/, with two plugin-only
deltas this script owns: each plugin skill may keep a top-level assets/
directory (marketplace icons, never shipped in the .ankiaddon), and
agents/openai.yaml gains icon_small/icon_large lines pointing at it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "skills"
PLUGIN_SKILLS_ROOT = ROOT / "plugins" / "deckhand" / "skills"
PRESERVED_TOP_LEVEL = {"assets"}
OPENAI_INTERFACE = Path("agents/openai.yaml")
ICON_LINES = [
    '  icon_small: "./assets/icon.png"',
    '  icon_large: "./assets/logo.png"',
]


def plugin_openai_yaml(source_text: str) -> str:
    """Return the plugin variant: source text plus icon lines after short_description."""
    if "icon_small:" in source_text:
        return source_text
    lines = source_text.splitlines()
    for index, line in enumerate(lines):
        if line.lstrip().startswith("short_description:"):
            lines[index + 1 : index + 1] = ICON_LINES
            return "\n".join(lines) + "\n"
    return source_text


def expected_files(skill_dir: Path) -> dict[Path, bytes]:
    expected: dict[Path, bytes] = {}
    for file in sorted(skill_dir.rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(skill_dir)
        if relative.parts[0] in PRESERVED_TOP_LEVEL:
            continue
        if relative == OPENAI_INTERFACE:
            expected[relative] = plugin_openai_yaml(file.read_text(encoding="utf-8")).encode("utf-8")
        else:
            expected[relative] = file.read_bytes()
    return expected


def actual_files(dest_dir: Path) -> dict[Path, bytes]:
    actual: dict[Path, bytes] = {}
    if not dest_dir.is_dir():
        return actual
    for file in sorted(dest_dir.rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(dest_dir)
        if relative.parts[0] in PRESERVED_TOP_LEVEL:
            continue
        actual[relative] = file.read_bytes()
    return actual


def remove_empty_dirs(dest_dir: Path) -> None:
    for directory in sorted((p for p in dest_dir.rglob("*") if p.is_dir()), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()


def sync_skills(check: bool) -> list[str]:
    drift: list[str] = []
    for skill_dir in sorted(SOURCE_ROOT.iterdir()):
        if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
            continue
        dest_dir = PLUGIN_SKILLS_ROOT / skill_dir.name
        expected = expected_files(skill_dir)
        actual = actual_files(dest_dir)
        for relative, content in expected.items():
            if actual.get(relative) != content:
                drift.append(f"stale or missing: {dest_dir / relative}")
                if not check:
                    target = dest_dir / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
        for relative in actual:
            if relative not in expected:
                drift.append(f"orphaned: {dest_dir / relative}")
                if not check:
                    (dest_dir / relative).unlink()
        if not check and dest_dir.is_dir():
            remove_empty_dirs(dest_dir)
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the plugin copy is current without writing.")
    args = parser.parse_args(argv)
    drift = sync_skills(check=args.check)
    if args.check:
        if drift:
            for line in drift:
                print(line, file=sys.stderr)
            print("plugin skills are out of sync; run scripts/build.py generate", file=sys.stderr)
            return 1
        print("plugin skills are in sync")
        return 0
    for line in drift:
        print(f"synced {line}")
    if not drift:
        print("plugin skills already in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
